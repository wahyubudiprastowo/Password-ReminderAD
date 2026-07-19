import argparse
import base64
import json
import os
import platform
import ssl
import socket
import sqlite3
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Any

import requests
from ldap3 import ALL, Connection, MODIFY_REPLACE, Server, SUBTREE, Tls

from .db import build_email_cycle_key, has_sent_template_for_cycle, has_sent_template_today, log_email_delivery, save_run
from .email_templates import (
    get_gsa_hint_image_src,
    get_inline_email_attachments,
    load_templates,
    render_template,
)
from .secrets import prepare_runtime_config


FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)
MIN_PLAUSIBLE_AD_DATE = datetime(2000, 1, 1, tzinfo=timezone.utc)
MAX_PLAUSIBLE_AD_DATE = datetime(2100, 1, 1, tzinfo=timezone.utc)


def load_config(config_path: str) -> dict[str, Any]:
    return prepare_runtime_config(config_path)


def filetime_to_datetime(value: Any) -> datetime | None:
    if value in (None, "", 0, "0"):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        raw = int(value)
    except (TypeError, ValueError):
        return None
    if raw <= 0:
        return None
    try:
        return FILETIME_EPOCH + timedelta(microseconds=raw / 10)
    except OverflowError:
        return None


def raw_filetime_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def iso_or_none(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat()


def is_plausible_ad_datetime(value: datetime | None) -> bool:
    if value is None:
        return False
    return MIN_PLAUSIBLE_AD_DATE <= value <= MAX_PLAUSIBLE_AD_DATE


def parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        normalized = value.strip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def ceil_days_between(future: datetime, now: datetime) -> int:
    delta = future - now
    total_seconds = delta.total_seconds()
    if total_seconds <= 0:
        return 0
    return int((total_seconds + 86399) // 86400)


def derive_password_last_set(expiry: datetime | None, max_password_age_days: int) -> datetime | None:
    if expiry is None or max_password_age_days <= 0:
        return None
    derived = expiry - timedelta(days=max_password_age_days)
    if not is_plausible_ad_datetime(derived):
        return None
    return derived


def decode_jwt_payload(access_token: str) -> dict[str, Any]:
    try:
        payload = access_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload.encode()).decode())
    except Exception:
        return {}


@dataclass
class Diagnostic:
    ok: bool
    summary: str
    detail: dict[str, Any]


