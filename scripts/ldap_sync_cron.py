#!/usr/bin/env python3
"""
Tareas - LDAP Sync Cron-Script
Wird per systemd-Timer oder Crontab aufgerufen.
Prueft ob genug Zeit seit dem letzten Sync vergangen ist.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

# Projekt-Root zum Path hinzufuegen
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from dashboard.database import get_db, init_db
from dashboard.ldap_sync import sync_ldap_users


def main():
    # DB initialisieren (fuer den Fall dass Schema noch nicht aktuell)
    init_db()

    db = get_db()
    try:
        config = db.execute("SELECT * FROM ldap_config WHERE id = 1").fetchone()
        if not config:
            print("Keine LDAP-Konfiguration vorhanden, ueberspringe Sync.")
            return

        if not config["group_dn"]:
            print("Keine AD-Gruppe ausgewaehlt, ueberspringe Sync.")
            return

        # Pruefen ob genug Zeit vergangen ist
        interval = config["sync_interval_minutes"] or 60
        last_sync = config["last_sync_at"]

        if last_sync:
            last_dt = datetime.strptime(last_sync, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            elapsed = (datetime.now(timezone.utc) - last_dt).total_seconds() / 60
            if elapsed < interval:
                print(f"Letzter Sync vor {elapsed:.0f} Min. (Intervall: {interval} Min.), ueberspringe.")
                return
    finally:
        db.close()

    print(f"Starte LDAP-Sync... ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})")
    result = sync_ldap_users()

    if "error" in result:
        print(f"FEHLER: {result['error']}")
        sys.exit(1)
    else:
        print(f"Sync abgeschlossen: Erstellt={result['created']}, "
              f"Aktualisiert={result['updated']}, Deaktiviert={result['deactivated']}")


if __name__ == "__main__":
    main()
