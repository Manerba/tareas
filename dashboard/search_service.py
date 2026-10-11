"""Permission-filtered FTS5 search shared by MCP and future search consumers.

The index uses one corpus and equal BM25 weights for every source type. Source
rows remain authoritative: metadata and permissions are joined at query time.
SQLite source timestamps without a timezone are UTC. Notes use updated_at;
tasks, subtasks and handoffs use created_at. Search is not a change feed.
"""

from datetime import datetime, timezone
import html
import re
import sqlite3
import unicodedata
import uuid

from dashboard.errors import ApplicationError
from dashboard.permissions import task_visibility_sql


SEARCH_TYPES = (
    "task", "sub_task", "task_note", "sub_task_note",
    "task_handoff", "sub_task_handoff",
)
_INDEX_VERSION = 1
# All identifiers/expressions are fixed schema constants, never request input.
_SOURCES = (
    ("tasks", "task", "name", "description", "id", "NULL", "created_by", "created_at"),
    ("sub_tasks", "sub_task", "name", "description", "project_id", "id", "created_by", "created_at"),
    ("task_notes", "task_note", "''", "content", "task_id", "NULL", "user_id", "updated_at"),
    ("sub_task_notes", "sub_task_note", "''", "content", "NULL", "sub_task_id", "user_id", "updated_at"),
    ("task_note_entries", "task_handoff", "''", "content", "task_id", "NULL", "user_id", "created_at"),
    ("sub_task_note_entries", "sub_task_handoff", "''", "content", "NULL", "sub_task_id", "user_id", "created_at"),
)


def _raise_if_fts_missing(exc: sqlite3.OperationalError) -> None:
    if "no such module" in str(exc).lower():
        raise RuntimeError(
            "Tareas search requires SQLite FTS5 (unicode61). Install a Python/SQLite "
            "build with ENABLE_FTS5, then restart; the search migration was rolled back."
        ) from exc


def rebuild_search_index(db: sqlite3.Connection) -> None:
    """Rebuild from current source rows atomically; preserves an outer transaction.

    Maintenance callers should use db_transaction(), then call this function.
    Source writes and rebuilds serialize through SQLite's normal writer lock.
    """
    db.execute("SAVEPOINT rebuild_search_index")
    try:
        db.execute("INSERT INTO search_fts(search_fts) VALUES ('rebuild')")
        db.execute("RELEASE SAVEPOINT rebuild_search_index")
    except Exception:
        db.execute("ROLLBACK TO SAVEPOINT rebuild_search_index")
        db.execute("RELEASE SAVEPOINT rebuild_search_index")
        raise


def initialize_search_index(db: sqlite3.Connection) -> None:
    """Install/upgrade under init_db's process lock and an IMMEDIATE transaction.

    Missing FTS5 is an explicit startup error; a LIKE fallback would silently
    change AND, phrase, prefix and ranking semantics. No loadable module needed.
    """
    db.execute("CREATE TABLE IF NOT EXISTS search_index_meta (version INTEGER NOT NULL)")
    version = db.execute("SELECT version FROM search_index_meta").fetchone()
    existing = {row[0] for row in db.execute(
        "SELECT name FROM sqlite_master WHERE name LIKE 'search_%'"
    )}
    expected = {"search_documents", "search_fts"} | {
        f"search_{table}_{event}" for table, *_ in _SOURCES for event in ("ai", "au", "ad")
    }
    if version and version[0] == _INDEX_VERSION and expected <= existing:
        # A later Python/SQLite upgrade may remove FTS5 even for an existing DB.
        try:
            db.execute("SELECT rowid FROM search_fts LIMIT 0")
        except sqlite3.OperationalError as exc:
            _raise_if_fts_missing(exc)
            raise
        return

    # The surrounding transaction rolls this back if schema creation/backfill
    # fails. Repairing a missing trigger always backfills writes it could miss.
    for table, *_ in _SOURCES:
        for event in ("ai", "au", "ad"):
            db.execute(f"DROP TRIGGER IF EXISTS search_{table}_{event}")
    db.execute("DROP TABLE IF EXISTS search_fts")
    db.execute("DROP VIEW IF EXISTS search_documents")
    selects = []
    for tag, (table, kind, title, body, project, subtask, author, date) in enumerate(_SOURCES, 1):
        def col(value):
            return value if value in ("NULL", "''") else f"s.{value}"
        # Subtask notes/handoffs get their current parent, including after moves.
        parent = "st.project_id" if project == "NULL" else col(project)
        join = " JOIN sub_tasks st ON st.id = s.sub_task_id" if project == "NULL" else ""
        selects.append(
            f"SELECT s.id * 8 + {tag} AS search_id, '{kind}' AS type, s.id AS id, "
            f"coalesce({col(title)}, '') AS title, coalesce({col(body)}, '') AS body, "
            f"{parent} AS project_id, {col(subtask)} AS subtask_id, "
            f"{col(author)} AS user_id, {col(date)} AS date FROM {table} s{join}"
        )
    db.execute("CREATE VIEW search_documents AS " + " UNION ALL ".join(selects))
    try:
        db.execute(
            "CREATE VIRTUAL TABLE search_fts USING fts5(title, body, "
            "content='search_documents', content_rowid='search_id', "
            "tokenize='unicode61 remove_diacritics 2', prefix='2 3 4')"
        )
    except sqlite3.OperationalError as exc:
        _raise_if_fts_missing(exc)
        raise
    for tag, (table, _, title, body, *_) in enumerate(_SOURCES, 1):
        def text_value(prefix, value):
            return "''" if value == "''" else f"coalesce({prefix}.{value}, '')"
        insert = (
            "INSERT INTO search_fts(rowid, title, body) "
            f"VALUES (new.id * 8 + {tag}, {text_value('new', title)}, {text_value('new', body)});"
        )
        delete = (
            "INSERT INTO search_fts(search_fts, rowid, title, body) "
            f"VALUES ('delete', old.id * 8 + {tag}, {text_value('old', title)}, {text_value('old', body)});"
        )
        indexed_columns = ", ".join(["id", body] + ([title] if title != "''" else []))
        for suffix, event, statements in (
            ("ai", "INSERT", insert), ("au", f"UPDATE OF {indexed_columns}", delete + insert), ("ad", "DELETE", delete),
        ):
            db.execute(f"CREATE TRIGGER search_{table}_{suffix} AFTER {event} ON {table} BEGIN {statements} END")
    rebuild_search_index(db)
    db.execute("DELETE FROM search_index_meta")
    db.execute("INSERT INTO search_index_meta(version) VALUES (?)", (_INDEX_VERSION,))