class EntraClient:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config["M365"]
        self.token_url = (
            f"https://login.microsoftonline.com/{self.config['TenantId']}/oauth2/v2.0/token"
        )

    def get_token(self) -> dict[str, Any]:
        response = requests.post(
            self.token_url,
            data={
                "client_id": self.config["ClientId"],
                "client_secret": self.config["ClientSecret"],
                "scope": "https://graph.microsoft.com/.default",
                "grant_type": "client_credentials",
            },
            timeout=20,
        )
        response.raise_for_status()
        return response.json()

    def diagnose(self) -> Diagnostic:
        try:
            token_payload = self.get_token()
        except Exception as exc:
            return Diagnostic(False, "Entra token request failed", {"error": str(exc)})

        access_token = token_payload["access_token"]
        jwt_payload = decode_jwt_payload(access_token)
        graph_url = self.config.get("GraphBaseUrl", "https://graph.microsoft.com/v1.0")
        headers = {"Authorization": f"Bearer {access_token}"}
        checks: list[dict[str, Any]] = []
        overall_ok = True

        for name, path in (
            ("organization", "/organization"),
            ("users", "/users?$top=1"),
            ("domains", "/domains"),
        ):
            url = f"{graph_url}{path}"
            try:
                response = requests.get(url, headers=headers, timeout=20)
                body = response.text[:500]
                checks.append(
                    {
                        "name": name,
                        "status_code": response.status_code,
                        "ok": response.ok,
                        "body_preview": body,
                    }
                )
                if not response.ok:
                    overall_ok = False
            except Exception as exc:
                checks.append({"name": name, "ok": False, "error": str(exc)})
                overall_ok = False

        roles = jwt_payload.get("roles", [])
        if overall_ok:
            summary = "Entra token and Graph permissions OK"
        elif not roles:
            summary = "Entra token OK but this app is using delegated permissions; client_credentials requires Application permissions"
        else:
            summary = "Entra token OK but Graph permissions are incomplete"
        return Diagnostic(
            overall_ok,
            summary,
            {
                "tenant_id": self.config["TenantId"],
                "client_id": self.config["ClientId"],
                "token_roles": roles,
                "token_has_scp": bool(jwt_payload.get("scp")),
                "checks": checks,
            },
        )

    def _headers(self) -> dict[str, str]:
        token_payload = self.get_token()
        return {
            "Authorization": f"Bearer {token_payload['access_token']}",
            "Content-Type": "application/json",
        }

    def send_mail(
        self,
        sender: str,
        to_recipients: list[str],
        subject: str,
        html_body: str,
        cc_recipients: list[str] | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if not to_recipients:
            raise ValueError("Missing recipient")
        payload = {
            "message": {
                "subject": subject,
                "body": {
                    "contentType": "HTML",
                    "content": html_body,
                },
                "toRecipients": [
                    {"emailAddress": {"address": recipient}}
                    for recipient in to_recipients
                ],
                "ccRecipients": [
                    {"emailAddress": {"address": recipient}}
                    for recipient in (cc_recipients or [])
                ],
            },
            "saveToSentItems": True,
        }
        if attachments:
            payload["message"]["attachments"] = attachments
        url = f"{self.config.get('GraphBaseUrl', 'https://graph.microsoft.com/v1.0')}/users/{sender}/sendMail"
        response = requests.post(url, headers=self._headers(), json=payload, timeout=20)
        if response.status_code not in (200, 202):
            raise requests.HTTPError(
                f"Graph sendMail failed: {response.status_code} {response.text[:500]}",
                response=response,
            )
        return {
            "ok": True,
            "status_code": response.status_code,
            "sender": sender,
            "to": to_recipients,
            "cc": cc_recipients or [],
        }


class ActiveDirectoryClient:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config["ActiveDirectory"]
        self.scope = config["Scope"]
        self.policy = config["Policy"]

    def diagnose(self) -> Diagnostic:
        missing = [
            key
            for key in ("Server", "BindUser", "BindPassword")
            if not self.config.get(key)
        ]
        if missing:
            return Diagnostic(
                False,
                "Active Directory config incomplete",
                {"missing_fields": missing, "server": self.config.get("Server", "")},
            )

        try:
            resolved = socket.gethostbyname(self.config["Server"])
        except Exception as exc:
            return Diagnostic(
                False,
                "Active Directory host cannot be resolved",
                {"server": self.config["Server"], "error": str(exc)},
            )

        try:
            server = Server(
                self.config["Server"],
                port=int(self.config.get("Port", 636)),
                use_ssl=bool(self.config.get("UseSsl", True)),
                get_info=ALL,
                connect_timeout=int(self.config.get("ConnectTimeoutSeconds", 10)),
                tls=self._tls_config(),
            )
            conn = Connection(
                server,
                user=self.config["BindUser"],
                password=self.config["BindPassword"],
                auto_bind=True,
                receive_timeout=int(self.config.get("ConnectTimeoutSeconds", 10)),
            )
            conn.unbind()
        except Exception as exc:
            return Diagnostic(
                False,
                "Active Directory bind failed",
                {"server": self.config["Server"], "resolved_ip": resolved, "error": str(exc)},
            )

        return Diagnostic(
            True,
            "Active Directory bind OK",
            {
                "server": self.config["Server"],
                "resolved_ip": resolved,
                "search_base": self.config.get("SearchBase") or self.scope["TargetOU"],
                "tls_validation": "strict" if self._validate_server_certificate() else "disabled",
                "ca_cert_file": self.config.get("CaCertFile") or "",
            },
        )

    def _validate_server_certificate(self) -> bool:
        return bool(self.config.get("ValidateServerCertificate", False))

    def _tls_config(self) -> Tls:
        validate_mode = ssl.CERT_REQUIRED if self._validate_server_certificate() else ssl.CERT_NONE
        kwargs: dict[str, Any] = {"validate": validate_mode}
        ca_cert_file = str(self.config.get("CaCertFile") or "").strip()
        if ca_cert_file:
            kwargs["ca_certs_file"] = ca_cert_file
        return Tls(**kwargs)

    def _server(self) -> Server:
        return Server(
            self.config["Server"],
            port=int(self.config.get("Port", 636)),
            use_ssl=bool(self.config.get("UseSsl", True)),
            get_info=ALL,
            connect_timeout=int(self.config.get("ConnectTimeoutSeconds", 10)),
            tls=self._tls_config(),
        )

    def _connection(self) -> Connection:
        return Connection(
            self._server(),
            user=self.config["BindUser"],
            password=self.config["BindPassword"],
            auto_bind=True,
            receive_timeout=int(self.config.get("ConnectTimeoutSeconds", 10)),
        )

    def _password_reset_connection(self) -> Connection:
        timeout = int(self.config.get("ConnectTimeoutSeconds", 10))
        tls = self._tls_config()
        ldaps_only = bool(self.config.get("PasswordResetLdapsOnly", True))

        ldaps_port = int(self.config.get("PasswordResetPort", 636))
        try:
            ldaps_server = Server(
                self.config["Server"],
                port=ldaps_port,
                use_ssl=True,
                get_info=ALL,
                connect_timeout=timeout,
                tls=tls,
            )
            return Connection(
                ldaps_server,
                user=self.config["BindUser"],
                password=self.config["BindPassword"],
                auto_bind=True,
                receive_timeout=timeout,
            )
        except Exception as ldaps_exc:
            if ldaps_only:
                raise RuntimeError(
                    "Password reset requires LDAPS on port "
                    f"{ldaps_port}, but the secure handshake was rejected by the domain controller. "
                    f"LDAPS error: {ldaps_exc}"
                ) from ldaps_exc
            starttls_port = int(self.config.get("PasswordResetStartTlsPort", self.config.get("Port", 389)))
            try:
                starttls_server = Server(
                    self.config["Server"],
                    port=starttls_port,
                    use_ssl=False,
                    get_info=ALL,
                    connect_timeout=timeout,
                    tls=tls,
                )
                conn = Connection(
                    starttls_server,
                    user=self.config["BindUser"],
                    password=self.config["BindPassword"],
                    auto_bind=False,
                    receive_timeout=timeout,
                )
                if not conn.open():
                    raise RuntimeError(str(conn.result))
                if not conn.start_tls():
                    raise RuntimeError(str(conn.result))
                if not conn.bind():
                    raise RuntimeError(str(conn.result))
                return conn
            except Exception as starttls_exc:
                raise RuntimeError(
                    "Password reset secure channel failed. "
                    f"LDAPS 636 error: {ldaps_exc}. "
                    f"StartTLS {starttls_port} error: {starttls_exc}"
                ) from starttls_exc

    def _find_user(self, sam: str) -> dict[str, Any] | None:
        conn = self._connection()
        try:
            search_base = self.config.get("SearchBase") or self.scope["TargetOU"]
            escaped_sam = sam.replace("\\", "\\5c").replace("(", "\\28").replace(")", "\\29").replace("*", "\\2a")
            conn.search(
                search_base,
                f"(&(objectCategory=person)(objectClass=user)(sAMAccountName={escaped_sam}))",
                SUBTREE,
                attributes=[
                    "distinguishedName",
                    "displayName",
                    "userPrincipalName",
                    "mail",
                    "lockoutTime",
                    "pwdLastSet",
                    "msDS-UserPasswordExpiryTimeComputed",
                    "msDS-User-Account-Control-Computed",
                    "userAccountControl",
                ],
            )
            if not conn.entries:
                return None
            entry = conn.entries[0]
            data = entry.entry_attributes_as_dict
            pwd_last_set_value = _first(data.get("pwdLastSet"))
            raw_pwd_last_set = raw_filetime_int(pwd_last_set_value)
            pwd_last_set = filetime_to_datetime(pwd_last_set_value)
            expiry = filetime_to_datetime(_first(data.get("msDS-UserPasswordExpiryTimeComputed")))
            must_change_at_logon = raw_pwd_last_set == 0
            if not is_plausible_ad_datetime(pwd_last_set):
                pwd_last_set = None
            if not is_plausible_ad_datetime(expiry):
                expiry = None
            if expiry is None and pwd_last_set is not None:
                expiry = pwd_last_set + timedelta(days=int(self.policy["MaxPasswordAgeDays"]))
            elif pwd_last_set is None and expiry is not None:
                pwd_last_set = derive_password_last_set(expiry, int(self.policy["MaxPasswordAgeDays"]))
            lockout_value = _first(data.get("lockoutTime")) or 0
            computed_uac = int(_first(data.get("msDS-User-Account-Control-Computed")) or 0)
            user_account_control = int(_first(data.get("userAccountControl")) or 0)
            status_reason = None
            if must_change_at_logon:
                status_reason = "must_change_at_next_logon"
            elif pwd_last_set is None and expiry is None:
                status_reason = "missing_password_dates"
            elif raw_pwd_last_set in (None, 0) and pwd_last_set is not None and expiry is not None:
                status_reason = "password_last_set_derived_from_expiry"
            return {
                "dn": entry.entry_dn,
                "display_name": _first(data.get("displayName")) or sam,
                "upn": _first(data.get("userPrincipalName")),
                "email": _first(data.get("mail")) or _first(data.get("userPrincipalName")),
                "lockout_time": lockout_value,
                "password_last_set": iso_or_none(pwd_last_set),
                "password_expiry": iso_or_none(expiry),
                "days_until_expiry": (expiry - datetime.now(timezone.utc)).days if expiry is not None else None,
                "is_locked": _is_locked_value(lockout_value, computed_uac),
                "must_change_at_logon": must_change_at_logon,
                "status_reason": status_reason,
                "user_account_control": user_account_control,
                "is_disabled": bool(user_account_control & 2),
            }
        finally:
            conn.unbind()

    def fetch_users(self) -> list[dict[str, Any]]:
        conn = self._connection()
        search_base = self.config.get("SearchBase") or self.scope["TargetOU"]
        search_filter = self.config.get("SearchFilter") or "(&(objectCategory=person)(objectClass=user))"
        attributes = [
            "sAMAccountName",
            "userPrincipalName",
            "displayName",
            "mail",
            "pwdLastSet",
            "msDS-UserPasswordExpiryTimeComputed",
            "lockoutTime",
            "msDS-User-Account-Control-Computed",
            "userAccountControl",
            "memberOf",
        ]
        conn.search(search_base, search_filter, SUBTREE, attributes=attributes)
        users: list[dict[str, Any]] = []
        excluded_users = {str(item).strip().lower() for item in self.scope.get("ExcludedUsers", []) if str(item).strip()}
        excluded_groups = {str(item).strip().lower() for item in self.scope.get("ExcludedGroups", []) if str(item).strip()}
        whitelisted_users = {str(item).strip().lower() for item in self.scope.get("WhitelistedUsers", []) if str(item).strip()}
        now = datetime.now(timezone.utc)

        for entry in conn.entries:
            data = entry.entry_attributes_as_dict
            sam = _first(data.get("sAMAccountName"))
            if not sam:
                continue
            display_name = _first(data.get("displayName")) or sam
            upn = _first(data.get("userPrincipalName"))
            email = _first(data.get("mail")) or upn
            identities = _normalized_identities(sam, upn, email, entry.entry_dn)
            member_of = _extract_group_names(data.get("memberOf"))
            is_whitelisted = bool(identities & whitelisted_users)
            if not is_whitelisted:
                if identities & excluded_users:
                    continue
                if member_of & excluded_groups:
                    continue
            pwd_last_set_value = _first(data.get("pwdLastSet"))
            raw_pwd_last_set = raw_filetime_int(pwd_last_set_value)
            pwd_last_set = filetime_to_datetime(pwd_last_set_value)
            expiry = filetime_to_datetime(_first(data.get("msDS-UserPasswordExpiryTimeComputed")))
            must_change_at_logon = raw_pwd_last_set == 0
            if not is_plausible_ad_datetime(pwd_last_set):
                pwd_last_set = None
            if not is_plausible_ad_datetime(expiry):
                expiry = None
            if expiry is None and pwd_last_set is not None:
                expiry = pwd_last_set + timedelta(days=int(self.policy["MaxPasswordAgeDays"]))
            elif pwd_last_set is None and expiry is not None:
                pwd_last_set = derive_password_last_set(expiry, int(self.policy["MaxPasswordAgeDays"]))
            lockout_value = _first(data.get("lockoutTime")) or 0
            computed_uac = int(_first(data.get("msDS-User-Account-Control-Computed")) or 0)
            user_account_control = int(_first(data.get("userAccountControl")) or 0)
            is_disabled = bool(user_account_control & 2)
            is_locked = _is_locked_value(lockout_value, computed_uac)
            days = (expiry - now).days if expiry is not None else None
            status_reason = None
            if must_change_at_logon:
                status_reason = "must_change_at_next_logon"
            elif pwd_last_set is None and expiry is None:
                status_reason = "missing_password_dates"
            elif raw_pwd_last_set in (None, 0) and pwd_last_set is not None and expiry is not None:
                status_reason = "password_last_set_derived_from_expiry"
            users.append(
                {
                    "DistinguishedName": entry.entry_dn,
                    "SamAccountName": sam,
                    "UserPrincipalName": upn,
                    "DisplayName": display_name,
                    "Email": email,
                    "PasswordLastSet": iso_or_none(pwd_last_set),
                    "PasswordExpiryDate": iso_or_none(expiry),
                    "DaysUntilExpiry": days,
                    "ActualDaysUntilExpiry": days,
                    "IsLocked": is_locked,
                    "IsDisabled": is_disabled,
                    "MustChangeAtLogon": must_change_at_logon,
                    "StatusReason": status_reason,
                }
            )

        conn.unbind()
        return users

    def unlock_expired_locked_users(self) -> dict[str, Any]:
        conn = self._connection()

        users = self.fetch_users()
        targets = [u for u in users if u.get("IsLocked") and isinstance(u.get("DaysUntilExpiry"), int) and u["DaysUntilExpiry"] < 0]
        unlocked: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []

        for user in targets:
            dn = user.get("DistinguishedName")
            if not dn:
                failed.append({"sam": user["SamAccountName"], "error": "Missing distinguished name"})
                continue
            ok = conn.modify(dn, {"lockoutTime": [(MODIFY_REPLACE, [0])]})
            if ok:
                unlocked.append(
                    {
                        "sam": user["SamAccountName"],
                        "upn": user["UserPrincipalName"],
                        "days_until_expiry": user["DaysUntilExpiry"],
                    }
                )
            else:
                failed.append(
                    {
                        "sam": user["SamAccountName"],
                        "upn": user["UserPrincipalName"],
                        "error": str(conn.result),
                    }
                )

        conn.unbind()
        return {
            "target_count": len(targets),
            "unlocked_count": len(unlocked),
            "failed_count": len(failed),
            "unlocked": unlocked,
            "failed": failed,
        }

    def admin_set_password(
        self,
        sam: str,
        new_password: str,
        must_change_at_next_logon: bool = True,
        unlock_if_locked: bool = False,
    ) -> dict[str, Any]:
        if not sam:
            raise ValueError("Missing sam")
        if not new_password or len(new_password) < 8:
            raise ValueError("Password minimum 8 characters")

        user = self._find_user(sam)
        if not user:
            raise ValueError(f"User not found: {sam}")

        try:
            conn = self._password_reset_connection()
        except Exception as exc:
            raise RuntimeError(str(exc)) from exc
        try:
            dn = user["dn"]
            try:
                password_ok = conn.extend.microsoft.modify_password(dn, new_password)
            except Exception as exc:
                raise RuntimeError(
                    "Password reset requires secure LDAPS channel on port 636. "
                    f"Current reset path to {self.config['Server']}:"
                    f"{self.config.get('PasswordResetPort', 636)} failed: {exc}"
                ) from exc
            if not password_ok:
                raise RuntimeError(
                    "Password reset was rejected by Active Directory. "
                    f"Check LDAPS/reset permissions. Result: {conn.result}"
                )

            must_change_ok = True
            if must_change_at_next_logon:
                must_change_ok = conn.modify(dn, {"pwdLastSet": [(MODIFY_REPLACE, [0])]})
                if not must_change_ok:
                    raise RuntimeError(f"Password changed but pwdLastSet update failed: {conn.result}")

            unlocked = False
            if unlock_if_locked:
                unlocked = conn.modify(dn, {"lockoutTime": [(MODIFY_REPLACE, [0])]})
                if not unlocked:
                    raise RuntimeError(f"Password changed but unlock failed: {conn.result}")

            return {
                "ok": True,
                "sam": sam,
                "display_name": user["display_name"],
                "upn": user["upn"],
                "email": user["email"],
                "must_change_at_next_logon": must_change_at_next_logon,
                "unlock_if_locked": unlock_if_locked,
                "unlocked": unlocked,
                "result": conn.result,
            }
        finally:
            conn.unbind()

    def force_change_at_next_logon(self, sam: str) -> dict[str, Any]:
        user = self._find_user(sam)
        if not user:
            raise ValueError(f"User not found: {sam}")
        conn = self._connection()
        try:
            ok = conn.modify(user["dn"], {"pwdLastSet": [(MODIFY_REPLACE, [0])]})
            if not ok:
                raise RuntimeError(str(conn.result))
            return {
                "ok": True,
                "sam": sam,
                "display_name": user["display_name"],
                "upn": user["upn"],
                "email": user["email"],
                "result": conn.result,
            }
        finally:
            conn.unbind()

    def unlock_user(self, sam: str) -> dict[str, Any]:
        user = self._find_user(sam)
        if not user:
            raise ValueError(f"User not found: {sam}")
        conn = self._connection()
        try:
            ok = conn.modify(user["dn"], {"lockoutTime": [(MODIFY_REPLACE, [0])]})
            if not ok:
                raise RuntimeError(str(conn.result))
            return {
                "ok": True,
                "sam": sam,
                "display_name": user["display_name"],
                "upn": user["upn"],
                "email": user["email"],
                "result": conn.result,
            }
        finally:
            conn.unbind()

    def disable_user(self, sam: str) -> dict[str, Any]:
        user = self._find_user(sam)
        if not user:
            raise ValueError(f"User not found: {sam}")
        conn = self._connection()
        try:
            current_uac = int(user.get("user_account_control") or 0)
            disabled_uac = current_uac | 0x0002
            ok = conn.modify(user["dn"], {"userAccountControl": [(MODIFY_REPLACE, [disabled_uac])]})
            if not ok:
                raise RuntimeError(str(conn.result))
            return {
                "ok": True,
                "sam": sam,
                "display_name": user["display_name"],
                "upn": user["upn"],
                "email": user["email"],
                "user_account_control": disabled_uac,
                "result": conn.result,
            }
        finally:
            conn.unbind()

    def enable_user(self, sam: str) -> dict[str, Any]:
        user = self._find_user(sam)
        if not user:
            raise ValueError(f"User not found: {sam}")
        conn = self._connection()
        try:
            current_uac = int(user.get("user_account_control") or 0)
            enabled_uac = current_uac & ~0x0002
            ok = conn.modify(user["dn"], {"userAccountControl": [(MODIFY_REPLACE, [enabled_uac])]})
            if not ok:
                raise RuntimeError(str(conn.result))
            return {
                "ok": True,
                "sam": sam,
                "display_name": user["display_name"],
                "upn": user["upn"],
                "email": user["email"],
                "user_account_control": enabled_uac,
                "result": conn.result,
            }
        finally:
            conn.unbind()


def apply_temporary_grace(
    config: dict[str, Any],
    users_snapshot: list[dict[str, Any]],
    now: datetime,
) -> list[dict[str, Any]]:
    policy = config.get("Policy", {})
    grace_days = int(policy.get("TemporaryExpiredGraceDays") or 0)
    grace_start = parse_datetime(policy.get("TemporaryExpiredGraceStart"))
    if grace_days <= 0 or grace_start is None:
        return users_snapshot

    grace_end = grace_start + timedelta(days=grace_days)
    effective_users: list[dict[str, Any]] = []
    for user in users_snapshot:
        clone = dict(user)
        actual_days = clone.get("DaysUntilExpiry")
        clone["ActualDaysUntilExpiry"] = actual_days
        if isinstance(actual_days, int) and actual_days < 0 and not clone.get("IsLocked"):
            if now < grace_end:
                clone["DaysUntilExpiry"] = ceil_days_between(grace_end, now)
                clone["GraceApplied"] = True
                clone["GraceEndsAt"] = iso_or_none(grace_end)
        effective_users.append(clone)
    return effective_users


def _first(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        return value[0] if value else None
    return value


def _is_locked_value(value: Any, computed_uac: int = 0) -> bool:
    if computed_uac & 0x0010:
        return True
    if value in (None, "", 0, "0"):
        return False
    if isinstance(value, datetime):
        normalized = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return normalized > FILETIME_EPOCH
    try:
        return int(value) > 0
    except (TypeError, ValueError):
        return bool(value)


def _normalized_identities(*values: Any) -> set[str]:
    identities: set[str] = set()
    for value in values:
        if not value:
            continue
        if isinstance(value, (list, tuple, set)):
            for item in value:
                identities.update(_normalized_identities(item))
            continue
        text = str(value).strip().lower()
        if text:
            identities.add(text)
    return identities


def _extract_group_names(member_of: Any) -> set[str]:
    names: set[str] = set()
    values = member_of if isinstance(member_of, (list, tuple, set)) else [member_of]
    for value in values:
        if not value:
            continue
        dn = str(value).strip()
        if not dn:
            continue
        names.add(dn.lower())
        first = dn.split(",", 1)[0].strip()
        if first.upper().startswith("CN="):
            names.add(first[3:].strip().lower())
    return names


def get_run_mode(config: dict[str, Any]) -> str:
    raw = str(config.get("Policy", {}).get("RunMode") or "enforcement").strip().lower()
    if raw not in {"monitoring_only", "reminder_only", "enforcement"}:
        return "enforcement"
    return raw


def build_payload(config: dict[str, Any], users_snapshot: list[dict[str, Any]], whatif: bool) -> dict[str, Any]:
    stats = {
        "Total": len(users_snapshot),
        "Compliant": 0,
        "Warned": 0,
        "ForcedChange": 0,
        "Disabled": 0,
        "Locked": 0,
        "Errors": 0,
    }
    actions: list[dict[str, Any]] = []
    warning_days = set(int(day) for day in config["Policy"]["WarningDays"])
    force_day = int(config["Policy"]["ForceChangeAtLogonOnDay"])
    grace_days = int(config["Policy"]["GracePeriodDaysAfterExpiry"])
    after_grace_action = str(config["Policy"].get("ActionAfterGrace") or "Disable").strip().lower()

    for user in users_snapshot:
        days_raw = user["DaysUntilExpiry"]
        days = int(days_raw) if days_raw is not None else None
        action = None
        if user.get("IsDisabled"):
            stats["Disabled"] += 1
            continue
        if days is None:
            continue
        if user["IsLocked"]:
            stats["Locked"] += 1
            continue
        if days > max(warning_days):
            stats["Compliant"] += 1
        elif days > 0 and days in warning_days:
            stats["Warned"] += 1
            action = "Warned"
        elif days <= force_day and days > -grace_days:
            stats["ForcedChange"] += 1
            action = "ForcedChange"
        elif days <= -grace_days:
            if after_grace_action == "forcechange":
                stats["ForcedChange"] += 1
                action = "ForcedChange"
            elif after_grace_action in ("none", "monitor", "monitoring_only"):
                stats["Compliant"] += 1
            else:
                stats["Disabled"] += 1
                action = "Disabled"
        else:
            stats["Compliant"] += 1

        if action:
            actions.append(
                {
                    "User": user["SamAccountName"],
                    "Upn": user["UserPrincipalName"],
                    "DisplayName": user["DisplayName"],
                    "Email": user["Email"],
                    "Action": action,
                    "DaysLeft": days,
                }
            )

    now = datetime.now(timezone.utc).isoformat()
    return {
        "run_id": f"LDAP-{uuid.uuid4().hex[:10].upper()}",
        "started_at": now,
        "finished_at": now,
        "whatif": whatif,
        "server": platform.node() or "docker",
        "stats": stats,
        "actions": actions,
        "users_snapshot": users_snapshot,
    }


def build_password_notification_email(
    display_name: str,
    upn: str,
    days_left: int | None,
    grace_end: datetime | None = None,
    grace_mode: bool = False,
) -> tuple[str, str, str]:
    deadline_text = format_datetime(grace_end) if grace_end else "sesuai kebijakan IT yang berlaku"
    template_key = "expired" if grace_mode or (days_left is not None and days_left < 0) else f"warn_{days_left}"
    templates = load_templates()
    template = templates.get(template_key)
    if template is None:
        template_key = "expired"
        template = templates["expired"]
    context = {
        "display_name": display_name,
        "upn": upn,
        "days_left": days_left if days_left is not None else "",
        "deadline_text": deadline_text,
        "gsa_hint_image": get_gsa_hint_image_src(preview=False),
    }
    return render_template(template["html"], context), render_template(template["subject"], context), template_key


def send_notification_emails(config: dict[str, Any], payload: dict[str, Any], users_snapshot: list[dict[str, Any]]) -> tuple[int, int]:
    sender = config.get("M365", {}).get("ReminderSender") or config.get("Notification", {}).get("FromAddress")
    if not sender or not config.get("M365", {}).get("Enabled"):
        print("[MAIL] Skipped: sender or M365 config is not enabled")
        return 0, 0

    snapshot_map = {user.get("SamAccountName"): user for user in users_snapshot}
    entra = EntraClient(config)
    sent = 0
    failed = 0

    for action in payload.get("actions", []):
        if action.get("Action") not in ("Warned", "ForcedChange"):
            continue
        sam = action.get("User")
        display_name = action.get("DisplayName") or sam
        upn = action.get("Upn")
        email = action.get("Email") or upn
        days_left = action.get("DaysLeft")
        user_snapshot = snapshot_map.get(sam, {})
        cycle_key = build_email_cycle_key(
            password_last_set=user_snapshot.get("PasswordLastSet"),
            password_expiry=user_snapshot.get("PasswordExpiryDate"),
            sam=sam,
        )
        grace_end = parse_datetime(user_snapshot.get("GraceEndsAt"))
        grace_mode = bool(user_snapshot.get("GraceApplied")) or (isinstance(days_left, int) and days_left <= 0)

        if not email:
            attempt_no = log_email_delivery(
                run_id=payload["run_id"],
                sam=sam,
                upn=upn,
                display_name=display_name,
                email=email,
                action=action.get("Action"),
                template_key="unknown",
                days_left=days_left,
                cycle_key=cycle_key,
                password_last_set=user_snapshot.get("PasswordLastSet"),
                password_expiry=user_snapshot.get("PasswordExpiryDate"),
                subject="",
                status="skipped",
                error="Missing email address",
            )
            print(f"[MAIL] SKIPPED | user={sam} | attempt={attempt_no} | reason=missing_email")
            failed += 1
            continue

        body, subject, template_key = build_password_notification_email(
            display_name=display_name,
            upn=upn or email,
            days_left=days_left,
            grace_end=grace_end,
            grace_mode=grace_mode,
        )
        if has_sent_template_for_cycle(sam, template_key, cycle_key, actions=("Warned", "ForcedChange")):
            attempt_no = log_email_delivery(
                run_id=payload["run_id"],
                sam=sam,
                upn=upn,
                display_name=display_name,
                email=email,
                action=action.get("Action"),
                template_key=template_key,
                days_left=days_left,
                cycle_key=cycle_key,
                password_last_set=user_snapshot.get("PasswordLastSet"),
                password_expiry=user_snapshot.get("PasswordExpiryDate"),
                subject=subject,
                status="skipped",
                error="Template already sent for this password cycle",
            )
            print(
                f"[MAIL] SKIPPED | user={sam} | template={template_key} | attempt={attempt_no} | reason=already_sent_this_cycle"
            )
            continue
        if has_sent_template_today(sam, template_key, actions=("Warned", "ForcedChange")):
            attempt_no = log_email_delivery(
                run_id=payload["run_id"],
                sam=sam,
                upn=upn,
                display_name=display_name,
                email=email,
                action=action.get("Action"),
                template_key=template_key,
                days_left=days_left,
                cycle_key=cycle_key,
                password_last_set=user_snapshot.get("PasswordLastSet"),
                password_expiry=user_snapshot.get("PasswordExpiryDate"),
                subject=subject,
                status="skipped",
                error="Template already sent today for this user",
            )
            print(
                f"[MAIL] SKIPPED | user={sam} | template={template_key} | attempt={attempt_no} | reason=already_sent_today"
            )
            continue
        try:
            result = entra.send_mail(
                sender=sender,
                to_recipients=[email],
                subject=subject,
                html_body=body,
                cc_recipients=[],
                attachments=get_inline_email_attachments(),
            )
            attempt_no = log_email_delivery(
                run_id=payload["run_id"],
                sam=sam,
                upn=upn,
                display_name=display_name,
                email=email,
                action=action.get("Action"),
                template_key=template_key,
                days_left=days_left,
                cycle_key=cycle_key,
                password_last_set=user_snapshot.get("PasswordLastSet"),
                password_expiry=user_snapshot.get("PasswordExpiryDate"),
                subject=subject,
                status="sent",
                provider_status_code=result.get("status_code"),
                error=None,
            )
            sent += 1
            print(
                f"[MAIL] SENT | user={sam} | template={template_key} | attempt={attempt_no} | status={result.get('status_code')} | to={email}"
            )
        except Exception as exc:
            attempt_no = log_email_delivery(
                run_id=payload["run_id"],
                sam=sam,
                upn=upn,
                display_name=display_name,
                email=email,
                action=action.get("Action"),
                template_key=template_key,
                days_left=days_left,
                cycle_key=cycle_key,
                password_last_set=user_snapshot.get("PasswordLastSet"),
                password_expiry=user_snapshot.get("PasswordExpiryDate"),
                subject=subject,
                status="failed",
                provider_status_code=getattr(getattr(exc, "response", None), "status_code", None),
                error=str(exc),
            )
            failed += 1
            print(f"[MAIL] FAILED | user={sam} | template={template_key} | attempt={attempt_no} | error={exc}")

    print(f"[MAIL] SUMMARY | sent={sent} | failed={failed}")
    return sent, failed


def execute_directory_actions(
    config: dict[str, Any],
    payload: dict[str, Any],
) -> tuple[int, int]:
    ad = ActiveDirectoryClient(config)
    success = 0
    failed = 0
    disable_limit = int(config.get("Safety", {}).get("MaxDisablesPerRun") or 0)
    disabled_count = 0

    for action in payload.get("actions", []):
        action_type = action.get("Action")
        if action_type not in ("ForcedChange", "Disabled"):
            continue
        sam = action.get("User")
        if not sam:
            continue

        if action_type == "Disabled" and disable_limit > 0 and disabled_count >= disable_limit:
            failed += 1
            payload["stats"]["Errors"] += 1
            print(f"[POLICY] SKIPPED | user={sam} | action=Disabled | reason=max_disable_limit_reached")
            continue

        try:
            if action_type == "ForcedChange":
                ad.force_change_at_next_logon(sam)
            elif action_type == "Disabled":
                ad.disable_user(sam)
                disabled_count += 1
            success += 1
            print(f"[POLICY] APPLIED | user={sam} | action={action_type}")
        except Exception as exc:
            failed += 1
            payload["stats"]["Errors"] += 1
            print(f"[POLICY] FAILED | user={sam} | action={action_type} | error={exc}")

    print(f"[POLICY] SUMMARY | applied={success} | failed={failed} | disabled_limit={disable_limit}")
    return success, failed


def run_scan(config_path: str, whatif: bool) -> int:
    config = load_config(config_path)
    ad = ActiveDirectoryClient(config)
    run_mode = get_run_mode(config)
    ad_diag = ad.diagnose()
    print(f"[AD] {ad_diag.summary}")
    if not ad_diag.ok:
        print(json.dumps(ad_diag.detail, indent=2))
        return 2

    entra_diag = EntraClient(config).diagnose() if config.get("M365", {}).get("Enabled") else None
    if entra_diag is not None:
        print(f"[ENTRA] {entra_diag.summary}")
        if not entra_diag.ok:
            print(json.dumps(entra_diag.detail, indent=2))

    print(f"[POLICY] Run mode: {run_mode}")
    users_snapshot = apply_temporary_grace(config, ad.fetch_users(), datetime.now(timezone.utc))
    payload = build_payload(config, users_snapshot, whatif)
    if not whatif:
        if run_mode == "monitoring_only":
            print("[POLICY] Monitoring only mode active: no email and no directory action executed")
        else:
            sent, failed = send_notification_emails(config, payload, users_snapshot)
            payload["stats"]["Errors"] += failed
            print(json.dumps({"mail_sent": sent, "mail_failed": failed}, indent=2))
            if run_mode == "enforcement":
                applied, action_failed = execute_directory_actions(config, payload)
                print(json.dumps({"directory_actions_applied": applied, "directory_actions_failed": action_failed}, indent=2))
            else:
                print("[POLICY] Reminder only mode active: directory enforcement skipped")
        users_snapshot = apply_temporary_grace(config, ad.fetch_users(), datetime.now(timezone.utc))
        payload["users_snapshot"] = users_snapshot
    save_run(payload)
    print(
        json.dumps(
            {
                "run_id": payload["run_id"],
                "users": len(users_snapshot),
                "stats": payload["stats"],
            },
            indent=2,
        )
    )
    return 0


def run_diagnostics(config_path: str) -> int:
    config = load_config(config_path)
    result = {
        "active_directory": ActiveDirectoryClient(config).diagnose().__dict__,
        "entra": EntraClient(config).diagnose().__dict__ if config.get("M365", {}).get("Enabled") else None,
    }
    print(json.dumps(result, indent=2))
    return 0 if result["active_directory"]["ok"] and (result["entra"] is None or result["entra"]["ok"]) else 1


def run_unlock_expired(config_path: str) -> int:
    config = load_config(config_path)
    ad = ActiveDirectoryClient(config)
    ad_diag = ad.diagnose()
    if not ad_diag.ok:
        print(json.dumps({"active_directory": ad_diag.__dict__}, indent=2))
        return 1
    result = ad.unlock_expired_locked_users()
    print(json.dumps(result, indent=2))
    return 0 if result["failed_count"] == 0 else 1


def run_test_expired_email(config_path: str, to_address: str) -> int:
    config = load_config(config_path)
    sender = config.get("M365", {}).get("ReminderSender") or config.get("Notification", {}).get("FromAddress")
    if not sender:
        print(json.dumps({"ok": False, "error": "Missing ReminderSender/FromAddress"}, indent=2))
        return 1

    body, subject = build_password_notification_email(
        display_name="Wahyu Prastowo",
        upn=to_address,
        days_left=-1,
        grace_end=None,
        grace_mode=True,
    )

    result = EntraClient(config).send_mail(
        sender=sender,
        to_recipients=[to_address],
        subject=subject,
        html_body=body,
        cc_recipients=config.get("Notification", {}).get("AdminRecipients", []),
    )
    print(json.dumps(result, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="PCE Linux-native runner")
    parser.add_argument("--config", default=os.getenv("PCE_CONFIG_PATH", "/app/config/config.json"))
    parser.add_argument("--whatif", action="store_true")
    parser.add_argument("--diagnose", action="store_true")
    parser.add_argument("--unlock-expired", action="store_true")
    parser.add_argument("--test-expired-email", action="store_true")
    parser.add_argument("--to")
    args = parser.parse_args()

    if args.diagnose:
        return run_diagnostics(args.config)
    if args.unlock_expired:
        return run_unlock_expired(args.config)
    if args.test_expired_email:
        if not args.to:
            print(json.dumps({"ok": False, "error": "Missing --to recipient"}, indent=2))
            return 1
        return run_test_expired_email(args.config, args.to)
    return run_scan(args.config, args.whatif)


if __name__ == "__main__":
    sys.exit(main())
