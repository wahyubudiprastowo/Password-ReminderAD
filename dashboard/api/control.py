import asyncio, os, sys
from fastapi import APIRouter, BackgroundTasks, HTTPException
from datetime import datetime, timezone
from ..db import (
    append_live_log,
    clear_live_logs,
    get_app_state,
    recent_live_logs,
    set_app_state,
)
from ..sse import broadcaster

router = APIRouter()
PS_SCRIPT = os.getenv("PCE_INVOKE_PATH", r"C:\ProgramData\PCE\Invoke-PCE.ps1")
PS_EXE = os.getenv("PCE_PS_EXE", "powershell.exe")
RUNNER_MODE = os.getenv("PCE_RUNNER_MODE", "python")
CONFIG_PATH = os.getenv("PCE_CONFIG_PATH", "/app/config/config.json")


def _build_args(whatif: bool, diagnose: bool = False):
    if RUNNER_MODE == "python":
        args = [sys.executable, "-m", "dashboard.runner", "--config", CONFIG_PATH]
        if diagnose:
            args.append("--diagnose")
        if whatif:
            args.append("--whatif")
        return args

    args = [PS_EXE, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", PS_SCRIPT]
    if whatif:
        args.append("-WhatIf")
    return args


def _build_unlock_args():
    if RUNNER_MODE == "python":
        return [sys.executable, "-m", "dashboard.runner", "--config", CONFIG_PATH, "--unlock-expired"]
    raise RuntimeError("Unlock expired users is only supported in python runner mode")


async def _publish_event(event_type, data):
    await broadcaster.publish(event_type, data)


async def _publish_log_line(message, level="info", run_id=None):
    append_live_log("run_log", message, level=level, run_id=run_id)
    await _publish_event("run_log", {"line": message, "level": level, "run_id": run_id})


def _is_state_running(state_key):
    state = get_app_state(state_key, {}) or {}
    return isinstance(state, dict) and state.get("status") == "running"


def _ensure_idle(action_label):
    running = []
    if _is_state_running("policy_status"):
        running.append("policy")
    if _is_state_running("sync_directory_status"):
        running.append("sync-directory")

    if running:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot start {action_label} while another background task is still "
                f"running: {', '.join(running)}."
            ),
        )

async def _run_pce(whatif):
    args = _build_args(whatif)
    run_id = None
    started_at = datetime.now(timezone.utc).isoformat()
    set_app_state("policy_status", {
        "status": "running",
        "action": "test_policy" if whatif else "run_policy",
        "started_at": started_at,
        "finished_at": None,
        "exit_code": None,
        "error": None,
    })
    append_live_log("run_started", f"{'TEST POLICY' if whatif else 'RUN POLICY'} STARTED | whatif={whatif} | cmd={' '.join(args)}", level="ok")
    await _publish_event("run_started", {"whatif": whatif, "cmd": " ".join(args)})
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode(errors="ignore").rstrip()
            level = "err" if any(token in text.lower() for token in ("error", "failed", "exception")) else "warn" if any(token in text.lower() for token in ("warn", "skip")) else "ok" if any(token in text.lower() for token in ("success", "sent", "completed", "[ad]", "[entra]")) else "info"
            await _publish_log_line(text, level=level, run_id=run_id)
        await proc.wait()
        set_app_state("policy_status", {
            "status": "success" if proc.returncode == 0 else "failed",
            "action": "test_policy" if whatif else "run_policy",
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "exit_code": proc.returncode,
            "error": None if proc.returncode == 0 else f"Process exited with code {proc.returncode}",
        })
        append_live_log("run_finished", f"{'TEST POLICY' if whatif else 'RUN POLICY'} FINISHED | exit={proc.returncode}", level="ok" if proc.returncode == 0 else "err", run_id=run_id)
        await _publish_event("run_finished", {"exit_code": proc.returncode, "run_id": run_id})
        await _publish_event("run_completed", {"exit_code": proc.returncode, "run_id": run_id, "whatif": whatif})
    except Exception as e:
        set_app_state("policy_status", {
            "status": "failed",
            "action": "test_policy" if whatif else "run_policy",
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "exit_code": -1,
            "error": str(e),
        })
        append_live_log("run_finished", f"{'TEST POLICY' if whatif else 'RUN POLICY'} FINISHED | exit=-1 | error={e}", level="err", run_id=run_id)
        await _publish_event("run_finished", {"exit_code": -1, "error": str(e), "run_id": run_id})


