import shutil
import tempfile
import unittest
import asyncio
import os
from pathlib import Path
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from dashboard import app as dashboard_app
from dashboard import db as dashboard_db
from dashboard import security as dashboard_security
from dashboard.runner import filetime_to_datetime
from dashboard.secrets import prepare_runtime_config, save_runtime_config
from dashboard.sse import broadcaster


class DashboardSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._temp_dir = Path(tempfile.mkdtemp(prefix="pce-smoke-"))
        cls._original_db_path = dashboard_db.DB_PATH
        cls._original_token = dashboard_security.API_TOKEN

        dashboard_db.DB_PATH = cls._temp_dir / "pce.db"
        dashboard_db.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        dashboard_security.API_TOKEN = "test-token"

        dashboard_db.init_db()
        cls._seed_data()

    @classmethod
    def tearDownClass(cls):
        dashboard_db.DB_PATH = cls._original_db_path
        dashboard_security.API_TOKEN = cls._original_token
        shutil.rmtree(cls._temp_dir, ignore_errors=True)

    @classmethod
    def _seed_data(cls):
        with dashboard_db.get_conn() as conn:
            conn.execute("DELETE FROM email_deliveries")
            conn.execute("DELETE FROM actions")
            conn.execute("DELETE FROM users_snapshot")
            conn.execute("DELETE FROM runs")
            conn.execute("DELETE FROM live_logs")
            conn.execute("DELETE FROM app_state")
            conn.execute(
                """INSERT INTO runs
                   (run_id, started_at, finished_at, total, compliant, warned,
                    forced_change, disabled, errors, whatif, server)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "RUN-001",
                    "2026-07-15T09:00:00+07:00",
                    "2026-07-15T09:05:00+07:00",
                    3,
                    1,
                    1,
                    1,
                    0,
                    0,
                    0,
                    "dc01",
                ),
            )
            conn.execute(
                """INSERT INTO users_snapshot
                   (sam, upn, display_name, email, password_last_set, password_expiry,
                    days_until_expiry, is_locked, is_disabled, must_change_at_logon,
                    status_reason, last_seen)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                (
                    "jdoe",
                    "jdoe@example.com",
                    "John Doe",
                    "jdoe@example.com",
                    "2026-07-01T08:00:00+07:00",
                    "2026-07-20T08:00:00+07:00",
                    5,
                    0,
                    0,
                    0,
                    None,
                ),
            )
            conn.execute(
                """INSERT INTO users_snapshot
                   (sam, upn, display_name, email, password_last_set, password_expiry,
                    days_until_expiry, is_locked, is_disabled, must_change_at_logon,
                    status_reason, last_seen)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                (
                    "asmith",
                    "asmith@example.com",
                    "Alice Smith",
                    "asmith@example.com",
                    "2026-07-01T08:00:00+07:00",
                    "2026-07-15T08:00:00+07:00",
                    0,
                    1,
                    0,
                    1,
                    "must_change_at_next_logon",
                ),
            )
            conn.execute(
                """INSERT INTO actions
                   (run_id, sam, upn, display_name, email, action, days_left)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                ("RUN-001", "jdoe", "jdoe@example.com", "John Doe", "jdoe@example.com", "Warned", 5),
            )
            conn.execute(
                """INSERT INTO email_deliveries
                   (run_id, sam, upn, display_name, email, action, template_key, days_left,
                    cycle_key, password_last_set, password_expiry, subject, status,
                    provider_status_code, error, attempt_no)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "RUN-001",
                    "jdoe",
                    "jdoe@example.com",
                    "John Doe",
                    "jdoe@example.com",
                    "Warned",
                    "warn_7",
                    5,
                    "pls=2026-07-01|pexp=2026-07-20",
                    "2026-07-01T08:00:00+07:00",
                    "2026-07-20T08:00:00+07:00",
                    "Reminder",
                    "sent",
                    202,
                    None,
                    1,
                ),
            )

    def setUp(self):
        self._seed_data()
        self.client_cm = TestClient(dashboard_app.app)
        self.client = self.client_cm.__enter__()
        self.headers = {"X-API-Token": "test-token"}
        broadcaster.clear_history()

    def tearDown(self):
        self.client_cm.__exit__(None, None, None)

    def test_healthz(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "healthy")

    def test_login_page_and_redirect(self):
        response = self.client.get("/login")
        self.assertEqual(response.status_code, 200)
        self.assertIn("PCE Dashboard", response.text)

        protected = self.client.get("/", follow_redirects=False)
        self.assertEqual(protected.status_code, 303)
        self.assertEqual(protected.headers["location"], "/login")

    def test_login_sets_session_cookie(self):
        response = self.client.post("/login", json={"token": "test-token"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertIn("pce_session=", response.headers.get("set-cookie", ""))

    def test_dashboard_pages_with_auth(self):
        self.client.post("/login", json={"token": "test-token"})
        for path in ["/", "/users", "/runs", "/logs", "/settings", "/actions"]:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)

    def test_stats_endpoint(self):
        response = self.client.get("/api/stats/kpi", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["total_users"], 2)
        self.assertEqual(payload["locked"], 1)

    def test_runs_endpoint(self):
        response = self.client.get("/api/runs", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        items = response.json()
        self.assertGreaterEqual(len(items), 1)
        self.assertIn("RUN-001", {item["run_id"] for item in items})

    def test_actions_endpoint(self):
        response = self.client.get("/api/actions", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        items = response.json()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["email_status"], "sent")
        self.assertEqual(items[0]["email_template"], "warn_7")

    def test_users_endpoint(self):
        response = self.client.get("/api/users", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertGreaterEqual(payload["total"], 2)
        self.assertEqual(len(payload["users"]), 2)

    def test_users_endpoint_derives_missing_password_last_set(self):
        with dashboard_db.get_conn() as conn:
            conn.execute(
                "UPDATE users_snapshot SET password_last_set = NULL, status_reason = NULL WHERE sam = ?",
                ("jdoe",),
            )
        response = self.client.get("/api/users", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        users = {user["sam"]: user for user in response.json()["users"]}
        self.assertTrue(users["jdoe"]["password_last_set"])
        self.assertEqual(users["jdoe"]["status_reason"], "password_last_set_derived_from_expiry")

    def test_settings_endpoints(self):
        quick = self.client.get("/api/settings/quick-config", headers=self.headers)
        self.assertEqual(quick.status_code, 200)
        self.assertIn("policy", quick.json())

        mode = self.client.get("/api/settings/run-mode", headers=self.headers)
        self.assertEqual(mode.status_code, 200)
        self.assertIn("mode", mode.json())

        scope = self.client.get("/api/settings/scope-rules", headers=self.headers)
        self.assertEqual(scope.status_code, 200)
        self.assertIn("excluded_users", scope.json())

        secret_health = self.client.get("/api/settings/secret-health", headers=self.headers)
        self.assertEqual(secret_health.status_code, 200)
        self.assertIn("items", secret_health.json())

        doctor = self.client.get("/api/settings/config-doctor", headers=self.headers)
        self.assertEqual(doctor.status_code, 200)
        self.assertIn("checks", doctor.json())

    def test_control_busy_guard(self):
        dashboard_db.set_app_state(
            "policy_status",
            {
                "status": "running",
                "action": "run_policy",
                "started_at": "2026-07-15T09:10:00+07:00",
            },
        )
        response = self.client.post("/api/control/sync-directory", headers=self.headers)
        self.assertEqual(response.status_code, 409)
        self.assertIn("running", response.json()["detail"])
        dashboard_db.set_app_state("policy_status", {"status": "idle"})

    def test_ingest_updates_dashboard_data(self):
        payload = {
            "run_id": "RUN-002",
            "started_at": "2026-07-15T10:00:00+07:00",
            "finished_at": "2026-07-15T10:01:00+07:00",
            "whatif": True,
            "server": "dc02",
            "stats": {
                "Total": 1,
                "Compliant": 0,
                "Warned": 1,
                "ForcedChange": 0,
                "Disabled": 0,
                "Errors": 0,
            },
            "actions": [
                {
                    "User": "newuser",
                    "Upn": "newuser@example.com",
                    "DisplayName": "New User",
                    "Email": "newuser@example.com",
                    "Action": "Warned",
                    "DaysLeft": 2,
                }
            ],
            "users_snapshot": [
                {
                    "SamAccountName": "newuser",
                    "UserPrincipalName": "newuser@example.com",
                    "DisplayName": "New User",
                    "Email": "newuser@example.com",
                    "PasswordLastSet": "2026-07-10T08:00:00+07:00",
                    "PasswordExpiryDate": "2026-07-17T08:00:00+07:00",
                    "DaysUntilExpiry": 2,
                    "IsLocked": False,
                    "IsDisabled": False,
                    "MustChangeAtLogon": False,
                    "StatusReason": None,
                }
            ],
        }

        response = self.client.post("/api/ingest", headers=self.headers, json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

        runs = self.client.get("/api/runs", headers=self.headers)
        self.assertEqual(runs.status_code, 200)
        self.assertEqual(len(runs.json()), 2)

        users = self.client.get("/api/users", headers=self.headers).json()
        self.assertEqual(users["total"], 1)
        self.assertEqual(users["users"][0]["sam"], "newuser")

        actions = self.client.get("/api/actions", headers=self.headers).json()
        self.assertEqual(actions[0]["sam"], "newuser")

    def test_log_history_and_clear(self):
        dashboard_db.append_live_log("run_log", "hello info", level="info")
        dashboard_db.append_live_log("run_log", "watch out", level="warn")

        response = self.client.get("/api/control/log-history?limit=10", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        rows = response.json()
        self.assertGreaterEqual(len(rows), 2)
        self.assertEqual(rows[-1]["message"], "watch out")

        cleared = self.client.post("/api/control/log-history/clear", headers=self.headers)
        self.assertEqual(cleared.status_code, 200)
        self.assertGreaterEqual(cleared.json()["deleted"], 2)

        after = self.client.get("/api/control/log-history?limit=10", headers=self.headers)
        self.assertEqual(after.status_code, 200)
        self.assertEqual(after.json(), [])

    def test_sse_stream_returns_hello_and_history(self):
        asyncio.run(broadcaster.publish("run_log", {"line": "stream history line"}))
        
        class DummyRequest:
            async def is_disconnected(self):
                return False

        async def read_stream_chunks():
            response = await dashboard_app.sse_stream(DummyRequest(), True)
            hello = await response.body_iterator.__anext__()
            history = await response.body_iterator.__anext__()
            await response.body_iterator.aclose()
            return hello + history

        body = asyncio.run(read_stream_chunks())
        self.assertIn("event: hello", body)
        self.assertIn("event: run_log", body)
        self.assertIn("stream history line", body)

    def test_prepare_runtime_config_externalizes_plaintext_secrets(self):
        config_path = self._temp_dir / "secrets-config.json"
        env_path = self._temp_dir / "secrets.env"
        secret_keys = [
            "PCE_API_TOKEN",
            "PCE_AD_BIND_PASSWORD",
            "PCE_M365_CLIENT_SECRET",
            "PCE_NOTIFICATION_SMTP_PASSWORD",
        ]
        original_env = {key: os.environ.get(key) for key in secret_keys}
        for key in secret_keys:
            os.environ.pop(key, None)
        config_path.write_text(
            """{
  "Dashboard": {"ApiToken": "token-123"},
  "ActiveDirectory": {"BindPassword": "ad-secret"},
  "M365": {"ClientSecret": "graph-secret"},
  "Notification": {"Password": "smtp-secret"}
}""",
            encoding="utf-8",
        )

        try:
            loaded = prepare_runtime_config(str(config_path), str(env_path))
            self.assertEqual(loaded["Dashboard"]["ApiToken"], "token-123")
            self.assertEqual(loaded["ActiveDirectory"]["BindPassword"], "ad-secret")
            self.assertEqual(loaded["M365"]["ClientSecret"], "graph-secret")
            self.assertEqual(loaded["Notification"]["Password"], "smtp-secret")

            sanitized = config_path.read_text(encoding="utf-8")
            self.assertNotIn("ad-secret", sanitized)
            self.assertNotIn("graph-secret", sanitized)
            self.assertNotIn("smtp-secret", sanitized)
            self.assertNotIn("token-123", sanitized)

            env_body = env_path.read_text(encoding="utf-8")
            self.assertIn("PCE_API_TOKEN=token-123", env_body)
            self.assertIn("PCE_AD_BIND_PASSWORD=ad-secret", env_body)
            self.assertIn("PCE_M365_CLIENT_SECRET=graph-secret", env_body)
            self.assertIn("PCE_NOTIFICATION_SMTP_PASSWORD=smtp-secret", env_body)
        finally:
            for key, value in original_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_save_runtime_config_moves_secret_fields_to_env(self):
        config_path = self._temp_dir / "save-config.json"
        env_path = self._temp_dir / "save.env"
        secret_keys = [
            "PCE_API_TOKEN",
            "PCE_AD_BIND_PASSWORD",
            "PCE_M365_CLIENT_SECRET",
            "PCE_NOTIFICATION_SMTP_PASSWORD",
        ]
        original_env = {key: os.environ.get(key) for key in secret_keys}
        for key in secret_keys:
            os.environ.pop(key, None)
        saved = {
            "Dashboard": {"ApiToken": "saved-token"},
            "ActiveDirectory": {"BindPassword": "saved-ad"},
            "M365": {"ClientSecret": "saved-graph"},
            "Notification": {"Password": "saved-smtp"},
        }

        try:
            save_runtime_config(saved, str(config_path), str(env_path))
            config_body = config_path.read_text(encoding="utf-8")
            self.assertNotIn("saved-token", config_body)
            self.assertNotIn("saved-ad", config_body)
            self.assertNotIn("saved-graph", config_body)
            self.assertNotIn("saved-smtp", config_body)

            env_body = env_path.read_text(encoding="utf-8")
            self.assertIn("PCE_API_TOKEN=saved-token", env_body)
            self.assertIn("PCE_AD_BIND_PASSWORD=saved-ad", env_body)
            self.assertIn("PCE_M365_CLIENT_SECRET=saved-graph", env_body)
            self.assertIn("PCE_NOTIFICATION_SMTP_PASSWORD=saved-smtp", env_body)
        finally:
            for key, value in original_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_filetime_to_datetime_accepts_datetime_objects(self):
        value = datetime(2026, 7, 16, 9, 30, tzinfo=timezone.utc)
        parsed = filetime_to_datetime(value)
        self.assertEqual(parsed, value)


if __name__ == "__main__":
    unittest.main()