def _invalid_query(detail: str):
    return ApplicationError(400, detail, code="invalid_search_query", field="query")


def prepare_query(query: str) -> str | None:
    """Compile a small literal AND/phrase/prefix language into bound FTS syntax.

    Other punctuation separates ordinary terms; FTS control syntax is rejected.
    Uppercase AND/OR/NOT are literal words, never injected FTS operators.
    """
    if not isinstance(query, str) or len(query) > 1000:
        raise _invalid_query("Suchanfrage muss Text mit hoechstens 1000 Zeichen sein")
    if any(unicodedata.category(char) == "Cs" or (unicodedata.category(char) == "Cc" and not char.isspace()) for char in query):
        raise _invalid_query("Suchanfrage enthaelt ungueltige Steuerzeichen")
    query = unicodedata.normalize("NFC", query).strip()
    if not query:
        return None
    parts = []
    pos = 0
    while pos < len(query):
        if query[pos].isspace():
            pos += 1
            continue
        if query[pos] == '"':
            end = query.find('"', pos + 1)
            if end == -1:
                raise _invalid_query("Anfuehrungszeichen der Suchphrase sind nicht geschlossen")
            value = query[pos + 1:end]
            if not re.search(r"[^\W_]", value) or "*" in value:
                raise _invalid_query("Suchphrase braucht Woerter; Praefix * nur nach einem einzelnen Wort verwenden")
            if end + 1 < len(query) and not query[end + 1].isspace():
                raise _invalid_query("Suchphrasen durch Leerzeichen trennen")
            parts.append('"' + value + '"')
            pos = end + 1
        else:
            end = pos
            while end < len(query) and not query[end].isspace():
                end += 1
            value = query[pos:end]
            if any(char in value for char in '"():{}[]^\\'):
                raise _invalid_query("Suchsyntax ungueltig; Woerter, \"Phrasen\" oder wort* verwenden")
            prefix = value.endswith("*")
            stem = value[:-1] if prefix else value
            if "*" in stem or (prefix and (not stem or not stem[-1].isalnum())):
                raise _invalid_query("Praefix * nur unmittelbar nach einem Wort verwenden")
            words = re.findall(r"[^\W_]+", stem)
            if not words:
                raise _invalid_query("Suchanfrage enthaelt keine durchsuchbaren Woerter")
            parts.extend('"' + word + '"' for word in words)
            if prefix:
                parts[-1] += "*"
            pos = end
        if len(parts) > 32:
            raise _invalid_query("Suchanfrage darf hoechstens 32 Begriffe oder Phrasen enthalten")
    return " AND ".join(parts)


def _since_utc(value: str) -> str:
    try:
        if not isinstance(value, str) or not re.match(r"^\d{4}-\d{2}-\d{2}T", value):
            raise ValueError
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(timezone.utc).isoformat()
    except (ValueError, OverflowError):
        raise ApplicationError(
            400, "since muss ein ISO-8601-Zeitpunkt mit Zeitzone sein, z.B. 2026-10-11T12:00:00Z",
            code="invalid_search_since", field="since",
        ) from None


