import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Body, HTTPException

from ..db import build_email_cycle_key, get_conn, get_app_state, log_email_delivery
from ..runner import ActiveDirectoryClient, EntraClient, get_run_mode, load_config
from ..secrets import get_secret_health, runtime_env_path, save_runtime_config

from ..email_templates import (
    get_default_templates,
    get_gsa_hint_image_src,
    get_inline_email_attachments,
    get_template_path,
    load_templates,
    render_template,
    reset_templates,
    save_templates,
)


router = APIRouter()

RUN_MODE_DETAILS = {
    "monitoring_only": {
        "label": "Monitoring Only",
        "badge_class": "pill-neutral",
        "summary": "The dashboard only monitors user data without sending email or changing accounts.",
        "effects": [
            "Still reads expiry, locked, and disabled status from the directory.",
            "Does not send automatic reminder emails.",
            "Does not run automatic force change or disable actions.",
        ],
    },
    "reminder_only": {
        "label": "Reminder Only",
        "badge_class": "pill-warn",
        "summary": "The system only sends reminder emails based on policy, without enforcing account changes.",
        "effects": [
            "Users in the active expiring window can receive automatic reminder emails.",
            "Does not run automatic force change.",
            "Does not run automatic disable after the grace period.",
        ],
    },
    "enforcement": {
        "label": "Enforcement",
        "badge_class": "pill-danger",
        "summary": "The system runs reminder and enforcement actions based on the active policy.",
        "effects": [
            "Sends reminder emails for users that match the policy.",
            "Runs force change on the configured day.",
            "Can run automatic disable after the grace period, depending on ActionAfterGrace.",
        ],
    },
}


def _scheduler_info() -> dict:
    tz = ZoneInfo("Asia/Jakarta")
    now = datetime.now(tz)
    next_run = now.replace(minute=5, second=0, microsecond=0)
    if now.minute >= 5:
        next_run = next_run + timedelta(hours=1)
    return {
        "timezone": "Asia/Jakarta",
        "cron": "5 * * * *",
        "frequency_label": "Every hour at minute 05",
        "next_run_at": next_run.isoformat(),
        "threshold_note": "The 7/3/1-day reminder thresholds are evaluated on every scheduler run. The same template is sent only once per password cycle, with extra same-day duplicate protection.",
    }


def _load_runtime_config() -> dict:
    return load_config(os.getenv("PCE_CONFIG_PATH", "/app/config/config.json"))


def _config_path() -> str:
    return os.getenv("PCE_CONFIG_PATH", "/app/config/config.json")


def _save_runtime_config(config: dict) -> str:
    path = _config_path()
    return save_runtime_config(config, path, runtime_env_path())


def _version_path() -> Path:
    return Path(__file__).resolve().parents[2] / "VERSION"


def _runtime_version() -> str:
    try:
        return _version_path().read_text(encoding="utf-8").strip() or "dev"
    except Exception:
        return "dev"


def _secret_health_summary() -> dict:
    rows = get_secret_health(_config_path(), runtime_env_path())
    return {
        "items": rows,
        "configured": sum(1 for row in rows if row["configured"]),
        "total": len(rows),
    }


def _runtime_env_available(secret_health: dict) -> bool:
    env_file_exists = Path(runtime_env_path()).exists()
    env_backed = any(row["source"] == ".env" for row in secret_health.get("items", []))
    return env_file_exists or env_backed


def _looks_like_email(value: str) -> bool:
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value or ""))


def _ldaps_doctor(config: dict) -> dict:
    ad = config.get("ActiveDirectory", {})
    if not ad.get("Server") or not ad.get("BindUser") or not ad.get("BindPassword"):
        return {
            "ok": False,
            "summary": "LDAPS check skipped because AD bind settings are incomplete",
        }
    client = ActiveDirectoryClient(config)
    try:
        conn = client._password_reset_connection()
        conn.unbind()
        return {"ok": True, "summary": "LDAPS password-reset channel is ready"}
    except Exception as exc:
        return {"ok": False, "summary": str(exc)}


