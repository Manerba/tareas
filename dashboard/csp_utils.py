"""Dynamische CSP-Hilfsfunktionen fuer OnlyOffice-Integration."""
import logging
from urllib.parse import urlparse

from dashboard.db_utils import db_query

logger = logging.getLogger(__name__)

# ============================================================
# OnlyOffice-Origin Cache (fuer dynamische CSP)
# ============================================================

_onlyoffice_origin_cache = {"origin": None, "loaded": False}


def get_onlyoffice_origin() -> str | None:
    """OnlyOffice-Origin (Schema+Host+Port) aus DB laden und cachen.

    Returns:
        Origin-String (z.B. 'https://office.example.com:8443') oder None
    """
    if _onlyoffice_origin_cache["loaded"]:
        return _onlyoffice_origin_cache["origin"]

    origin = None
    try:
        with db_query() as db:
            row = db.execute(
                "SELECT server_url FROM onlyoffice_config WHERE id = 1"
            ).fetchone()
            if row and row["server_url"]:
                parsed = urlparse(row["server_url"])
                # Origin = Schema + Host + ggf. Port
                if parsed.scheme and parsed.hostname:
                    port_part = f":{parsed.port}" if parsed.port else ""
                    origin = f"{parsed.scheme}://{parsed.hostname}{port_part}"
    except Exception:
        logger.warning("OnlyOffice-Origin konnte nicht aus DB geladen werden", exc_info=True)

    _onlyoffice_origin_cache["origin"] = origin
    _onlyoffice_origin_cache["loaded"] = True
    return origin


def invalidate_onlyoffice_cache():
    """OnlyOffice-Origin-Cache invalidieren (nach Config-Aenderung aufrufen)."""
    _onlyoffice_origin_cache["loaded"] = False
