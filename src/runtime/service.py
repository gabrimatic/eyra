"""Small local service manager for the Eyra Web/control runtime."""

from __future__ import annotations

import json
import os
import secrets
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from utils.settings import Settings


@dataclass(frozen=True)
class ServicePaths:
    state: Path
    log: Path


def service_paths() -> ServicePaths:
    data_dir = Path.home() / ".local" / "share" / "eyra"
    log_dir = Path.home() / "Library" / "Logs" / "Eyra"
    data_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    return ServicePaths(state=data_dir / "service.json", log=log_dir / "eyra-web-service.log")


def base_web_url(settings: Settings) -> str:
    return f"http://{settings.WEB_UI_HOST}:{settings.WEB_UI_PORT}"


def _read_state() -> dict[str, Any]:
    state_path = service_paths().state
    if not state_path.exists():
        return {}
    try:
        return json.loads(state_path.read_text())
    except Exception:
        return {}


def _write_state(payload: dict[str, Any]) -> None:
    state_path = service_paths().state
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    state_path.chmod(0o600)


def _process_running(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _health(settings: Settings, timeout: float = 1.5) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(f"{base_web_url(settings)}/api/health", timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None


def web_url_with_token(settings: Settings, state: dict[str, Any] | None = None) -> str:
    token = settings.WEB_UI_TOKEN.strip() or (state or _read_state()).get("webToken", "")
    if token:
        return f"{base_web_url(settings)}/?token={token}"
    return base_web_url(settings)


def service_status(settings: Settings) -> dict[str, Any]:
    state = _read_state()
    pid = int(state.get("pid") or 0)
    process_running = _process_running(pid)
    health = _health(settings)
    running = bool(health)
    managed = process_running and running
    return {
        "running": running,
        "managed": managed,
        "pid": pid if process_running else None,
        "url": base_web_url(settings),
        "openUrl": web_url_with_token(settings, state) if running else base_web_url(settings),
        "log": str(service_paths().log),
        "stateFile": str(service_paths().state),
        "health": health or {},
        "message": _service_message(running=running, managed=managed, process_running=process_running),
    }


def _service_message(*, running: bool, managed: bool, process_running: bool) -> str:
    if managed:
        return "Eyra Web control service is running."
    if running:
        return "Eyra Web UI is reachable, but it was not started by `eyra service start`."
    if process_running:
        return "Eyra service process exists, but the Web UI is not reachable yet."
    return "Eyra Web control service is stopped."


def start_service(settings: Settings) -> dict[str, Any]:
    current = service_status(settings)
    if current["running"]:
        return {**current, "started": False}

    paths = service_paths()
    token = settings.WEB_UI_TOKEN.strip() or secrets.token_urlsafe(32)
    env = os.environ.copy()
    env.update(
        {
            "WEB_UI_ENABLED": "true",
            "WEB_UI_TOKEN": token,
            "WEB_UI_HOST": settings.WEB_UI_HOST,
            "WEB_UI_PORT": str(settings.WEB_UI_PORT),
        }
    )
    paths.log.parent.mkdir(parents=True, exist_ok=True)
    log_handle = paths.log.open("a")
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "web.server"],
            cwd=str(Path(__file__).resolve().parents[1]),
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    finally:
        log_handle.close()
    _write_state(
        {
            "pid": proc.pid,
            "webToken": token,
            "url": base_web_url(settings),
            "log": str(paths.log),
            "startedAt": time.time(),
        }
    )
    for _ in range(30):
        time.sleep(0.5)
        if _health(settings, timeout=0.5):
            status = service_status(settings)
            return {**status, "started": True}
        if proc.poll() is not None:
            break
    status = service_status(settings)
    return {**status, "started": False}


def stop_service(settings: Settings) -> dict[str, Any]:
    state = _read_state()
    pid = int(state.get("pid") or 0)
    stopped = False
    if _process_running(pid):
        try:
            os.kill(pid, signal.SIGTERM)
            stopped = True
        except OSError:
            pass
        for _ in range(20):
            if not _process_running(pid):
                break
            time.sleep(0.2)
        if _process_running(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    try:
        service_paths().state.unlink()
    except OSError:
        pass
    status = service_status(settings)
    return {**status, "stopped": stopped}