def _serialize_run_mode(config: dict) -> dict:
    current_mode = get_run_mode(config)
    current_detail = RUN_MODE_DETAILS[current_mode]
    policy = config.get("Policy", {})
    return {
        "mode": current_mode,
        "label": current_detail["label"],
        "badge_class": current_detail["badge_class"],
        "summary": current_detail["summary"],
        "effects": current_detail["effects"],
        "path": _config_path(),
        "modes": [
            {
                "key": key,
                "label": value["label"],
                "badge_class": value["badge_class"],
                "summary": value["summary"],
                "effects": value["effects"],
            }
            for key, value in RUN_MODE_DETAILS.items()
        ],
        "policy_context": {
            "warning_days": policy.get("WarningDays", []),
            "force_change_day": policy.get("ForceChangeAtLogonOnDay"),
            "grace_days": policy.get("GracePeriodDaysAfterExpiry"),
            "action_after_grace": policy.get("ActionAfterGrace"),
        },
        "sync_status": _load_sync_status(),
        "policy_status": _load_policy_status(),
        "scheduler": _scheduler_info(),
    }


def _load_sync_status() -> dict:
    sync_state = get_app_state("sync_directory_status", {}) or {}
    return {
        "status": sync_state.get("status", "idle"),
        "action": sync_state.get("action", "sync_directory"),
        "started_at": sync_state.get("started_at"),
        "finished_at": sync_state.get("finished_at"),
        "exit_code": sync_state.get("exit_code"),
        "error": sync_state.get("error"),
    }


def _load_policy_status() -> dict:
    policy_state = get_app_state("policy_status", {}) or {}
    with get_conn() as c:
        last_run = c.execute(
            "SELECT run_id, started_at, finished_at, whatif, total, warned, forced_change, disabled, errors FROM runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return {
        "status": policy_state.get("status", "idle"),
        "action": policy_state.get("action"),
        "started_at": policy_state.get("started_at"),
        "finished_at": policy_state.get("finished_at"),
        "exit_code": policy_state.get("exit_code"),
        "error": policy_state.get("error"),
        "last_run": dict(last_run) if last_run else None,
    }


def _scope_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _comma_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def _serialize_quick_config(config: dict) -> dict:
    policy = config.get("Policy", {})
    scope = config.get("Scope", {})
    ad = config.get("ActiveDirectory", {})
    m365 = config.get("M365", {})
    notification = config.get("Notification", {})
    dashboard = config.get("Dashboard", {})
    return {
        "path": _config_path(),
        "runtime_env_path": runtime_env_path(),
        "policy": {
            "max_password_age_days": policy.get("MaxPasswordAgeDays", 90),
            "warning_days": policy.get("WarningDays", [7, 3, 1]),
            "grace_period_days_after_expiry": policy.get("GracePeriodDaysAfterExpiry", 3),
            "force_change_at_logon_on_day": policy.get("ForceChangeAtLogonOnDay", 0),
            "action_after_grace": policy.get("ActionAfterGrace", "Disable"),
        },
        "active_directory": {
            "server": ad.get("Server", ""),
            "port": ad.get("Port", 389),
            "use_ssl": bool(ad.get("UseSsl", False)),
            "bind_user": ad.get("BindUser", ""),
            "bind_password_configured": bool(str(ad.get("BindPassword", "")).strip()),
            "search_base": ad.get("SearchBase") or scope.get("TargetOU", ""),
            "search_filter": ad.get("SearchFilter", ""),
        },
        "m365": {
            "tenant_id": m365.get("TenantId", ""),
            "client_id": m365.get("ClientId", ""),
            "reminder_sender": m365.get("ReminderSender", ""),
            "revoke_sessions_on_lock": bool(m365.get("RevokeSessionsOnLock", False)),
            "client_secret_configured": bool(str(m365.get("ClientSecret", "")).strip()),
        },
        "notification": {
            "from_address": notification.get("FromAddress", ""),
            "from_display_name": notification.get("FromDisplayName", ""),
            "admin_recipients": notification.get("AdminRecipients", []),
            "password_configured": bool(str(notification.get("Password", "")).strip()),
        },
        "dashboard": {
            "base_url": dashboard.get("BaseUrl", ""),
            "api_token_configured": bool(str(dashboard.get("ApiToken", "")).strip()),
        },
    }


