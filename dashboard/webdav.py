"""
Tareas - WebDAV-Modul fuer Nextcloud-Integration.
Kommunikation mit Nextcloud via WebDAV (httpx).
"""

import re
from typing import Optional
from xml.etree import ElementTree as ET

import httpx

from dashboard.database import get_db
from dashboard.crypto_utils import decrypt
from dashboard.tls_utils import get_tls_verify_config

# WebDAV XML-Namespaces
DAV_NS = "DAV:"
OC_NS = "http://owncloud.org/ns"
NC_NS = "http://nextcloud.org/ns"

PROPFIND_BODY = """<?xml version="1.0" encoding="UTF-8"?>
<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns" xmlns:nc="http://nextcloud.org/ns">
  <d:prop>
    <d:resourcetype/>
    <d:getcontentlength/>
    <d:getlastmodified/>
    <d:getcontenttype/>
    <oc:fileid/>
  </d:prop>
</d:propfind>"""

TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def _get_config() -> dict | None:
    """Nextcloud-Konfiguration aus der DB laden."""
    db = get_db()
    try:
        row = db.execute("SELECT * FROM nextcloud_config WHERE id = 1").fetchone()
        if not row:
            return None
        verify_cfg = get_tls_verify_config()
        return {
            "server_url": row["server_url"].rstrip("/"),
            "username": row["username"],
            "password": decrypt(row["password"]),
            "base_path": row["base_path"].strip("/"),
            "verify": verify_cfg["verify_nextcloud"],
        }
    finally:
        db.close()


def _build_webdav_url(config: dict, rel_path: str = "") -> str:
    """Vollstaendige WebDAV-URL zusammenbauen."""
    base = f"{config['server_url']}/remote.php/dav/files/{config['username']}"
    if config["base_path"]:
        base += f"/{config['base_path']}"
    if rel_path:
        # Slashes normalisieren
        rel_path = rel_path.strip("/")
        if rel_path:
            base += f"/{rel_path}"
    return base


def _build_auth(config: dict) -> httpx.BasicAuth:
    """HTTP-Basic-Auth aus Config erstellen."""
    return httpx.BasicAuth(config["username"], config["password"])


def _parse_propfind(xml_text: str, base_url: str) -> list[dict]:
    """PROPFIND-XML-Antwort parsen und Eintraege zurueckgeben."""
    entries = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return entries

    for response in root.findall(f"{{{DAV_NS}}}response"):
        href_el = response.find(f"{{{DAV_NS}}}href")
        if href_el is None or href_el.text is None:
            continue

        href = href_el.text.rstrip("/")

        propstat = response.find(f"{{{DAV_NS}}}propstat")
        if propstat is None:
            continue

        prop = propstat.find(f"{{{DAV_NS}}}prop")
        if prop is None:
            continue

        # Resourcetype pruefen
        restype = prop.find(f"{{{DAV_NS}}}resourcetype")
        is_dir = False
        if restype is not None:
            is_dir = restype.find(f"{{{DAV_NS}}}collection") is not None

        # Dateigroesse
        size_el = prop.find(f"{{{DAV_NS}}}getcontentlength")
        size = int(size_el.text) if size_el is not None and size_el.text else 0

        # Letzte Aenderung
        modified_el = prop.find(f"{{{DAV_NS}}}getlastmodified")
        last_modified = modified_el.text if modified_el is not None and modified_el.text else ""

        # MIME-Type
        mime_el = prop.find(f"{{{DAV_NS}}}getcontenttype")
        mime_type = mime_el.text if mime_el is not None and mime_el.text else ""

        # Nextcloud File-ID
        fileid_el = prop.find(f"{{{OC_NS}}}fileid")
        file_id = fileid_el.text if fileid_el is not None and fileid_el.text else ""

        # Name aus href extrahieren
        # href ist URL-encoded, daher dekodieren
        from urllib.parse import unquote
        decoded_href = unquote(href)
        name = decoded_href.rsplit("/", 1)[-1] if "/" in decoded_href else decoded_href

        entries.append({
            "name": name,
            "type": "directory" if is_dir else "file",
            "size": size,
            "last_modified": last_modified,
            "mime_type": mime_type,
            "file_id": file_id,
            "href": href,
        })

    return entries


