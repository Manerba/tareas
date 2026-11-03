"""Begrenzte Textausschnitte und Dateisuche ohne Dateiinhalt im Modellkontext."""

import codecs
import io
import re
import time
from collections import deque
from contextlib import closing

from dashboard.errors import ApplicationError

MAX_SCAN_BYTES = 64 * 1024 * 1024
MAX_SCAN_SECONDS = 30
DEFAULT_READ_CHARS = 12000
MAX_READ_CHARS = 32000
MAX_SEARCH_CHARS = 16000
MAX_SNIPPET_CHARS = 640


def _integer(value, field, minimum, maximum, *, code="invalid_argument"):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ApplicationError(400, f"{field} muss eine ganze Zahl zwischen {minimum} und {maximum} sein",
                               code=code, field=field)


def _unsupported():
    return ApplicationError(415, "Textzugriff braucht UTF-8 oder UTF-16 mit BOM, keine Binaer-/PDF-/Office-Datei",
                            code="unsupported_text_file", field="path")


class _TextSource:
    """Dekodiert blockweise; Zeilenenden werden fuer stabile Positionen zu LF."""

    def __init__(self, storage, path, *, utf8_only=False):
        self.storage, self.path = storage, path
        self.utf8_only = utf8_only
        self.encoding = "utf-8"
        self.bytes_read = 0
        self.started = time.monotonic()

    def _check(self, chunk):
        self.bytes_read += len(chunk)
        if self.bytes_read > MAX_SCAN_BYTES:
            raise ApplicationError(413, "Textzugriff liest maximal 64 MiB je Aufruf einschliesslich uebersprungener Bereiche",
                                   code="file_scan_limit", field="path")
        if time.monotonic() - self.started > MAX_SCAN_SECONDS:
            raise ApplicationError(408, "Zeitlimit beim Durchsuchen der Datei erreicht",
                                   code="file_scan_timeout", field="path")

    def _chunks(self):
        with closing(self.storage.iter_file(self.path)) as chunks:
            prefix = b""
            while len(prefix) < 4:
                chunk = next(chunks, b"")
                if not chunk:
                    break
                self._check(chunk)
                prefix += chunk
            if prefix.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE, b"%PDF", b"PK\x03\x04")):
                raise _unsupported()
            bom = b""
            for marker, encoding in ((codecs.BOM_UTF8, "utf-8"), (codecs.BOM_UTF16_LE, "utf-16-le"),
                                     (codecs.BOM_UTF16_BE, "utf-16-be")):
                if prefix.startswith(marker):
                    bom, self.encoding = marker, encoding
                    break
            if self.utf8_only and self.encoding != "utf-8":
                raise ApplicationError(415, "Datei ist nicht UTF-8. Fuer UTF-16 mit BOM encoding=auto verwenden",
                                       code="invalid_encoding", field="encoding")
            decoder = io.IncrementalNewlineDecoder(codecs.getincrementaldecoder(self.encoding)(errors="strict"), True)

            def decode(data, final=False):
                try:
                    text = decoder.decode(data, final=final)
                except UnicodeError as exc:
                    raise _unsupported() from exc
                if re.search(r"[\x00-\x08\x0b\x0e-\x1f]", text):
                    raise _unsupported()
                return text

            yield decode(prefix[len(bom):])
            for chunk in chunks:
                self._check(chunk)
                yield decode(chunk)
            yield decode(b"", final=True)

    def segments(self):
        """(Zeile, Spalte, Fragment, Zeilenende), auch bei sehr langen Zeilen."""
        line, column = 1, 1
        with closing(self._chunks()) as chunks:
            for chunk in chunks:
                for match in re.finditer(r"[^\n]*\n|[^\n]+$", chunk):
                    text = match.group()
                    ended = text.endswith("\n")
                    yield line, column, text, ended
                    if ended:
                        line, column = line + 1, 1
                    else:
                        column += len(text)


