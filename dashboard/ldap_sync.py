"""
Tareas - LDAP Synchronisierung
Synchronisiert AD-Gruppenmitglieder mit der lokalen users-Tabelle.
"""

from datetime import datetime, timezone

from dashboard.database import get_db
from dashboard.crypto_utils import decrypt
from dashboard.ldap_utils import get_group_members
from dashboard.tls_utils import get_tls_verify_config


def sync_ldap_users():
    """
    Hauptfunktion fuer LDAP-Sync:
    1. LDAP-Config aus DB laden
    2. Gruppenmitglieder abrufen
    3. Benutzer anlegen/aktualisieren/deaktivieren
    4. last_sync_at aktualisieren
    Liefert dict mit Statistiken: {created, updated, deactivated, error}
    """
    db = get_db()
    try:
        # 1. Config laden
        config_row = db.execute("SELECT * FROM ldap_config WHERE id = 1").fetchone()
        if not config_row:
            return {"error": "Keine LDAP-Konfiguration vorhanden"}

        if not config_row["group_dn"]:
            return {"error": "Keine AD-Gruppe ausgewaehlt"}

        verify_cfg = get_tls_verify_config()
        config = {
            "server": config_row["server"],
            "port": config_row["port"],
            "use_ssl": config_row["use_ssl"],
            "bind_dn": config_row["bind_dn"],
            "bind_password": decrypt(config_row["bind_password"]),
            "search_base": config_row["search_base"],
            "group_dn": config_row["group_dn"],
            "verify_cert": verify_cfg["verify_ldap"],
        }

        # 2. Gruppenmitglieder abrufen
        try:
            members = get_group_members(config)
        except Exception as e:
            return {"error": f"LDAP-Abfrage fehlgeschlagen: {e}"}

        stats = {"created": 0, "updated": 0, "deactivated": 0}
        member_usernames = set()

        # 3. Fuer jeden AD-Benutzer
        for member in members:
            username = member["sAMAccountName"]
            if not username:
                continue

            member_usernames.add(username.lower())

            existing = db.execute(
                "SELECT id, auth_source FROM users WHERE username = ?",
                (username,),
            ).fetchone()

            if existing:
                if existing["auth_source"] == "ldap":
                    # Attribute aktualisieren, is_active=1 setzen
                    db.execute(
                        """UPDATE users SET vorname = ?, nachname = ?, email = ?,
                           ldap_dn = ?, is_active = 1 WHERE id = ?""",
                        (member["givenName"], member["sn"], member["mail"],
                         member["dn"], existing["id"]),
                    )
                    stats["updated"] += 1
                # Lokale Benutzer (auth_source='local') werden NICHT angefasst
            else:
                # Neuen LDAP-User anlegen
                db.execute(
                    """INSERT INTO users (username, password_hash, vorname, nachname, email,
                       auth_source, ldap_dn, is_active, is_admin)
                       VALUES (?, '', ?, ?, ?, 'ldap', ?, 1, 0)""",
                    (username, member["givenName"], member["sn"], member["mail"],
                     member["dn"]),
                )
                stats["created"] += 1

        # 4. LDAP-Benutzer deaktivieren die nicht mehr in der Gruppe sind
        ldap_users = db.execute(
            "SELECT id, username FROM users WHERE auth_source = 'ldap' AND is_active = 1"
        ).fetchall()

        for ldap_user in ldap_users:
            if ldap_user["username"].lower() not in member_usernames:
                db.execute(
                    "UPDATE users SET is_active = 0 WHERE id = ?",
                    (ldap_user["id"],),
                )
                stats["deactivated"] += 1

        # 5. last_sync_at aktualisieren
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        db.execute("UPDATE ldap_config SET last_sync_at = ? WHERE id = 1", (now,))

        db.commit()
        return stats

    except Exception as e:
        return {"error": str(e)}
    finally:
        db.close()
