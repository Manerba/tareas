"""Dynamische CSP-Hilfsfunktionen fuer OnlyOffice-Integration."""
import logging
from urllib.parse import urlparse

from dashboard.db_utils import db_query

logger = logging.getLogger(__name__)

# ============================================================
# OnlyOffice-Origin (fuer dynamische CSP und Dateiaktionen)
# ============================================================

def get_onlyoffice_origin() -> str | None:
    """OnlyOffice-Origin (Schema+Host+Port) aus der aktuellen DB-Konfiguration laden.

    Haupt-App und Admin laufen in getrennten Prozessen. Ein Prozess-Cache
    wuerde Aenderungen im Admin nicht zuverlaessig beruecksichtigen.

    Returns:
        Origin-String (z.B. 'https://office.example.com:8443') oder None
    """
    origin = None
    try:
        with db_query() as db:
            row = db.execute(
                "SELECT server_url FROM onlyoffice_config WHERE id = 1"
            ).fetchone()
            if row and row["server_url"]:
                parsed = urlparse(row["server_url"])
                # Origin = Schema + Host + ggf. Port
                if parsed.scheme in ("http", "https") and parsed.hostname:
                    port_part = f":{parsed.port}" if parsed.port else ""
                    origin = f"{parsed.scheme}://{parsed.hostname}{port_part}"
    except Exception:
        logger.warning("OnlyOffice-Origin konnte nicht aus DB geladen werden", exc_info=True)

    return origin
