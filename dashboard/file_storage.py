"""Projektgebundene Dateiablage auf dem Server oder ueber WebDAV."""

import fcntl
import hashlib
import mimetypes
import os
import posixpath
import shutil
import stat
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

from dashboard import database, webdav
from dashboard.db_utils import db_query
from dashboard.errors import ApplicationError
from dashboard.permissions import require_task_access


def safe_rel_path(value: str, *, allow_empty: bool = True) -> str:
    """Pfadsegmente pruefen, auch bei mehrfach URL-kodierten Eingaben.

    Die Rueckgabe behaelt literale Prozentzeichen in Dateinamen bei.
    """
    decoded = value
    while True:
        if (decoded.startswith("/") or "\\" in decoded
                or any(ord(char) < 32 or ord(char) == 127 for char in decoded)
                or ".." in decoded.split("/")):
            raise ApplicationError(400, "Ungueltiger Pfad", code="invalid_path", field="path")
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    result = posixpath.normpath(value)
    if result == ".":
        result = ""
    if not result and not allow_empty:
        raise ApplicationError(400, "Ein Datei- oder Ordnerpfad ist erforderlich", code="path_required", field="path")
    return result


def safe_filename(value: str) -> str:
    if not value or value in (".", "..") or "/" in value:
        raise ApplicationError(400, "Ungueltiger Dateiname", code="invalid_filename", field="filename")
    decoded = value
    while True:
        safe_rel_path(decoded, allow_empty=False)
        if "/" in decoded:
            raise ApplicationError(400, "Ungueltiger Dateiname", code="invalid_filename", field="filename")
        next_value = unquote(decoded)
        if next_value == decoded:
            return value
        decoded = next_value


def storage_type(task) -> str:
    """Alte WebDAV-Zuordnungen bleiben ohne Umstellung lesbar."""
    if task["file_storage_type"] == "local":
        return "local"
    return "webdav" if task["nextcloud_path"] else "none"


def local_path(task_id: int, path: str = "") -> Path:
    """Keine frei waehlbaren Serverpfade oder symbolischen Links zulassen."""
    path = safe_rel_path(path)
    base = database.DB_DIR.resolve()
    target = base / "files" / f"task-{int(task_id)}"
    if path:
        target = target / path
    current = base
    for part in target.relative_to(base).parts:
        current = current / part
        if current.is_symlink():
            raise ApplicationError(400, "Symbolische Links sind in der Dateiablage nicht erlaubt",
                                   code="invalid_path", field="path")
    return target


def ensure_local_directory(task_id: int):
    root = local_path(task_id)
    root.parent.mkdir(mode=0o700, exist_ok=True)
    root.mkdir(mode=0o700, exist_ok=True)


def remove_local_directory(task_id: int):
    """Nur das serverseitig bestimmte Projektverzeichnis samt Inhalt entfernen."""
    root = local_path(task_id)
    if root.exists():
        # rmtree entfernt enthaltene Symlinks, ohne deren Ziele zu verfolgen.
        shutil.rmtree(root)


def get_task_storage(task_id: int, user: dict, *, write: bool = False):
    """Ablage mit den aktuellen Rechten des Session- oder WOPI-Benutzers laden."""
    with db_query() as db:
        task, rights = require_task_access(db, task_id, user, "edit" if write else "read")
        can_write = rights["can_edit"]
        kind = storage_type(task)
        if kind == "none":
            raise ApplicationError(400, "Keine Dateiablage zugeordnet",
                                   code="storage_not_configured", field="file_storage_type")
        base_path = safe_rel_path(task["nextcloud_path"], allow_empty=False) if kind == "webdav" else ""
        return TaskStorage(task_id, kind, base_path, can_write)