def _apply_quick_config_payload(config: dict, payload: dict) -> dict:
    policy = config.setdefault("Policy", {})
    ad = config.setdefault("ActiveDirectory", {})
    m365 = config.setdefault("M365", {})
    notification = config.setdefault("Notification", {})
    dashboard = config.setdefault("Dashboard", {})

    policy_payload = payload.get("policy") or {}
    ad_payload = payload.get("active_directory") or {}
    m365_payload = payload.get("m365") or {}
    notification_payload = payload.get("notification") or {}
    dashboard_payload = payload.get("dashboard") or {}

    if policy_payload:
        warning_days = policy_payload.get("warning_days")
        if warning_days is not None:
            if not isinstance(warning_days, list):
                raise HTTPException(status_code=400, detail="warning_days must be a list")
            normalized_days = sorted({int(day) for day in warning_days})
            policy["WarningDays"] = normalized_days
        for src_key, dest_key in (
            ("max_password_age_days", "MaxPasswordAgeDays"),
            ("grace_period_days_after_expiry", "GracePeriodDaysAfterExpiry"),
            ("force_change_at_logon_on_day", "ForceChangeAtLogonOnDay"),
        ):
            if src_key in policy_payload:
                policy[dest_key] = int(policy_payload.get(src_key))
        if "action_after_grace" in policy_payload:
            action = str(policy_payload.get("action_after_grace") or "").strip()
            if not action:
                raise HTTPException(status_code=400, detail="action_after_grace is required")
            policy["ActionAfterGrace"] = action

    if ad_payload:
        for src_key, dest_key in (
            ("server", "Server"),
            ("bind_user", "BindUser"),
            ("search_base", "SearchBase"),
            ("search_filter", "SearchFilter"),
        ):
            if src_key in ad_payload:
                ad[dest_key] = str(ad_payload.get(src_key) or "").strip()
        if "bind_password" in ad_payload:
            bind_password = ad_payload.get("bind_password")
            if bind_password is None:
                pass
            else:
                bind_password = str(bind_password)
                if bind_password.strip():
                    ad["BindPassword"] = bind_password
        if "port" in ad_payload:
            ad["Port"] = int(ad_payload.get("port"))
        if "use_ssl" in ad_payload:
            ad["UseSsl"] = bool(ad_payload.get("use_ssl"))

    if m365_payload:
        for src_key, dest_key in (
            ("tenant_id", "TenantId"),
            ("client_id", "ClientId"),
            ("reminder_sender", "ReminderSender"),
        ):
            if src_key in m365_payload:
                m365[dest_key] = str(m365_payload.get(src_key) or "").strip()
        if "revoke_sessions_on_lock" in m365_payload:
            m365["RevokeSessionsOnLock"] = bool(m365_payload.get("revoke_sessions_on_lock"))

    if notification_payload:
        for src_key, dest_key in (
            ("from_address", "FromAddress"),
            ("from_display_name", "FromDisplayName"),
        ):
            if src_key in notification_payload:
                notification[dest_key] = str(notification_payload.get(src_key) or "").strip()
        if "admin_recipients" in notification_payload:
            value = notification_payload.get("admin_recipients")
            if not isinstance(value, list):
                raise HTTPException(status_code=400, detail="admin_recipients must be a list")
            notification["AdminRecipients"] = [str(item).strip() for item in value if str(item).strip()]

    if dashboard_payload and "base_url" in dashboard_payload:
        dashboard["BaseUrl"] = str(dashboard_payload.get("base_url") or "").strip()

    return config


@router.get("/run-mode")
async def get_run_mode_settings():
    return _serialize_run_mode(_load_runtime_config())


@router.put("/run-mode")
async def update_run_mode(payload: dict = Body(...)):
    requested_mode = str(payload.get("run_mode") or "").strip().lower()
    if requested_mode not in RUN_MODE_DETAILS:
        raise HTTPException(status_code=400, detail="Invalid run mode")

    config = _load_runtime_config()
    config.setdefault("Policy", {})
    config["Policy"]["RunMode"] = requested_mode
    path = _save_runtime_config(config)

    response = _serialize_run_mode(config)
    response["status"] = "saved"
    response["path"] = path
    return response


