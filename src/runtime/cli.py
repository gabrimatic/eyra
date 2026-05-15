"""Command-line entrypoints for Eyra installs and support diagnostics."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib.metadata
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from runtime.preflight import PreflightManager
from utils.settings import Settings


@dataclass(frozen=True)
class CommandResult:
    ok: bool
    message: str
    data: dict[str, Any]


def cli(argv: list[str] | None = None) -> int:
    """Run the Eyra command router. No args starts the live session."""
    parser = argparse.ArgumentParser(prog="eyra", description="Local-first voice coordinator for macOS.")
    subcommands = parser.add_subparsers(dest="command")

    subcommands.add_parser("web", help="Start the local Web UI.")

    doctor = subcommands.add_parser("doctor", help="Check install, local runtime, and optional surfaces.")
    doctor.add_argument("--json", action="store_true", help="Print machine-readable support diagnostics.")

    setup = subcommands.add_parser("setup", help="Create or repair local first-run configuration.")
    setup.add_argument("--non-interactive", action="store_true", help="Do not run provider selection prompts.")
    setup.add_argument("--json", action="store_true", help="Print machine-readable setup status.")

    certify = subcommands.add_parser("certify", help="Run the local certification matrix.")
    certify.add_argument("--include-physical", action="store_true", help="Request attended physical microphone checks.")
    certify.add_argument("--synthetic-mic", action="store_true", help="Accept a configured virtual microphone source.")
    certify.add_argument("--human-phrase", default="", help="Challenge phrase for attended physical barge-in checks.")
    certify.add_argument("--json", action="store_true", help="Print certification rows as JSON.")

    connectors = subcommands.add_parser("connectors", help="Validate, test, and list connector manifests.")
    connectors.add_argument("connector_action", choices=("validate", "test", "list"), help="Connector action to run.")
    connectors.add_argument("connector_id", nargs="?", help="Connector id for the test action.")
    connectors.add_argument("--json", action="store_true", dest="json_output", help="Print machine-readable connector output.")

    update = subcommands.add_parser("update", help="Explain the correct update command for this install.")
    update.add_argument("--json", action="store_true", help="Print machine-readable update guidance.")

    uninstall = subcommands.add_parser("uninstall", help="Remove Eyra-created command shims.")
    uninstall.add_argument("--dry-run", action="store_true", help="Show what would be removed.")
    uninstall.add_argument("--yes", action="store_true", help="Remove shims without prompting.")
    uninstall.add_argument("--with-data", action="store_true", help="Also remove config/data paths after confirmation.")
    uninstall.add_argument("--json", action="store_true", help="Print machine-readable uninstall plan.")

    version = subcommands.add_parser("version", help="Print version and install source.")
    version.add_argument("--json", action="store_true", help="Print machine-readable version info.")

    paths = subcommands.add_parser("paths", help="Print config, log, job, trigger, and command paths.")
    paths.add_argument("--json", action="store_true", help="Print machine-readable path info.")

    args = parser.parse_args(argv)
    if args.command is None:
        return _run_live_session()
    if args.command == "web":
        from web.server import run

        run()
        return 0
    if args.command == "doctor":
        return _emit(_run_async(_doctor()), json_output=args.json)
    if args.command == "setup":
        return _emit(_setup(non_interactive=args.non_interactive), json_output=args.json)
    if args.command == "certify":
        from runtime.certification import run_certification

        settings = Settings.load_from_env()
        report = run_certification(
            settings,
            include_physical=args.include_physical,
            synthetic_mic=args.synthetic_mic,
            human_phrase=args.human_phrase,
        )
        if args.json:
            print(json.dumps({"ok": not report.failed, "rows": [asdict(row) for row in report.rows]}, indent=2))
        else:
            print(report.render())
        return 1 if report.failed else 0
    if args.command == "connectors":
        from runtime.connectors.cli import main as connectors_main

        connector_args = [args.connector_action]
        if args.connector_id:
            connector_args.append(args.connector_id)
        if args.json_output:
            connector_args.append("--json")
        return connectors_main(connector_args)
    if args.command == "update":
        return _emit(_update_guidance(), json_output=args.json)
    if args.command == "uninstall":
        return _emit(
            _uninstall(dry_run=args.dry_run, assume_yes=args.yes, with_data=args.with_data),
            json_output=args.json,
        )
    if args.command == "version":
        return _emit(_version_result(), json_output=args.json)
    if args.command == "paths":
        return _emit(CommandResult(True, _format_paths(), _paths()), json_output=args.json)
    parser.error(f"unknown command: {args.command}")
    return 2


def doctor() -> None:
    raise SystemExit(cli(["doctor", *sys.argv[1:]]))


def setup() -> None:
    raise SystemExit(cli(["setup", *sys.argv[1:]]))


def certify() -> None:
    raise SystemExit(cli(["certify", *sys.argv[1:]]))


def _run_live_session() -> int:
    from main import main
    from runtime.startup import maybe_run_startup_selector
    from utils.theme import NC, RED

    maybe_run_startup_selector()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n  Interrupted.\n")
    except Exception as exc:
        import logging

        from main import get_log_file_path

        logging.getLogger("Main").exception("Unhandled: %s", exc)
        print(f"\n  {RED}Something went wrong.{NC} Check {get_log_file_path()} and try again.\n")
        return 1
    return 0


def _run_async(coro):
    return asyncio.run(coro)


def _emit(result: CommandResult, *, json_output: bool) -> int:
    if json_output:
        print(json.dumps({"ok": result.ok, "message": result.message, **result.data}, indent=2, sort_keys=True))
    else:
        print(result.message)
    return 0 if result.ok else 1


async def _doctor(settings_override: Settings | None = None) -> CommandResult:
    settings = settings_override or Settings.load_from_env()
    preflight = None
    preflight_error = ""
    safe_settings = _settings_for_diagnostics(settings)
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            preflight = await PreflightManager(safe_settings).run()
    except Exception as exc:
        preflight_error = str(exc) or exc.__class__.__name__

    data = {
        "version": _version_info(),
        "paths": _paths(settings),
        "commands": _command_versions(),
        "platform": {
            "system": platform.system(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "macos": platform.mac_ver()[0],
        },
        "settings": _safe_settings(settings),
        "preflight": _preflight_summary(preflight, preflight_error),
        "microphones": _microphone_summary(),
        "recentErrors": _recent_errors(),
    }
    voice_requested = settings.LIVE_LISTENING_ENABLED or settings.LIVE_SPEECH_ENABLED
    voice_ok = not voice_requested or bool(preflight and preflight.wh_available)
    ok = not preflight_error and bool(preflight and preflight.backend_reachable and not preflight.models_missing and voice_ok)
    if settings.USE_MOCK_CLIENT:
        ok = not preflight_error
    message = _format_doctor(data, ok)
    return CommandResult(ok, message, data)


def _settings_for_diagnostics(settings: Settings) -> Settings:
    from dataclasses import replace

    return replace(settings, AUTO_PULL_MODELS=False)


def _preflight_summary(preflight, error: str) -> dict[str, Any]:
    if preflight is None:
        return {"ok": False, "error": error}
    return {
        "ok": not error,
        "error": error,
        "backendReachable": preflight.backend_reachable,
        "modelsReady": list(preflight.models_ready),
        "modelsMissing": list(preflight.models_missing),
        "toolCapableModels": list(preflight.tool_capable_models),
        "visionCapableModels": list(preflight.vision_capable_models),
        "localWhisper": {
            "available": preflight.wh_available,
            "binary": _redact_path(preflight.wh_bin or ""),
            "listeningAvailable": preflight.listening_available,
            "speechAvailable": preflight.speech_available,
        },
        "screenCaptureAvailable": preflight.screen_capture_available,
    }


def _microphone_summary() -> dict[str, Any]:
    try:
        from runtime.voice_diagnostics import list_input_devices

        devices = list_input_devices()
    except Exception:
        devices = []
    return {
        "inputDeviceCount": len(devices),
        "inputDevices": [
            {
                "index": device.get("index"),
                "name": str(device.get("name", "")),
                "maxInputChannels": device.get("max_input_channels"),
            }
            for device in devices[:12]
        ],
    }


def _safe_settings(settings: Settings) -> dict[str, Any]:
    return {
        "apiBaseUrl": settings.API_BASE_URL,
        "model": settings.MODEL,
        "simpleModel": settings.SIMPLE_MODEL,
        "moderateModel": settings.MODERATE_MODEL,
        "visionModel": settings.VISION_MODEL or settings.MODEL,
        "workerModel": settings.WORKER_MODEL or settings.MODEL,
        "networkToolsEnabled": settings.NETWORK_TOOLS_ENABLED,
        "liveListeningEnabled": settings.LIVE_LISTENING_ENABLED,
        "liveSpeechEnabled": settings.LIVE_SPEECH_ENABLED,
        "osToolsEnabled": settings.OS_TOOLS_ENABLED,
        "mcpToolsEnabled": settings.MCP_TOOLS_ENABLED,
        "connectorsEnabled": settings.CONNECTORS_ENABLED,
        "connectorsAllowRemote": settings.CONNECTORS_ALLOW_REMOTE,
        "agentToolsEnabled": settings.AGENT_TOOLS_ENABLED,
        "externalAgentToolsEnabled": settings.EXTERNAL_AGENT_TOOLS_ENABLED,
        "realtimeVoiceEnabled": settings.REALTIME_VOICE_ENABLED,
        "webUiEnabled": settings.WEB_UI_ENABLED,
        "filesystemAllowedPaths": settings.FILESYSTEM_ALLOWED_PATHS,
        "filesystemDefaultPath": settings.FILESYSTEM_DEFAULT_PATH,
        "hasApiKey": bool(settings.API_KEY and settings.API_KEY != "ollama"),
        "hasOpenAiApiKey": bool(settings.OPENAI_API_KEY),
    }


def _format_doctor(data: dict[str, Any], ok: bool) -> str:
    preflight = data["preflight"]
    lines = ["Eyra doctor", ""]
    lines.append(f"Status: {'ready' if ok else 'needs attention'}")
    lines.append(f"Version: {data['version']['version']} ({data['version']['installSource']})")
    lines.append(f"Python: {data['platform']['python']}")
    lines.append(f"Backend: {'ready' if preflight.get('backendReachable') else 'not reachable'}")
    missing = preflight.get("modelsMissing") or []
    lines.append(f"Models: {'ready' if not missing else 'missing ' + ', '.join(missing)}")
    wh = preflight.get("localWhisper", {})
    voice_enabled = data["settings"]["liveListeningEnabled"] or data["settings"]["liveSpeechEnabled"]
    if not voice_enabled:
        lines.append("Local Whisper: disabled")
    else:
        lines.append(f"Local Whisper: {'ready' if wh.get('available') else 'not ready'}")
    lines.append(f"Microphones: {data['microphones']['inputDeviceCount']} input device(s)")
    lines.append(f"Screen capture: {'ready' if preflight.get('screenCaptureAvailable') else 'not ready'}")
    lines.append(f"Web UI: {'enabled' if data['settings']['webUiEnabled'] else 'disabled'}")
    lines.append(f"Network/OS/MCP/connectors/agents: {_optional_surface_summary(data['settings'])}")
    if preflight.get("error"):
        lines.append(f"Error: {preflight['error']}")
    lines.append("")
    lines.append("Run `eyra certify` for the release matrix.")
    return "\n".join(lines)


def _optional_surface_summary(settings: dict[str, Any]) -> str:
    enabled = [
        name
        for name, flag in [
            ("network", settings["networkToolsEnabled"]),
            ("OS", settings["osToolsEnabled"]),
            ("MCP", settings["mcpToolsEnabled"]),
            ("connectors", settings["connectorsEnabled"]),
            ("agents", settings["agentToolsEnabled"] or settings["externalAgentToolsEnabled"]),
        ]
        if flag
    ]
    return ", ".join(enabled) if enabled else "disabled by default"


def _setup(*, non_interactive: bool) -> CommandResult:
    repo = _repo_root()
    env_path = _primary_env_path()
    example = repo / ".env.example"
    created = False
    preserved = env_path.exists()
    if not env_path.exists():
        env_path.parent.mkdir(parents=True, exist_ok=True)
        env_path.write_text(example.read_text() if example.exists() else _default_env_text())
        created = True
    _ensure_user_dirs()
    if not non_interactive:
        from runtime.startup import maybe_run_startup_selector

        maybe_run_startup_selector()
    data = {"envPath": str(env_path), "createdEnv": created, "preservedEnv": preserved, "paths": _paths()}
    if created:
        message = "Created .env from .env.example. Run `eyra doctor` next."
    elif preserved:
        message = "Existing .env preserved. Run `eyra doctor` next."
    else:
        message = "No .env.example was found; no config was written. Run `eyra doctor` to inspect the install."
    return CommandResult(True, message, data)


def _update_guidance() -> CommandResult:
    source = _detect_install_source()
    commands = {
        "source": "cd <eyra checkout> && git pull --ff-only && uv sync",
        "managed-install": "Run the latest install.sh for the release you want, after verifying the source.",
        "homebrew": "brew update && brew upgrade gabrimatic/eyra/eyra",
        "uv-tool": "uv tool upgrade eyra",
        "pipx": "pipx upgrade eyra",
        "wheel": "Install a newer wheel, then rerun `eyra doctor`.",
        "unknown": "Use the same method you originally used to install Eyra, then rerun `eyra doctor`.",
    }
    key = source["kind"]
    message = (
        "Eyra update\n\n"
        f"Install source: {source['label']}\n"
        f"Suggested command: {commands.get(key, commands['unknown'])}\n\n"
        "Update never deletes .env, jobs, triggers, logs, or the operation ledger."
    )
    return CommandResult(True, message, {"installSource": source, "suggestedCommand": commands.get(key, commands["unknown"])})


def _uninstall(*, dry_run: bool, assume_yes: bool, with_data: bool) -> CommandResult:
    paths = _paths()
    candidates = [
        Path(paths["userBin"]) / name
        for name in ("eyra", "eyra-web", "eyra-doctor", "eyra-certify", "eyra-setup", "eyra-connectors")
    ]
    existing = [path for path in candidates if path.exists()]
    data_paths = [Path(paths[name]).expanduser() for name in ("configDir", "dataDir", "logDir")]
    shell_rc_paths = _shell_rc_paths()
    if dry_run:
        return CommandResult(
            True,
            _format_uninstall(existing, data_paths, shell_rc_paths, dry_run=True, with_data=with_data),
            {
                "removed": [],
                "wouldRemove": [str(path) for path in existing],
                "wouldCleanShellRc": [str(path) for path in shell_rc_paths],
                "dataPaths": [str(path) for path in data_paths],
            },
        )
    if not assume_yes:
        answer = input("Remove Eyra command shims from ~/.local/bin? Type yes to continue: ").strip().lower()
        if answer != "yes":
            return CommandResult(False, "Uninstall cancelled.", {"removed": []})
    removed: list[str] = []
    for path in existing:
        try:
            path.unlink()
            removed.append(str(path))
        except OSError:
            pass
    cleaned_shell_rc = _remove_shell_rc_lines(shell_rc_paths)
    data_removed: list[str] = []
    if with_data:
        if not assume_yes:
            answer = input("Also remove Eyra config/data/log directories? Type DELETE to continue: ").strip()
            if answer != "DELETE":
                with_data = False
        if with_data:
            for path in data_paths:
                if path.exists():
                    shutil.rmtree(path, ignore_errors=True)
                    data_removed.append(str(path))
    message = _format_uninstall(existing, data_paths, shell_rc_paths, dry_run=False, with_data=with_data)
    return CommandResult(True, message, {"removed": removed, "cleanedShellRc": cleaned_shell_rc, "dataRemoved": data_removed})


def _format_uninstall(
    command_paths: list[Path],
    data_paths: list[Path],
    shell_rc_paths: list[Path],
    *,
    dry_run: bool,
    with_data: bool,
) -> str:
    action = "Would remove" if dry_run else "Removed"
    lines = ["Eyra uninstall", ""]
    if command_paths:
        lines.extend(f"{action}: {path}" for path in command_paths)
    else:
        lines.append("No Eyra command shims found in ~/.local/bin.")
    if shell_rc_paths:
        lines.append("")
        lines.extend(f"{'Would clean' if dry_run else 'Cleaned'} Eyra shell lines in: {path}" for path in shell_rc_paths)
    if with_data:
        lines.append("")
        lines.extend(f"{action} data path: {path}" for path in data_paths)
    else:
        lines.append("")
        lines.append("User data is preserved by default:")
        lines.extend(f"- {path}" for path in data_paths)
    return "\n".join(lines)


def _shell_rc_paths() -> list[Path]:
    home = Path.home()
    return [path for path in (home / ".zshrc", home / ".bashrc") if path.exists()]


def _remove_shell_rc_lines(paths: list[Path]) -> list[str]:
    cleaned: list[str] = []
    for path in paths:
        try:
            original = path.read_text()
        except OSError:
            continue
        kept = [line for line in original.splitlines() if not line.rstrip().endswith("# eyra")]
        trailing_newline = "\n" if original.endswith("\n") and kept else ""
        new_content = "\n".join(kept) + trailing_newline
        if new_content != original:
            path.write_text(new_content)
            cleaned.append(str(path))
    return cleaned


def _version_result() -> CommandResult:
    info = _version_info()
    message = (
        f"Eyra {info['version']}\n"
        f"Install source: {info['installSource']}\n"
        f"Commit: {info['commit'] or 'unknown'}\n"
        f"Python: {platform.python_version()}"
    )
    return CommandResult(True, message, {"version": info})


def _version_info() -> dict[str, Any]:
    try:
        version = importlib.metadata.version("eyra")
    except importlib.metadata.PackageNotFoundError:
        version = _pyproject_version()
    source = _detect_install_source()
    return {
        "version": version,
        "commit": _git_commit(),
        "installSource": source["kind"],
        "installSourceLabel": source["label"],
        "executable": sys.argv[0],
    }


def _pyproject_version() -> str:
    pyproject = _repo_root() / "pyproject.toml"
    if not pyproject.exists():
        return "unknown"
    for line in pyproject.read_text().splitlines():
        if line.startswith("version ="):
            return line.split("=", 1)[1].strip().strip('"')
    return "unknown"


def _primary_env_path() -> Path:
    repo = _repo_root()
    if _is_source_checkout(repo):
        return repo / ".env"
    return Path.home() / ".config" / "eyra" / ".env"


def _is_source_checkout(repo: Path | None = None) -> bool:
    root = repo or _repo_root()
    return (root / ".git").exists() and (root / "pyproject.toml").exists() and (root / "src").exists()


def _default_env_text() -> str:
    return "\n".join(
        [
            "API_BASE_URL=http://localhost:11434/v1",
            "API_KEY=ollama",
            "USE_MOCK_CLIENT=false",
            "MODEL=gemma4:e4b",
            "VISION_MODEL=",
            "AUTO_PULL_MODELS=true",
            "LIVE_LISTENING_ENABLED=true",
            "LIVE_SPEECH_ENABLED=true",
            "VOICE_VAD_THRESHOLD=0.15",
            "NETWORK_TOOLS_ENABLED=false",
            "OS_TOOLS_ENABLED=false",
            "AGENT_TOOLS_ENABLED=false",
            "EXTERNAL_AGENT_TOOLS_ENABLED=false",
            "MCP_TOOLS_ENABLED=false",
            "WEB_UI_ENABLED=false",
            "REALTIME_VOICE_ENABLED=false",
            "FILESYSTEM_ALLOWED_PATHS=~/Documents,~/Desktop,~/Downloads,/tmp",
            "FILESYSTEM_DEFAULT_PATH=~/Documents",
            "",
        ]
    )


def _git_commit() -> str:
    repo = _repo_root()
    if not (repo / ".git").exists():
        return ""
    candidates = [Path("/usr/bin/git"), Path("/opt/homebrew/bin/git")]
    try:
        resolved = shutil.which("git")
        if resolved:
            candidates.append(Path(resolved))
    except Exception:
        pass
    seen: set[str] = set()
    for candidate in candidates:
        git = str(candidate)
        if git in seen or not candidate.exists():
            continue
        seen.add(git)
        try:
            result = subprocess.run(
                [git, "rev-parse", "--short=12", "HEAD"],
                cwd=repo,
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
        except Exception:
            continue
        if result.returncode == 0:
            return result.stdout.strip()
    try:
        return subprocess.check_output(["git", "rev-parse", "--short=12", "HEAD"], cwd=repo, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return ""


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False


def _detect_install_source() -> dict[str, str]:
    repo = _repo_root()
    executable = Path(sys.argv[0]).expanduser()
    candidates = [executable, Path(sys.executable).expanduser(), Path(sys.prefix).expanduser(), Path(sys.base_prefix).expanduser()]
    resolved: list[Path] = []
    for candidate in candidates:
        resolved.append(candidate)
        try:
            resolved.append(candidate.resolve())
        except Exception:
            pass
    pipx_roots = [os.environ.get("PIPX_HOME"), os.environ.get("PIPX_BIN_DIR")]
    is_pipx_env = any(
        root and any(_is_relative_to(path, Path(root).expanduser()) for path in resolved)
        for root in pipx_roots
    )
    text = " ".join(str(path) for path in resolved)
    if _is_source_checkout(repo):
        return {"kind": "source", "label": f"source checkout at {repo}"}
    if any(
        marker in text
        for marker in (
            "Cellar/eyra",
            "/opt/homebrew/opt/eyra",
            "/opt/homebrew/var/eyra",
            "/usr/local/opt/eyra",
            "/usr/local/var/eyra",
            "/var/eyra/venv",
            "Homebrew",
        )
    ):
        return {"kind": "homebrew", "label": "Homebrew tap"}
    if "/.local/share/eyra/app/.venv" in text or "/share/eyra/app/.venv" in text:
        return {"kind": "managed-install", "label": "install.sh managed app"}
    if ".local/share/uv/tools" in text or "/uv/tools/" in text:
        return {"kind": "uv-tool", "label": "uv tool"}
    if is_pipx_env or "pipx/venvs" in text or "/pipx/" in text:
        return {"kind": "pipx", "label": "pipx"}
    try:
        importlib.metadata.distribution("eyra")
        return {"kind": "wheel", "label": "installed Python package"}
    except importlib.metadata.PackageNotFoundError:
        pass
    return {"kind": "unknown", "label": "unknown"}


def _paths(settings: Settings | None = None) -> dict[str, str]:
    settings = settings or Settings.load_from_env()
    home = Path.home()
    return {
        "repoRoot": str(_repo_root()),
        "env": str(_primary_env_path().expanduser()),
        "userConfigEnv": str(home / ".config" / "eyra" / ".env"),
        "configDir": str(home / ".config" / "eyra"),
        "dataDir": str(home / ".local" / "share" / "eyra"),
        "logDir": str(home / "Library" / "Logs" / "Eyra"),
        "jobStore": _redact_path(settings.JOB_STORE_PATH),
        "triggerStore": _redact_path(settings.TRIGGER_STORE_PATH),
        "externalAgentConfig": _redact_path(settings.EXTERNAL_AGENT_CONFIG_PATH),
        "mcpConfig": _redact_path(settings.MCP_CONFIG_PATH),
        "userBin": str(home / ".local" / "bin"),
    }


def _format_paths() -> str:
    lines = ["Eyra paths", ""]
    for key, value in _paths().items():
        lines.append(f"{key}: {value}")
    return "\n".join(lines)


def _ensure_user_dirs() -> None:
    for key, value in _paths().items():
        if key.endswith("Dir") or key == "userBin":
            Path(value).expanduser().mkdir(parents=True, exist_ok=True)


def _command_versions() -> dict[str, Any]:
    return {
        "uv": _command_version("uv", "--version"),
        "brew": _command_version("brew", "--version"),
        "ollama": _command_version("ollama", "--version"),
        "wh": _command_version("wh", "--version"),
    }


def _recent_errors() -> list[str]:
    log_file = Path.home() / "Library" / "Logs" / "Eyra" / "eyra.log"
    if not log_file.exists():
        return []
    try:
        lines = log_file.read_text(errors="replace").splitlines()[-200:]
    except OSError:
        return []
    errors = [line for line in lines if " ERROR " in line or " CRITICAL " in line]
    return [_redact_path(_redact_secrets(line))[:500] for line in errors[-10:]]


def _redact_secrets(value: str) -> str:
    redacted = re.sub(r"(?i)(api[_-]?key|token|secret|password)=([^\s]+)", r"\1=[REDACTED]", value)
    redacted = re.sub(r"sk-[A-Za-z0-9_-]{16,}", "[REDACTED]", redacted)
    return re.sub(r"(?i)(token=)[^&\s]+", r"\1[REDACTED]", redacted)


def _command_version(command: str, *args: str) -> dict[str, Any]:
    path = shutil.which(command)
    if not path:
        return {"available": False, "path": "", "version": ""}
    try:
        version = subprocess.check_output([command, *args], text=True, stderr=subprocess.STDOUT, timeout=5).splitlines()[0]
    except Exception:
        version = ""
    return {"available": True, "path": _redact_path(path), "version": version}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _redact_path(value: str | Path) -> str:
    text = str(value)
    home = str(Path.home())
    return text.replace(home, "~") if home else text
