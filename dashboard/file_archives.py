"""ZIP-Downloads lokaler und WebDAV-Ablagen ohne Dateiinhalte im Arbeitsspeicher."""

import posixpath
from contextlib import closing
from pathlib import PureWindowsPath
from zipfile import ZipFile, ZIP_DEFLATED

from dashboard.errors import ApplicationError
from dashboard.file_storage import safe_filename, safe_rel_path

MAX_ARCHIVE_PATHS = 500
MAX_ARCHIVE_ENTRIES = 10000
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
MAX_ARCHIVE_DEPTH = 100


def _too_large():
    return ApplicationError(413, "files.archiveTooLarge", code="archive_too_large", field="paths")


def build_archive(storage, paths: list[str], output) -> str:
    """Auswahl deduplizieren, Struktur/Leerordner erhalten, erst vollstaendig ausliefern.

    ZIP-Pfade beginnen am gemeinsamen Elternordner der Auswahl. Die leere
    Pfadangabe waehlt die gesamte Ablage, ohne einen kuenstlichen Wurzelordner.
    """
    if not paths or len(paths) > MAX_ARCHIVE_PATHS:
        raise ApplicationError(400, "files.archiveSelectionRequired", code="invalid_selection", field="paths")
    paths = sorted({safe_rel_path(path) for path in paths})
    roots = []
    # Nur die zuletzt gelesene Elternliste behalten, auch bei grosser Auswahl.
    last_parent, siblings = None, {}
    for path in paths:
        if any(kind == "directory" and (not root or path.startswith(root + "/")) for root, kind in roots):
            continue
        if not path:
            roots.append((path, "directory"))
            continue
        parent, name = posixpath.split(path)
        if parent != last_parent:
            siblings = {item["name"]: item["type"] for item in storage.list_directory(parent)}
            last_parent = parent
        kind = siblings.get(name)
        if kind not in ("file", "directory"):
            raise FileNotFoundError(path)
        roots.append((path, kind))

    base = posixpath.commonpath([posixpath.dirname(path) for path, _ in roots])
    pending = [(path, kind, 0) for path, kind in reversed(roots)]
    entries, total_bytes = 0, 0
    with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=1, allowZip64=True) as archive:
        while pending:
            path, kind, depth = pending.pop()
            if depth > MAX_ARCHIVE_DEPTH:
                raise _too_large()
            if path:
                arcname = safe_rel_path(posixpath.relpath(path, base or "."), allow_empty=False)
                # Auch auf Windows darf ein ZIP-Eintrag kein Laufwerk adressieren.
                if PureWindowsPath(arcname).drive:
                    raise ApplicationError(400, "Ungueltiger Pfad", code="invalid_path", field="paths")
                entries += 1
                if entries > MAX_ARCHIVE_ENTRIES or len(arcname.encode("utf-8")) > 65534:
                    raise _too_large()
                if kind == "directory":
                    archive.writestr(arcname + "/", b"")
                else:
                    with closing(storage.iter_file(path)) as source, archive.open(arcname, "w", force_zip64=True) as target:
                        for chunk in source:
                            total_bytes += len(chunk)
                            if total_bytes > MAX_ARCHIVE_BYTES:
                                raise _too_large()
                            target.write(chunk)
            if kind == "directory":
                children = storage.list_directory(path)
                if entries + len(pending) + len(children) > MAX_ARCHIVE_ENTRIES:
                    raise _too_large()
                for child in reversed(children):
                    name = safe_filename(child["name"])
                    if child["type"] not in ("file", "directory"):
                        raise ApplicationError(400, "Ungueltiger Dateityp", code="invalid_path", field="paths")
                    pending.append((posixpath.join(path, name), child["type"], depth + 1))

    return (posixpath.basename(roots[0][0]) if len(roots) == 1 and roots[0][0]
            else f"task-{storage.task_id}-files") + ".zip"