@router.get("/scope-rules")
async def get_scope_rules():
    config = _load_runtime_config()
    scope = config.get("Scope", {})
    ad = config.get("ActiveDirectory", {})
    return {
        "target_ou": scope.get("TargetOU", ""),
        "search_base": ad.get("SearchBase") or scope.get("TargetOU", ""),
        "search_filter": ad.get("SearchFilter", ""),
        "excluded_users": _scope_list(scope.get("ExcludedUsers")),
        "excluded_groups": _scope_list(scope.get("ExcludedGroups")),
        "whitelisted_users": _scope_list(scope.get("WhitelistedUsers")),
        "include_disabled_accounts": bool(scope.get("IncludeDisabledAccounts", False)),
        "path": _config_path(),
    }


@router.put("/scope-rules")
async def update_scope_rules(payload: dict = Body(...)):
    config = _load_runtime_config()
    scope = config.setdefault("Scope", {})

    for key, config_key in (
        ("excluded_users", "ExcludedUsers"),
        ("excluded_groups", "ExcludedGroups"),
        ("whitelisted_users", "WhitelistedUsers"),
    ):
        value = payload.get(key)
        if value is not None:
            if not isinstance(value, list):
                raise HTTPException(status_code=400, detail=f"{key} must be a list")
            scope[config_key] = [str(item).strip() for item in value if str(item).strip()]

    if "include_disabled_accounts" in payload:
        scope["IncludeDisabledAccounts"] = bool(payload.get("include_disabled_accounts"))

    _save_runtime_config(config)
    response = await get_scope_rules()
    response["status"] = "saved"
    return response


@router.get("/quick-config")
async def get_quick_config():
    return _serialize_quick_config(_load_runtime_config())


@router.get("/secret-health")
async def get_secret_health_view():
    data = _secret_health_summary()
    data["env_path"] = runtime_env_path()
    data["config_path"] = _config_path()
    return data


@router.get("/config-doctor")
async def get_config_doctor():
    config = _load_runtime_config()
    secret_health = _secret_health_summary()
    ad_diag = ActiveDirectoryClient(config).diagnose()
    entra_diag = EntraClient(config).diagnose()
    ldaps_diag = _ldaps_doctor(config)
    sender = config.get("M365", {}).get("ReminderSender") or config.get("Notification", {}).get("FromAddress") or ""

    with get_conn() as c:
        total_users = c.execute("SELECT COUNT(*) FROM users_snapshot").fetchone()[0]
        missing_direct_last_set = c.execute(
            "SELECT COUNT(*) FROM users_snapshot WHERE password_last_set IS NULL AND password_expiry IS NOT NULL"
        ).fetchone()[0]
        derived_last_set = c.execute(
            "SELECT COUNT(*) FROM users_snapshot WHERE status_reason = 'password_last_set_derived_from_expiry'"
        ).fetchone()[0]
        missing_password_dates = c.execute(
            "SELECT COUNT(*) FROM users_snapshot WHERE status_reason = 'missing_password_dates'"
        ).fetchone()[0]

    checks = [
        {
            "key": "config_path",
            "label": "Config path",
            "ok": Path(_config_path()).exists(),
            "summary": _config_path(),
            "suggestion": "Create or mount config/config.json if this path is missing.",
        },
        {
            "key": "env_path",
            "label": "Env path",
            "ok": _runtime_env_available(secret_health),
            "summary": runtime_env_path(),
            "suggestion": "Provide runtime secrets through .env or container environment variables so secrets stay outside config.json.",
        },
        {
            "key": "secrets",
            "label": "Secrets",
            "ok": secret_health["configured"] == secret_health["total"],
            "summary": f'{secret_health["configured"]}/{secret_health["total"]} configured',
            "suggestion": "Fill missing runtime secrets in .env and avoid storing them in config.json.",
        },
        {
            "key": "active_directory",
            "label": "AD bind",
            "ok": ad_diag.ok,
            "summary": ad_diag.summary,
            "suggestion": "Verify AD server, bind user, bind password, port, and search base.",
        },
        {
            "key": "ldaps",
            "label": "LDAPS readiness",
            "ok": ldaps_diag["ok"],
            "summary": ldaps_diag["summary"],
            "suggestion": "Prepare LDAPS certificate and secure bind on the domain controller before password reset actions.",
        },
        {
            "key": "entra",
            "label": "Entra / Graph",
            "ok": entra_diag.ok,
            "summary": entra_diag.summary,
            "suggestion": "Check Tenant ID, Client ID, client secret, and Graph application permissions.",
        },
        {
            "key": "sender",
            "label": "Reminder sender",
            "ok": _looks_like_email(sender),
            "summary": sender or "Missing sender",
            "suggestion": "Set M365.ReminderSender or Notification.FromAddress to a valid mailbox address.",
        },
        {
            "key": "password_dates",
            "label": "Password date coverage",
            "ok": missing_password_dates == 0,
            "summary": f"{missing_password_dates} of {total_users} snapshot rows have no password dates from AD; {derived_last_set} rows use derived last-set; {missing_direct_last_set} rows are still missing direct pwdLastSet despite having expiry.",
            "suggestion": "Run Sync Directory Now after AD changes. If some users still have no expiry and no pwdLastSet, verify AD attributes, fine-grained policy behavior, and whether those accounts are set to never expire.",
        },
    ]
    return {
        "ok": all(check["ok"] for check in checks),
        "version": _runtime_version(),
        "checks": checks,
        "secret_health": secret_health,
    }


