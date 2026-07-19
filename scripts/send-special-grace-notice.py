from __future__ import annotations

from datetime import datetime
from email.utils import format_datetime
from zoneinfo import ZoneInfo

from dashboard.db import get_conn, has_sent_template_today, log_email_delivery
from dashboard.email_templates import get_inline_email_attachments, load_templates, render_template
from dashboard.runner import ActiveDirectoryClient, EntraClient, load_config


CONFIG_PATH = "/app/config/config.json"
RUN_ID = "SPECIAL-GRACE-20260710"
JAKARTA = ZoneInfo("Asia/Jakarta")
CUTOFF = datetime(2026, 7, 13, 23, 0, 0, tzinfo=JAKARTA)


def choose_template_key(days_left: int | None) -> str:
    if days_left is None or days_left <= 0:
        return "expired"
    if days_left <= 1:
        return "warn_1"
    if days_left <= 3:
        return "warn_3"
    return "warn_7"


def main() -> int:
    config = load_config(CONFIG_PATH)
    ad = ActiveDirectoryClient(config)
    entra = EntraClient(config)
    templates = load_templates()
    sender = config.get("M365", {}).get("ReminderSender") or config.get("Notification", {}).get("FromAddress")
    if not sender:
        raise RuntimeError("Missing ReminderSender/FromAddress in config")

    users = ad.fetch_users()
    targets = [
        user
        for user in users
        if not user.get("IsDisabled")
        and isinstance(user.get("DaysUntilExpiry"), int)
        and user["DaysUntilExpiry"] <= 7
    ]

    sent = 0
    skipped = 0
    failed = 0

    for user in targets:
        sam = user.get("SamAccountName")
        upn = user.get("UserPrincipalName")
        email = user.get("Email") or upn
        display_name = user.get("DisplayName") or sam
        days_left = user.get("DaysUntilExpiry")
        template_key = choose_template_key(days_left)
        template = templates[template_key]

        if not email:
            failed += 1
            continue

        if has_sent_template_today(sam, template_key, actions=("Warned", "ForcedChange", "ManualNotify")):
            print(f"[SPECIAL-NOTICE] SKIPPED | user={sam} | template={template_key} | reason=already_sent_today")
            skipped += 1
            continue

        context = {
            "display_name": display_name,
            "upn": upn or email,
            "days_left": days_left if days_left is not None else "",
            "deadline_text": format_datetime(CUTOFF),
            "gsa_hint_image": "cid:gsa-tray-guide",
        }
        subject = render_template(template["subject"], context)
        html = render_template(template["html"], context)

        try:
            result = entra.send_mail(
                sender=sender,
                to_recipients=[email],
                subject=subject,
                html_body=html,
                cc_recipients=[],
                attachments=get_inline_email_attachments(),
            )
            attempt_no = log_email_delivery(
                run_id=RUN_ID,
                sam=sam,
                upn=upn,
                display_name=display_name,
                email=email,
                action="ManualNotify",
                template_key=template_key,
                days_left=days_left,
                subject=subject,
                status="sent",
                provider_status_code=result.get("status_code"),
                error=None,
            )
            with get_conn() as conn:
                conn.execute(
                    "INSERT INTO actions (run_id, sam, upn, display_name, email, action, days_left) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (RUN_ID, sam, upn, display_name, email, "ManualNotify", days_left if days_left is not None else 0),
                )
            print(f"[SPECIAL-NOTICE] SENT | user={sam} | template={template_key} | attempt={attempt_no} | to={email}")
            sent += 1
        except Exception as exc:
            attempt_no = log_email_delivery(
                run_id=RUN_ID,
                sam=sam,
                upn=upn,
                display_name=display_name,
                email=email,
                action="ManualNotify",
                template_key=template_key,
                days_left=days_left,
                subject=subject,
                status="failed",
                provider_status_code=getattr(getattr(exc, "response", None), "status_code", None),
                error=str(exc),
            )
            print(f"[SPECIAL-NOTICE] FAILED | user={sam} | template={template_key} | attempt={attempt_no} | error={exc}")
            failed += 1

    print(
        {
            "run_id": RUN_ID,
            "targets": len(targets),
            "sent": sent,
            "skipped": skipped,
            "failed": failed,
            "cutoff": CUTOFF.isoformat(),
        }
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