def test_connection(server_url: str, username: str, password: str, base_path: str) -> tuple[bool, str]:
    """
    Verbindungstest: PROPFIND auf das Wurzelverzeichnis.
    Gibt (True, "") bei Erfolg oder (False, Fehlermeldung) zurueck.
    """
    url = f"{server_url.rstrip('/')}/remote.php/dav/files/{username}"
    if base_path.strip("/"):
        url += f"/{base_path.strip('/')}"

    auth = httpx.BasicAuth(username, password)
    verify = get_tls_verify_config()["verify_nextcloud"]

    try:
        with httpx.Client(timeout=TIMEOUT, verify=verify) as client:
            resp = client.request(
                "PROPFIND",
                url,
                headers={"Depth": "0", "Content-Type": "application/xml"},
                content=PROPFIND_BODY,
                auth=auth,
            )
            if resp.status_code == 207:
                return True, ""
            elif resp.status_code == 401:
                return False, "Authentifizierung fehlgeschlagen (401)"
            elif resp.status_code == 404:
                return False, f"Verzeichnis nicht gefunden: {base_path} (404)"
            else:
                return False, f"Unerwarteter Status: {resp.status_code}"
    except httpx.ConnectError as e:
        return False, f"Verbindungsfehler: {e}"
    except httpx.TimeoutException:
        return False, "Zeitueberschreitung bei der Verbindung"
    except Exception as e:
        return False, f"Fehler: {e}"


def list_directory(rel_path: str = "") -> list[dict]:
    """
    Verzeichnisinhalt auflisten (PROPFIND Depth 1).
    rel_path ist relativ zum base_path.
    """
    config = _get_config()
    if not config:
        raise RuntimeError("Keine Nextcloud-Konfiguration vorhanden")

    url = _build_webdav_url(config, rel_path)
    auth = _build_auth(config)

    with httpx.Client(timeout=TIMEOUT, verify=config.get("verify", False)) as client:
        resp = client.request(
            "PROPFIND",
            url,
            headers={"Depth": "1", "Content-Type": "application/xml"},
            content=PROPFIND_BODY,
            auth=auth,
        )
        if resp.status_code == 404:
            raise FileNotFoundError(f"Verzeichnis nicht gefunden: {rel_path}")
        if resp.status_code != 207:
            raise RuntimeError(f"WebDAV-Fehler: {resp.status_code}")

    entries = _parse_propfind(resp.text, url)

    # Erstes Element ist das Verzeichnis selbst -> entfernen
    if entries:
        entries = entries[1:]

    return entries


def list_subdirectories(base_path: str = "") -> list[dict]:
    """Nur Unterordner des Wurzelverzeichnisses auflisten (fuer Dropdown)."""
    entries = list_directory(base_path)
    return [e for e in entries if e["type"] == "directory"]


def get_file(rel_path: str) -> tuple[bytes, str]:
    """
    Datei herunterladen (GET).
    Gibt (Dateiinhalt, MIME-Type) zurueck.
    """
    config = _get_config()
    if not config:
        raise RuntimeError("Keine Nextcloud-Konfiguration vorhanden")

    url = _build_webdav_url(config, rel_path)
    auth = _build_auth(config)

    with httpx.Client(timeout=TIMEOUT, verify=config.get("verify", False)) as client:
        resp = client.get(url, auth=auth)
        if resp.status_code == 404:
            raise FileNotFoundError(f"Datei nicht gefunden: {rel_path}")
        if resp.status_code != 200:
            raise RuntimeError(f"WebDAV-Fehler: {resp.status_code}")

    content_type = resp.headers.get("content-type", "application/octet-stream")
    return resp.content, content_type