def _date_utc(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (ValueError, OverflowError):
        return None


def _snippets(raw: str, opening: str, closing: str) -> tuple[str, str]:
    """Bound visible text to 320 characters; only our escaped mark tags are HTML."""
    pieces = re.split(f"({re.escape(opening)}|{re.escape(closing)})", raw or "")
    spans = []
    highlighted = False
    offset = 0
    first_match = None
    for piece in pieces:
        if piece == opening:
            highlighted = True
        elif piece == closing:
            highlighted = False
        else:
            text = re.sub(r"\s+", " ", piece)
            text = "".join(c for c in text if unicodedata.category(c) != "Cc")
            if highlighted and first_match is None:
                first_match = offset
            spans.append((offset, text, highlighted))
            offset += len(text)
    start = max(0, (first_match or 0) - 90) if offset > 320 else 0
    end = min(offset, start + 320)
    plain = ["…" if start else ""]
    rendered = plain.copy()
    for position, value, marked in spans:
        value = value[max(0, start - position):max(0, end - position)]
        if not value:
            continue
        plain.append(value)
        escaped = html.escape(value)
        rendered.append(f"<mark>{escaped}</mark>" if marked else escaped)
    if end < offset:
        plain.append("…")
        rendered.append("…")
    return "".join(plain), "".join(rendered)


def search_content(
    db: sqlite3.Connection, user: dict, query: str, limit: int = 20, *,
    project_id: int | None = None, types: list[str] | None = None, since: str | None = None,
) -> list[dict]:
    """Search current visible rows before LIMIT; never return corpus scores.

    `types` accepts the six SEARCH_TYPES and `handoff` (both handoff kinds).
    `since` is inclusive ISO-8601 with an explicit timezone, normalized to UTC.
    Empty queries/empty type lists return []. Limits retain legacy 1..200 clamping.
    """
    prepared = prepare_query(query)
    if type(limit) is not int:
        raise ApplicationError(400, "limit muss ganzzahlig sein", code="invalid_search_limit", field="limit")
    limit = max(1, min(limit, 200))
    if project_id is not None and (type(project_id) is not int or project_id < 1):
        raise ApplicationError(400, "project_id muss positiv sein", code="invalid_project_id", field="project_id")
    selected = list(SEARCH_TYPES)
    if types is not None:
        if not isinstance(types, list) or any(not isinstance(v, str) or v not in (*SEARCH_TYPES, "handoff") for v in types):
            raise ApplicationError(400, "Unbekannter Suchtyp", code="invalid_search_types", field="types")
        selected = [kind for kind in SEARCH_TYPES if kind in types or ("handoff" in types and kind.endswith("_handoff"))]
    timestamp = _since_utc(since) if since is not None else None
    if not prepared or not selected:
        return []

    # Both task- and subtask-level contributions obey the exact current policy.
    task_visible, task_params = task_visibility_sql(user)
    subtask_visible, subtask_params = task_visibility_sql(user, subtask=True)
    conditions = [
        "search_fts MATCH ?", "d.type IN (" + ",".join("?" for _ in selected) + ")",
        f"((d.subtask_id IS NULL AND {task_visible}) OR (st.id IS NOT NULL AND {subtask_visible}))",
    ]
    params = [prepared, *selected, *task_params, *subtask_params]
    if project_id is not None:
        conditions.append("d.project_id = ?")
        params.append(project_id)
    if timestamp is not None:
        conditions.append("julianday(d.date) >= julianday(?)")
        params.append(timestamp)
    # Random per-query markers cannot be spoofed by stored content.
    marker = uuid.uuid4().hex
    opening, closing = f"<{marker}>", f"</{marker}>"
    try:
        rows = db.execute(
            "SELECT d.type, d.id, d.project_id, d.subtask_id, d.user_id, d.date, "
            "substr(d.title, 1, 160) AS name, substr(u.username, 1, 160) AS username, "
            "snippet(search_fts, -1, ?, ?, ' … ', 24) AS excerpt "
            "FROM search_fts JOIN search_documents d ON d.search_id = search_fts.rowid "
            "JOIN tasks t ON t.id = d.project_id "
            "LEFT JOIN sub_tasks st ON st.id = d.subtask_id AND st.project_id = t.id "
            "LEFT JOIN users u ON u.id = d.user_id WHERE " + " AND ".join(conditions) +
            " ORDER BY bm25(search_fts), julianday(d.date) DESC, d.search_id ASC LIMIT ?",
            [opening, closing, *params, limit],
        ).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower() or "no such module" in str(exc).lower():
            raise ApplicationError(
                503, "Suchindex nicht verfuegbar; Datenbankinitialisierung und SQLite FTS5 pruefen",
                code="search_unavailable",
            ) from exc
        raise
    results = []
    for row in rows:
        plain, rendered = _snippets(row["excerpt"], opening, closing)
        result = {
            "type": row["type"], "id": row["id"], "project_id": row["project_id"],
            "task_id": row["project_id"], "date": _date_utc(row["date"]),
            "snippet": plain, "snippet_html": rendered,
        }
        if row["type"] in ("task", "sub_task"):
            result["name"] = row["name"]
        if row["subtask_id"] is not None:
            result["subtask_id"] = row["subtask_id"]
        if row["user_id"] is not None:
            result.update(user_id=row["user_id"], user=row["username"])
        if row["type"].endswith("_handoff"):
            scope = "subtask" if row["subtask_id"] is not None else "task"
            result.update(handoff_id=f"{scope}:{row['id']}", local_id=row["id"])
        results.append(result)
    return results