class TaskStorage:
    def __init__(self, task_id: int, kind: str, base_path: str, can_write: bool):
        self.task_id = task_id
        self.kind = kind
        self.base_path = base_path
        self.can_write = can_write

    def _path(self, path: str, *, allow_empty: bool = False):
        path = safe_rel_path(path, allow_empty=allow_empty)
        if self.kind == "local":
            return local_path(self.task_id, path)
        return f"{self.base_path}/{path}" if path else self.base_path

    def list_directory(self, path: str = "") -> list[dict]:
        target = self._path(path, allow_empty=True)
        if self.kind == "webdav":
            entries = webdav.list_directory(target)
            config = webdav._get_config()
            if config:
                for item in entries:
                    if item.get("file_id"):
                        item["nextcloud_link"] = webdav.get_nextcloud_link(config, item["file_id"])
        else:
            entries = []
            for entry in target.iterdir():
                info = entry.lstat()
                if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                    continue
                is_dir = stat.S_ISDIR(info.st_mode)
                entries.append({
                    "name": entry.name, "type": "directory" if is_dir else "file",
                    "size": 0 if is_dir else info.st_size,
                    "last_modified": datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat(),
                    "mime_type": "" if is_dir else self._mime_type(entry),
                })
        entries.sort(key=lambda item: (item["type"] != "directory", item["name"].lower()))
        return entries

    @staticmethod
    def _mime_type(path: Path) -> str:
        return mimetypes.guess_type(path.name)[0] or "application/octet-stream"

    def get_file(self, path: str, *, max_bytes: int | None = None) -> tuple[bytes, str]:
        target = self._path(path)
        if self.kind == "webdav":
            if max_bytes is None:
                return webdav.get_file(target)
            response, client, content_type, _ = webdav.get_file_stream(target)
            try:
                content = bytearray()
                for chunk in response.iter_bytes(chunk_size=65536):
                    content.extend(chunk)
                    self._check_read_size(len(content), max_bytes)
                return bytes(content), content_type
            finally:
                response.close()
                client.close()
        if not target.is_file():
            raise FileNotFoundError(path)
        with target.open("rb") as source:
            content = source.read() if max_bytes is None else source.read(max_bytes + 1)
        self._check_read_size(len(content), max_bytes)
        return content, self._mime_type(target)

    @staticmethod
    def _check_read_size(size: int, max_bytes: int | None):
        if max_bytes is not None and size > max_bytes:
            raise ApplicationError(413, f"Datei zu gross fuer diesen Abruf (max. {max_bytes} Bytes)",
                                   code="file_too_large", field="path")

    @contextmanager
    def _write_lock(self, path: str):
        """Nur den Speichervorgang sperren, gemeinsam fuer alle Dienstprozesse.

        Der Ablagepfad identifiziert die Datei auch bei mehreren Projekten mit
        derselben WebDAV-Zuordnung. Separate Lockdateien ueberstehen os.replace;
        sie duerfen nicht geloescht werden, solange Dienste darauf zugreifen.
        """
        target = self._path(path)
        key = hashlib.sha256(f"{self.kind}:{target}".encode()).hexdigest()
        directory = database.DB_DIR / ".file-locks"
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        with (directory / f"{key}.lock").open("a") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            yield
        # close() gibt die Sperre auch bei Fehlern frei; bei Prozessende der Kernel.

    def upload_file(self, path: str, content: bytes, content_type: str = "application/octet-stream"):
        with self._write_lock(path):
            return self._upload_file(path, content, content_type)

    def update_file(self, path: str, transform, *, max_bytes: int):
        """Aktuellen Inhalt pruefen/umwandeln und unter derselben Sperre ersetzen."""
        with self._write_lock(path):
            original, content_type = self.get_file(path, max_bytes=max_bytes)
            content = transform(original)
            self._upload_file(path, content, content_type)
            return content

    def _upload_file(self, path: str, content: bytes, content_type: str):
        """Schreiben innerhalb von upload_file/update_file, die bereits sperren."""
        target = self._path(path)
        if self.kind == "webdav":
            return webdav.upload_file(target, content, content_type)
        if target.exists() and not target.is_file():
            raise FileExistsError(path)
        # Ersetzen erst nach vollstaendigem Schreiben, damit Fehler keine Datei zerstoeren.
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".upload-", delete=False) as tmp:
                temp_path = Path(tmp.name)
                tmp.write(content)
                tmp.flush()
                os.fsync(tmp.fileno())
            os.replace(temp_path, target)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    def create_directory(self, path: str):
        target = self._path(path)
        if self.kind == "webdav":
            return webdav.create_directory(target)
        target.mkdir(mode=0o700)

    def delete_item(self, path: str):
        target = self._path(path)
        if self.kind == "webdav":
            return webdav.delete_item(target)
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()

    def move_item(self, source: str, destination: str):
        try:
            source_path = self._path(source)
        except ApplicationError as exc:
            exc.field = "source"
            raise
        try:
            dest_path = self._path(destination)
        except ApplicationError as exc:
            exc.field = "destination"
            raise
        if self.kind == "webdav":
            return webdav.move_item(source_path, dest_path)
        if not source_path.exists():
            raise ApplicationError(404, "Datei oder Verzeichnis nicht gefunden",
                                   code="file_not_found", field="source")
        if dest_path.exists():
            raise ApplicationError(409, "Datei oder Verzeichnis existiert bereits",
                                   code="file_exists", field="destination")
        if source_path in dest_path.parents:
            raise ApplicationError(400, "Ordner kann nicht in sich selbst verschoben werden",
                                   code="invalid_destination", field="destination")
        source_path.rename(dest_path)