@router.put("/quick-config")
async def update_quick_config(payload: dict = Body(...)):
    config = _load_runtime_config()
    config = _apply_quick_config_payload(config, payload)
    _save_runtime_config(config)
    response = _serialize_quick_config(config)
    response["status"] = "saved"
    return response


@router.post("/quick-config/test")
async def test_quick_config(payload: dict = Body(...)):
    config = _load_runtime_config()
    config = _apply_quick_config_payload(config, payload)

    ad_diag = ActiveDirectoryClient(config).diagnose()
    entra_diag = EntraClient(config).diagnose()
    sender = config.get("M365", {}).get("ReminderSender") or config.get("Notification", {}).get("FromAddress")

    sender_note = (
        "Outlook sender display name usually follows the Exchange mailbox display name, "
        "not Notification.FromDisplayName. Change the mailbox display name in Exchange Admin Center "
        "if you want the visible sender name to change."
    )
    return {
        "ok": bool(ad_diag.ok and entra_diag.ok and sender),
        "active_directory": {
            "ok": ad_diag.ok,
            "summary": ad_diag.summary,
            "detail": ad_diag.detail,
        },
        "entra": {
            "ok": entra_diag.ok,
            "summary": entra_diag.summary,
            "detail": entra_diag.detail,
        },
        "sender": {
            "configured_sender": sender,
            "from_display_name": config.get("Notification", {}).get("FromDisplayName", ""),
            "note": sender_note,
        },
    }


@router.get("/email-templates")
async def get_email_templates():
    templates = load_templates()
    defaults = get_default_templates()
    return {
        "path": str(get_template_path()),
        "templates": templates,
        "labels": {key: value["label"] for key, value in defaults.items()},
        "placeholders": [
            "{{display_name}}",
            "{{upn}}",
            "{{days_left}}",
            "{{deadline_text}}",
            "{{gsa_hint_image}}",
        ],
    }


@router.put("/email-templates")
async def update_email_templates(payload: dict = Body(...)):
    existing = load_templates()
    defaults = get_default_templates()
    incoming = payload.get("templates")
    if not isinstance(incoming, dict):
        raise HTTPException(status_code=400, detail="Invalid templates payload")

    for key, value in incoming.items():
        if key not in defaults:
            raise HTTPException(status_code=400, detail=f"Unknown template key: {key}")
        if not isinstance(value, dict):
            raise HTTPException(status_code=400, detail=f"Invalid template body for: {key}")
        subject = value.get("subject", "")
        html = value.get("html", "")
        if not isinstance(subject, str) or not subject.strip():
            raise HTTPException(status_code=400, detail=f"Subject is required for: {key}")
        if not isinstance(html, str) or not html.strip():
            raise HTTPException(status_code=400, detail=f"HTML body is required for: {key}")
        existing[key]["subject"] = subject
        existing[key]["html"] = html

    save_templates({key: {"subject": item["subject"], "html": item["html"]} for key, item in existing.items()})
    return {"status": "saved", "path": str(get_template_path())}


