import json
import os
from pathlib import Path
from typing import Any


SECRET_SPECS = (
    {
        "env": "PCE_API_TOKEN",
        "path": ("Dashboard", "ApiToken"),
        "placeholder": "CHANGE-ME-WITH-A-LONG-RANDOM-TOKEN",
    },
    {
        "env": "PCE_AD_BIND_PASSWORD",
        "path": ("ActiveDirectory", "BindPassword"),
        "placeholder": "",
    },
    {
        "env": "PCE_M365_CLIENT_SECRET",
        "path": ("M365", "ClientSecret"),
        "placeholder": "",
    },
    {
        "env": "PCE_NOTIFICATION_SMTP_PASSWORD",
        "path": ("Notification", "Password"),
        "placeholder": "",
    },
)


def runtime_env_path() -> str:
    return os.getenv("PCE_ENV_PATH", "/app/.env")


def _parse_env_file(path: str) -> dict[str, str]:
    env_map: dict[str, str] = {}
    file_path = Path(path)
    if not file_path.exists():
        return env_map

    for raw_line in file_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env_map[key.strip()] = value.strip()
    return env_map


def load_runtime_env(env_path: str | None = None) -> dict[str, str]:
    env_path = env_path or runtime_env_path()
    env_map = _parse_env_file(env_path)
    for key, value in env_map.items():
        os.environ.setdefault(key, value)
    return env_map


def _write_env_file(path: str, updates: dict[str, str]) -> None:
    file_path = Path(path)
    existing = _parse_env_file(path)
    existing.update({key: value for key, value in updates.items() if value is not None})

    lines = [f"{key}={value}" for key, value in sorted(existing.items())]
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _nested_get(config: dict[str, Any], path: tuple[str, ...]) -> Any:
    node: Any = config
    for part in path:
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def _nested_set(config: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    node = config
    for part in path[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[path[-1]] = value


def _is_real_secret(value: Any, placeholder: str) -> bool:
    normalized = str(value or "").strip()
    if not normalized:
        return False
    if placeholder and normalized == placeholder:
        return False
    if normalized.startswith("CHANGE-ME"):
        return False
    return True


def _sanitize_config_secret_value(spec: dict[str, Any]) -> str:
    return spec["placeholder"] if spec["env"] == "PCE_API_TOKEN" else ""


def _externalize_config_secrets(
    config: dict[str, Any],
    env_values: dict[str, str],
) -> tuple[dict[str, Any], dict[str, str], bool]:
    changed = False
    env_updates: dict[str, str] = {}

    for spec in SECRET_SPECS:
        env_name = spec["env"]
        config_value = _nested_get(config, spec["path"])
        env_value = os.getenv(env_name) or env_values.get(env_name, "")

        if _is_real_secret(config_value, spec["placeholder"]) and not str(env_value).strip():
            env_updates[env_name] = str(config_value).strip()

        sanitized_value = _sanitize_config_secret_value(spec)
        if str(config_value or "") != sanitized_value:
            if _is_real_secret(config_value, spec["placeholder"]) or str(env_value).strip():
                _nested_set(config, spec["path"], sanitized_value)
                changed = True

    return config, env_updates, changed


def apply_env_secret_overrides(config: dict[str, Any]) -> dict[str, Any]:
    load_runtime_env()
    for spec in SECRET_SPECS:
        value = os.getenv(spec["env"])
        if value is not None and value != "":
            _nested_set(config, spec["path"], value)
    return config


def prepare_runtime_config(
    config_path: str,
    env_path: str | None = None,
) -> dict[str, Any]:
    env_path = env_path or runtime_env_path()
    env_values = load_runtime_env(env_path)

    with open(config_path, "r", encoding="utf-8") as fh:
        config = json.load(fh)

    config, env_updates, changed = _externalize_config_secrets(config, env_values)
    if env_updates:
        _write_env_file(env_path, env_updates)
        for key, value in env_updates.items():
            os.environ[key] = value
        env_values.update(env_updates)

    if changed:
        with open(config_path, "w", encoding="utf-8") as fh:
            json.dump(config, fh, indent=2, ensure_ascii=True)
            fh.write("\n")

    return apply_env_secret_overrides(config)


def save_runtime_config(
    config: dict[str, Any],
    config_path: str,
    env_path: str | None = None,
) -> str:
    env_path = env_path or runtime_env_path()
    env_values = load_runtime_env(env_path)
    sanitized_config, env_updates, _changed = _externalize_config_secrets(config, env_values)

    if env_updates:
        _write_env_file(env_path, env_updates)
        for key, value in env_updates.items():
            os.environ[key] = value

    with open(config_path, "w", encoding="utf-8") as fh:
        json.dump(sanitized_config, fh, indent=2, ensure_ascii=True)
        fh.write("\n")
    return config_path


def get_secret_health(config_path: str, env_path: str | None = None) -> list[dict[str, Any]]:
    env_path = env_path or runtime_env_path()
    env_values = _parse_env_file(env_path)

    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            raw_config = json.load(fh)
    except Exception:
        raw_config = {}

    rows: list[dict[str, Any]] = []
    for spec in SECRET_SPECS:
        env_name = spec["env"]
        env_value = str(os.getenv(env_name) or env_values.get(env_name, "")).strip()
        config_value = str(_nested_get(raw_config, spec["path"]) or "").strip()
        placeholder = spec["placeholder"]

        configured = bool(env_value or _is_real_secret(config_value, placeholder))
        if env_value:
            source = ".env"
        elif _is_real_secret(config_value, placeholder):
            source = "config.json"
        else:
            source = "missing"

        rows.append(
            {
                "key": env_name,
                "configured": configured,
                "source": source,
                "config_path": ".".join(spec["path"]),
            }
        )
    return rows