def read_text_range(storage, path, *, start_line=1, line_count=200, start_column=1,
                    max_chars=DEFAULT_READ_CHARS, utf8_only=False):
    for value, field, maximum in ((start_line, "start_line", 10**9), (line_count, "line_count", 1000),
                                   (start_column, "start_column", 10**9), (max_chars, "max_chars", MAX_READ_CHARS)):
        _integer(value, field, 1, maximum)
    source = _TextSource(storage, path, utf8_only=utf8_only)
    parts, length, end_line = [], 0, None
    start_line_end = None
    next_line = next_column = None
    with closing(source.segments()) as segments:
        for line, column, text, ended in segments:
            if line < start_line:
                continue
            if line == start_line:
                start_line_end = column + len(text)
            if line == start_line and column < start_column:
                skip = start_column - column
                if skip >= len(text):
                    if ended:
                        raise ApplicationError(400, "start_column liegt hinter dem Zeilenende",
                                               code="invalid_argument", field="start_column")
                    continue
                text, column = text[skip:], start_column
            if line >= start_line + line_count or length == max_chars:
                next_line, next_column = line, column
                break
            take = min(len(text), max_chars - length)
            parts.append(text[:take])
            length += take
            end_line = line
            if take < len(text):
                next_line, next_column = line, column + take
                break
    if start_line_end is not None and start_column > start_line_end:
        raise ApplicationError(400, "start_column liegt hinter dem Zeilenende",
                               code="invalid_argument", field="start_column")
    return {"content": "".join(parts), "encoding": "utf-8", "source_encoding": source.encoding,
            "start_line": start_line, "start_column": start_column, "end_line": end_line,
            "returned_chars": length, "next_line": next_line, "next_column": next_column,
            "eof": next_line is None}


def _line_summaries(source, pattern, query_length):
    """Ein Treffer je Zeile, begrenzte Vorschau auch bei minutenlangen Logzeilen."""
    prefix, tail, excerpt = "", "", ""
    length, excerpt_start, match_column, match_end = 0, 0, None, None
    active_line = None

    def summary():
        text = prefix if match_column is None else excerpt
        start = 0 if match_column is None else excerpt_start
        return {"line": active_line, "text": text, "start_column": start + 1,
                "truncated_start": start > 0, "truncated_end": start + len(text) < length,
                "match_column": match_column, "match_end_column": match_end}

    with closing(source.segments()) as segments:
        for line, _, fragment, ended in segments:
            active_line = line
            body = fragment[:-1] if ended else fragment
            prefix += body[:max(0, MAX_SNIPPET_CHARS - len(prefix))]
            if match_column is None:
                window = tail + body
                match = pattern.search(window)
                if match:
                    start = max(0, match.start() - 160)
                    excerpt_start = length - len(tail) + start
                    match_column = length - len(tail) + match.start() + 1
                    match_end = length - len(tail) + match.end() + 1
                    excerpt = window[start:start + MAX_SNIPPET_CHARS]
                tail = window[-(query_length + 160):]
            else:
                excerpt += body[:max(0, MAX_SNIPPET_CHARS - len(excerpt))]
            length += len(body)
            if ended:
                yield summary()
                prefix, tail, excerpt = "", "", ""
                length, excerpt_start, match_column, match_end = 0, 0, None, None
                active_line = None
        if active_line is not None:
            yield summary()


def search_text_file(storage, path, query, *, start_line=1, limit=20, context_lines=2, case_sensitive=False):
    if not isinstance(query, str) or not 1 <= len(query) <= 256 or "\n" in query or "\r" in query:
        raise ApplicationError(400, "query braucht 1 bis 256 Zeichen ohne Zeilenumbruch",
                               code="invalid_argument", field="query")
    _integer(start_line, "start_line", 1, 10**9)
    _integer(limit, "limit", 1, 50, code="invalid_pagination")
    _integer(context_lines, "context_lines", 0, 5)
    if type(case_sensitive) is not bool:
        raise ApplicationError(400, "case_sensitive muss ein Wahrheitswert sein",
                               code="invalid_argument", field="case_sensitive")
    source = _TextSource(storage, path)
    pattern = re.compile(re.escape(query), 0 if case_sensitive else re.IGNORECASE)
    matches, chars = [], 0
    history = deque(maxlen=context_lines)
    ahead = deque()
    next_line = None
    with closing(_line_summaries(source, pattern, len(query))) as lines:
        # Vor dem Start sind nur die unmittelbar vorherigen Kontextzeilen relevant.
        for line in lines:
            if line["line"] >= start_line:
                ahead.append(line)
                break
            history.append(line)
        while ahead:
            while len(ahead) <= context_lines:
                line = next(lines, None)
                if line is None:
                    break
                ahead.append(line)
            current = ahead.popleft()
            if current["match_column"] is not None:
                excerpt = [*history, current, *ahead]
                cost = sum(len(line["text"]) for line in excerpt)
                if chars + cost > MAX_SEARCH_CHARS:
                    next_line = current["line"]
                    break
                matches.append({"line": current["line"], "column": current["match_column"],
                                "end_column": current["match_end_column"],
                                "context": [{key: line[key] for key in
                                             ("line", "text", "start_column", "truncated_start", "truncated_end")}
                                            for line in excerpt]})
                chars += cost
                if len(matches) == limit:
                    next_line = current["line"] + 1
                    break
            history.append(current)
            if not ahead:
                line = next(lines, None)
                if line is not None:
                    ahead.append(line)
    return {"matches": matches, "source_encoding": source.encoding, "returned_chars": chars,
            "next_line": next_line, "complete": next_line is None}
