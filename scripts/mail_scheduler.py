#!/usr/bin/env python3
"""
Tareas - Mail Scheduler
Wird per systemd-Timer taeglich aufgerufen.
Versendet Faelligkeits-Mails (heute faellig + Vorwarnung).
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

# Projekt-Root zum Path hinzufuegen
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from dashboard.database import get_db, init_db
from dashboard.mail_service import notify_event


def main():
    init_db()

    db = get_db()
    try:
        # SMTP-Config pruefen
        config = db.execute("SELECT id FROM mail_config WHERE id = 1").fetchone()
        if not config:
            print("Keine Mail-Konfiguration vorhanden, ueberspringe.")
            return

        today = datetime.now().strftime("%Y-%m-%d")
        print(f"Mail-Scheduler gestartet ({today})")

        # 1. Tasks mit Deadline = heute -> deadline_reached
        tasks_today = db.execute(
            """SELECT t.id, t.name, t.assigned_to, t.created_by
               FROM tasks t
               WHERE date(t.deadline) = date(?)
                 AND t.status != 'erledigt'""",
            (today,),
        ).fetchall()

        sent_reached = 0
        for task in tasks_today:
            recipients = set()
            if task["assigned_to"]:
                recipients.add(task["assigned_to"])
            if task["created_by"]:
                recipients.add(task["created_by"])

            for uid in recipients:
                # Pruefen ob User diese Mail aktiviert hat
                pref = db.execute(
                    "SELECT enabled FROM user_mail_preferences WHERE user_id = ? AND event_type = 'deadline_reached'",
                    (uid,),
                ).fetchone()
                if pref and not pref["enabled"]:
                    continue

                result = notify_event("deadline_reached", uid, {
                    "task_name": task["name"],
                })
                if result is None:
                    sent_reached += 1

        # 2. Tasks mit Deadline = heute + X Tage -> deadline_warning
        # Pro User individuelle days_before aus Preferences
        users_with_warning = db.execute(
            """SELECT user_id, days_before FROM user_mail_preferences
               WHERE event_type = 'deadline_warning' AND enabled = 1"""
        ).fetchall()

        sent_warning = 0
        for user_pref in users_with_warning:
            uid = user_pref["user_id"]
            days = user_pref["days_before"] or 2
            target_date = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")

            warning_tasks = db.execute(
                """SELECT t.id, t.name, t.deadline
                   FROM tasks t
                   WHERE date(t.deadline) = date(?)
                     AND t.status != 'erledigt'
                     AND (t.assigned_to = ? OR t.created_by = ?)""",
                (target_date, uid, uid),
            ).fetchall()

            for task in warning_tasks:
                result = notify_event("deadline_warning", uid, {
                    "task_name": task["name"],
                    "days_before": str(days),
                    "deadline": task["deadline"] or "",
                })
                if result is None:
                    sent_warning += 1

        print(f"Fertig: {sent_reached} Faelligkeits-Mails, {sent_warning} Vorwarnungs-Mails gesendet.")

    finally:
        db.close()


if __name__ == "__main__":
    main()