def get_file_stream(rel_path: str):
    """
    Datei als Stream herunterladen (fuer grosse Dateien).
    Gibt (httpx.Response-Stream, content_type, content_length) zurueck.
    Aufrufer muss den Client/Response schliessen.
    """
    config = _get_config()
    if not config:
        raise RuntimeError("Keine Nextcloud-Konfiguration vorhanden")

    url = _build_webdav_url(config, rel_path)
    auth = _build_auth(config)

    client = httpx.Client(timeout=TIMEOUT, verify=config.get("verify", False))
    resp = client.send(
        client.build_request("GET", url, headers={}),
        auth=auth,
        stream=True,
    )
    if resp.status_code == 404:
        resp.close()
        client.close()
        raise FileNotFoundError(f"Datei nicht gefunden: {rel_path}")
    if resp.status_code != 200:
        resp.close()
        client.close()
        raise RuntimeError(f"WebDAV-Fehler: {resp.status_code}")

    content_type = resp.headers.get("content-type", "application/octet-stream")
    content_length = resp.headers.get("content-length")
    return resp, client, content_type, content_length


def upload_file(rel_path: str, content: bytes, content_type: str = "application/octet-stream") -> bool:
    """Datei hochladen (PUT)."""
    config = _get_config()
    if not config:
        raise RuntimeError("Keine Nextcloud-Konfiguration vorhanden")

    url = _build_webdav_url(config, rel_path)
    auth = _build_auth(config)

    with httpx.Client(timeout=TIMEOUT, verify=config.get("verify", False)) as client:
        resp = client.put(
            url,
            content=content,
            headers={"Content-Type": content_type},
            auth=auth,
        )
        if resp.status_code not in (200, 201, 204):
            raise RuntimeError(f"Upload fehlgeschlagen: {resp.status_code}")

    return True


def create_directory(rel_path: str) -> bool:
    """Ordner erstellen (MKCOL)."""
    config = _get_config()
    if not config:
        raise RuntimeError("Keine Nextcloud-Konfiguration vorhanden")

    url = _build_webdav_url(config, rel_path)
    auth = _build_auth(config)

    with httpx.Client(timeout=TIMEOUT, verify=config.get("verify", False)) as client:
        resp = client.request("MKCOL", url, auth=auth)
        if resp.status_code == 405:
            raise FileExistsError(f"Ordner existiert bereits: {rel_path}")
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Ordner erstellen fehlgeschlagen: {resp.status_code}")

    return True


def delete_item(rel_path: str) -> bool:
    """Datei oder Ordner loeschen (DELETE)."""
    config = _get_config()
    if not config:
        raise RuntimeError("Keine Nextcloud-Konfiguration vorhanden")

    url = _build_webdav_url(config, rel_path)
    auth = _build_auth(config)

    with httpx.Client(timeout=TIMEOUT, verify=config.get("verify", False)) as client:
        resp = client.delete(url, auth=auth)
        if resp.status_code == 404:
            raise FileNotFoundError(f"Nicht gefunden: {rel_path}")
        if resp.status_code not in (200, 204):
            raise RuntimeError(f"Loeschen fehlgeschlagen: {resp.status_code}")

    return True


def move_item(source_path: str, dest_path: str) -> bool:
    """Datei/Ordner verschieben/umbenennen (MOVE)."""
    config = _get_config()
    if not config:
        raise RuntimeError("Keine Nextcloud-Konfiguration vorhanden")

    source_url = _build_webdav_url(config, source_path)
    dest_url = _build_webdav_url(config, dest_path)
    auth = _build_auth(config)

    with httpx.Client(timeout=TIMEOUT, verify=config.get("verify", False)) as client:
        resp = client.request(
            "MOVE",
            source_url,
            headers={"Destination": dest_url, "Overwrite": "F"},
            auth=auth,
        )
        if resp.status_code == 404:
            raise FileNotFoundError(f"Quelle nicht gefunden: {source_path}")
        if resp.status_code == 412:
            raise FileExistsError(f"Ziel existiert bereits: {dest_path}")
        if resp.status_code not in (200, 201, 204):
            raise RuntimeError(f"Verschieben fehlgeschlagen: {resp.status_code}")

    return True


def get_nextcloud_link(config: dict, file_id: str) -> str:
    """Direktlink zur Datei in der Nextcloud-Weboberflaeche."""
    if file_id:
        return f"{config['server_url']}/f/{file_id}"
    return ""
