"""Native Anzeige/Bearbeitung kleiner Textdateien in Projektablagen."""

import codecs
import hashlib
import re
from threading import Lock

from fastapi import HTTPException

MAX_TEXT_BYTES = 2 * 1024 * 1024
MARKDOWN_EXTENSIONS = {"md", "markdown", "mdown", "mkd"}
TEXT_EXTENSIONS = {
    "txt", "text", "log", "csv", "tsv", "ps1", "psm1", "psd1", "sh", "bash", "zsh", "fish",
    "bat", "cmd", "py", "pyw", "js", "mjs", "cjs", "ts", "tsx", "jsx", "json", "jsonl",
    "yaml", "yml", "toml", "ini", "conf", "config", "cfg", "env", "xml", "html", "htm",
    "css", "scss", "sass", "less", "sql", "rb", "php", "pl", "pm", "lua", "r", "go", "rs",
    "c", "h", "cc", "cpp", "hpp", "cs", "java", "kt", "swift", "vue", "svelte", "tex",
    "rst", "properties", "service", "timer", "desktop", "gitignore", "gitattributes", "editorconfig",
}
TEXT_NAMES = {"dockerfile", "containerfile", "makefile", "cmakelists.txt", "readme", "license", "copying", ".bashrc", ".zshrc", ".profile"}
# Serialisiert konkurrierende native Speichervorgaenge im App-Prozess.
_write_locks = [Lock() for _ in range(64)]


def text_format(path: str) -> str | None:
    name = path.rsplit("/", 1)[-1].lower()
    extension = name.rsplit(".", 1)[-1] if "." in name else ""
    if extension in MARKDOWN_EXTENSIONS:
        return "markdown"
    if extension in TEXT_EXTENSIONS or name in TEXT_NAMES or name.startswith(".env."):
        return "text"
    return None


def _decode(content: bytes) -> tuple[str, str, bytes]:
    encoding, bom = "utf-8", b""
    if content.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        raise HTTPException(415, "textFile.unsupportedEncoding")
    for marker, codec in ((codecs.BOM_UTF8, "utf-8"), (codecs.BOM_UTF16_LE, "utf-16-le"), (codecs.BOM_UTF16_BE, "utf-16-be")):
        if content.startswith(marker):
            encoding, bom = codec, marker
            break
    try:
        text = content[len(bom):].decode(encoding)
    except UnicodeError:
        raise HTTPException(415, "textFile.unsupportedEncoding")
    _validate_text(text)
    return text, encoding, bom


def _validate_text(text: str):
    if any(ord(char) < 32 and char not in "\t\n\r\f" for char in text):
        raise HTTPException(415, "textFile.binary")


def _revision(storage, content: bytes) -> str:
    identity = f"{storage.kind}:{storage.base_path}\0".encode()
    return hashlib.sha256(identity + content).hexdigest()


def _read(storage, path):
    if not text_format(path):
        raise HTTPException(415, "textFile.unsupportedFormat")
    try:
        content, content_type = storage.get_file(path, max_bytes=MAX_TEXT_BYTES)
    except HTTPException as exc:
        if exc.status_code == 413:
            raise HTTPException(413, "textFile.tooLarge") from exc
        raise
    return content, content_type


def read_text_file(storage, path):
    content, _ = _read(storage, path)
    text, encoding, _ = _decode(content)
    return {"content": text, "format": text_format(path), "revision": _revision(storage, content),
            "can_write": storage.can_write, "encoding": encoding}


def write_text_file(storage, path, text, revision):
    lock_key = f"{storage.task_id}:{storage.kind}:{storage.base_path}:{path}".encode()
    with _write_locks[hashlib.sha256(lock_key).digest()[0] % len(_write_locks)]:
        original, content_type = _read(storage, path)
        if revision != _revision(storage, original):
            raise HTTPException(409, "textFile.conflict")
        old_text, encoding, bom = _decode(original)
        _validate_text(text)
        normalized = re.sub(r"\r\n?|\n", "\n", text)
        # Textareas normalisieren Zeilenenden. Unveraenderte Dateien bytegenau belassen.
        if normalized == re.sub(r"\r\n?|\n", "\n", old_text):
            content = original
        else:
            newline = re.search(r"\r\n|\r|\n", old_text)
            newline = newline.group() if newline else "\n"
            try:
                content = bom + normalized.replace("\n", newline).encode(encoding)
            except UnicodeError:
                raise HTTPException(415, "textFile.unsupportedEncoding")
        if len(content) > MAX_TEXT_BYTES:
            raise HTTPException(413, "textFile.tooLarge")
        storage.upload_file(path, content, content_type)
        return {"revision": _revision(storage, content)}