async def _run_sync_directory():
    args = _build_args(True)
    started_at = datetime.now(timezone.utc).isoformat()
    set_app_state("sync_directory_status", {
        "status": "running",
        "action": "sync_directory",
        "started_at": started_at,
        "finished_at": None,
        "exit_code": None,
        "error": None,
    })
    append_live_log("run_started", f"SYNC DIRECTORY STARTED | cmd={' '.join(args)}", level="ok")
    await _publish_event("run_started", {"sync_directory": True, "cmd": " ".join(args)})
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode(errors="ignore").rstrip()
            level = "err" if any(token in text.lower() for token in ("error", "failed", "exception")) else "warn" if any(token in text.lower() for token in ("warn", "skip")) else "ok" if any(token in text.lower() for token in ("success", "sent", "completed", "[ad]", "[entra]")) else "info"
            await _publish_log_line(text, level=level)
        await proc.wait()
        set_app_state("sync_directory_status", {
            "status": "success" if proc.returncode == 0 else "failed",
            "action": "sync_directory",
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "exit_code": proc.returncode,
            "error": None if proc.returncode == 0 else f"Process exited with code {proc.returncode}",
        })
        append_live_log("run_finished", f"SYNC DIRECTORY FINISHED | exit={proc.returncode}", level="ok" if proc.returncode == 0 else "err")
        await _publish_event("run_finished", {"exit_code": proc.returncode, "sync_directory": True})
        await _publish_event("run_completed", {"exit_code": proc.returncode, "sync_directory": True, "whatif": True})
    except Exception as e:
        set_app_state("sync_directory_status", {
            "status": "failed",
            "action": "sync_directory",
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "exit_code": -1,
            "error": str(e),
        })
        append_live_log("run_finished", f"SYNC DIRECTORY FINISHED | exit=-1 | error={e}", level="err")
        await _publish_event("run_finished", {"exit_code": -1, "error": str(e), "sync_directory": True})


async def _run_unlock_expired():
    unlock_args = _build_unlock_args()
    append_live_log("run_started", f"UNLOCK EXPIRED STARTED | cmd={' '.join(unlock_args)}", level="ok")
    await _publish_event("run_started", {"unlock_expired": True, "cmd": " ".join(unlock_args)})
    try:
        proc = await asyncio.create_subprocess_exec(
            *unlock_args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode(errors="ignore").rstrip()
            level = "err" if any(token in text.lower() for token in ("error", "failed", "exception")) else "warn" if any(token in text.lower() for token in ("warn", "skip")) else "ok" if any(token in text.lower() for token in ("success", "sent", "completed")) else "info"
            await _publish_log_line(text, level=level)
        await proc.wait()
        if proc.returncode == 0:
            await _publish_log_line("[INFO] Refreshing users snapshot after unlock...", level="info")
            await _run_pce(True)
        else:
            append_live_log("run_finished", f"UNLOCK EXPIRED FINISHED | exit={proc.returncode}", level="err")
            await _publish_event("run_finished", {"exit_code": proc.returncode, "unlock_expired": True})
    except Exception as e:
        append_live_log("run_finished", f"UNLOCK EXPIRED FINISHED | exit=-1 | error={e}", level="err")
        await _publish_event("run_finished", {"exit_code": -1, "error": str(e), "unlock_expired": True})

@router.post("/trigger")
async def trigger(background: BackgroundTasks, whatif: bool = False):
    _ensure_idle("policy run")
    background.add_task(_run_pce, whatif)
    return {"status": "started", "whatif": whatif}


@router.post("/diagnose")
async def diagnose(background: BackgroundTasks):
    _ensure_idle("diagnostic")

    async def _run_diagnostics():
        args = _build_args(False, diagnose=True)
        append_live_log("run_started", f"DIAGNOSTIC STARTED | cmd={' '.join(args)}", level="ok")
        await _publish_event("run_started", {"whatif": True, "cmd": " ".join(args), "diagnostic": True})
        try:
            proc = await asyncio.create_subprocess_exec(
                *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                text = line.decode(errors="ignore").rstrip()
                level = "err" if any(token in text.lower() for token in ("error", "failed", "exception")) else "warn" if any(token in text.lower() for token in ("warn", "skip")) else "ok" if any(token in text.lower() for token in ("success", "sent", "completed", "[ad]", "[entra]")) else "info"
                await _publish_log_line(text, level=level)
            await proc.wait()
            append_live_log("run_finished", f"DIAGNOSTIC FINISHED | exit={proc.returncode}", level="ok" if proc.returncode == 0 else "err")
            await _publish_event("run_finished", {"exit_code": proc.returncode, "diagnostic": True})
        except Exception as e:
            append_live_log("run_finished", f"DIAGNOSTIC FINISHED | exit=-1 | error={e}", level="err")
            await _publish_event("run_finished", {"exit_code": -1, "error": str(e), "diagnostic": True})

    background.add_task(_run_diagnostics)
    return {"status": "started", "diagnostic": True}


@router.post("/sync-directory")
async def sync_directory(background: BackgroundTasks):
    _ensure_idle("directory sync")
    background.add_task(_run_sync_directory)
    return {"status": "started", "sync_directory": True}


@router.post("/unlock-expired")
async def unlock_expired(background: BackgroundTasks):
    _ensure_idle("unlock-expired")
    background.add_task(_run_unlock_expired)
    return {"status": "started", "unlock_expired": True}


@router.get("/log-history")
async def log_history(limit: int = 300):
    return recent_live_logs(limit)


@router.post("/log-history/clear")
async def clear_log_history():
    deleted = clear_live_logs()
    broadcaster.clear_history()
    await _publish_event("log_cleared", {"deleted": deleted})
    return {"status": "cleared", "deleted": deleted}