@router.post("/email-templates/reset")
async def reset_email_templates():
    path = reset_templates()
    return {"status": "reset", "path": str(path)}


@router.post("/email-templates/preview")
async def preview_email_template(payload: dict = Body(...)):
    template_key = payload.get("key")
    templates = load_templates()
    if template_key not in templates:
        raise HTTPException(status_code=404, detail="Template not found")
    context = {
        "display_name": payload.get("display_name", "Example User"),
        "upn": payload.get("upn", "user@example.com"),
        "days_left": payload.get("days_left", 7),
        "deadline_text": payload.get("deadline_text", "Tue, 14 Jul 2026 00:00:00 +0700"),
        "gsa_hint_image": get_gsa_hint_image_src(preview=True),
    }
    template = templates[template_key]
    subject = payload.get("subject")
    html = payload.get("html")
    return {
        "subject": render_template(subject if isinstance(subject, str) and subject else template["subject"], context),
        "html": render_template(html if isinstance(html, str) and html else template["html"], context),
    }


@router.post("/email-templates/send-test")
async def send_test_email_template(payload: dict = Body(...)):
    template_key = payload.get("key")
    to_address = payload.get("to")
    if not isinstance(template_key, str) or not template_key:
        raise HTTPException(status_code=400, detail="Template key is required")
    if not isinstance(to_address, str) or "@" not in to_address:
        raise HTTPException(status_code=400, detail="Valid recipient email is required")

    templates = load_templates()
    if template_key not in templates:
        raise HTTPException(status_code=404, detail="Template not found")

    context = {
        "display_name": payload.get("display_name", "Wahyu Prastowo"),
        "upn": payload.get("upn", to_address),
        "days_left": payload.get("days_left", 7),
        "deadline_text": payload.get("deadline_text", "Tue, 14 Jul 2026 00:00:00 +0700"),
        "gsa_hint_image": get_gsa_hint_image_src(preview=False),
    }
    template = templates[template_key]
    subject_source = payload.get("subject")
    html_source = payload.get("html")
    subject = render_template(subject_source if isinstance(subject_source, str) and subject_source else template["subject"], context)
    html = render_template(html_source if isinstance(html_source, str) and html_source else template["html"], context)

    config = _load_runtime_config()
    sender = config.get("M365", {}).get("ReminderSender") or config.get("Notification", {}).get("FromAddress")
    if not sender:
        raise HTTPException(status_code=400, detail="Missing ReminderSender/FromAddress in config")

    result = EntraClient(config).send_mail(
        sender=sender,
        to_recipients=[to_address],
        subject=subject,
        html_body=html,
        cc_recipients=config.get("Notification", {}).get("AdminRecipients", []),
        attachments=get_inline_email_attachments(),
    )
    cycle_key = build_email_cycle_key(
        password_last_set=payload.get("password_last_set"),
        password_expiry=payload.get("password_expiry"),
        sam=(payload.get("sam") or to_address.split("@", 1)[0]),
    )
    log_email_delivery(
        run_id="TEST-EMAIL",
        sam=(payload.get("sam") or to_address.split("@", 1)[0]),
        upn=payload.get("upn", to_address),
        display_name=payload.get("display_name", "Wahyu Prastowo"),
        email=to_address,
        action="TestEmail",
        template_key=template_key,
        days_left=context["days_left"],
        cycle_key=cycle_key,
        password_last_set=payload.get("password_last_set"),
        password_expiry=payload.get("password_expiry"),
        subject=subject,
        status="sent",
        provider_status_code=result.get("status_code"),
        error=None,
    )
    return {"status": "sent", "subject": subject, "result": result}
