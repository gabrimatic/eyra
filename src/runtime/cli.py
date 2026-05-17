"""Command-line entrypoints for Eyra installs and support diagnostics."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib.metadata
import importlib.resources as resources
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import webbrowser
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from runtime.examples import render_examples
from runtime.preflight import PreflightManager
from runtime.service import service_paths, service_status, start_service, stop_service, web_url_with_token
from runtime.settings_catalog import get_setting, setting_specs, settings_snapshot, write_setting
from utils.settings import Settings


@dataclass(frozen=True)
class CommandResult:
    ok: bool
    message: str
    data: dict[str, Any]


@dataclass(frozen=True)
class MenuBarResource:
    resource_available: bool
    mode: str
    path: Path | None
    swift_required: bool
    fallback_command: str = "eyra open"


def cli(argv: list[str] | None = None) -> int:
    """Run the Eyra command router. No args starts the live session."""
    parser = argparse.ArgumentParser(prog="eyra", description="Local-first voice coordinator for macOS.")
    subcommands = parser.add_subparsers(dest="command")

    start = subcommands.add_parser("start", help="Start Eyra's local Web control service in the background.")
    start.add_argument("--json", action="store_true", help="Print machine-readable service status.")
    stop = subcommands.add_parser("stop", help="Stop Eyra's local Web control service.")
    stop.add_argument("--json", action="store_true", help="Print machine-readable service status.")
    restart = subcommands.add_parser("restart", help="Restart Eyra's local Web control service.")
    restart.add_argument("--json", action="store_true", help="Print machine-readable service status.")
    menu = subcommands.add_parser("menu", help="Launch the native macOS menu bar control surface.")
    menu.add_argument("--json", action="store_true", help="Print machine-readable launch status.")
    menu.add_argument("--check", action="store_true", help="Report menu bar availability without launching it.")
    status = subcommands.add_parser("status", help="Show simple local readiness and service status.")
    status.add_argument("--json", action="store_true", help="Print machine-readable status.")
    open_cmd = subcommands.add_parser("open", help="Open Eyra's local Web UI, starting it if needed.")
    open_cmd.add_argument("--json", action="store_true", help="Print machine-readable open status.")
    logs = subcommands.add_parser("logs", help="Show or open Eyra log locations.")
    logs.add_argument("--open", action="store_true", help="Open the log folder in Finder.")
    logs.add_argument("--json", action="store_true", help="Print machine-readable log paths.")
    service = subcommands.add_parser("service", help="Manage the local Web control service.")
    service_subcommands = service.add_subparsers(dest="service_action", required=True)
    for action in ("status", "start", "stop", "restart"):
        service_action = service_subcommands.add_parser(action, help=f"{action.title()} the service.")
        service_action.add_argument("--json", action="store_true", help="Print machine-readable service status.")

    subcommands.add_parser("web", help="Start the local Web UI.")
    subcommands.add_parser("examples", help="Show useful first prompts and local workflows.")

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

    settings = subcommands.add_parser("settings", help="Show or edit simple Eyra settings.")
    settings.add_argument("--json", action="store_true", help="Print machine-readable settings.")
    settings_subcommands = settings.add_subparsers(dest="settings_action")
    settings_subcommands.add_parser("list", help="List settings.")
    settings_get = settings_subcommands.add_parser("get", help="Show one setting.")
    settings_get.add_argument("key")
    settings_set = settings_subcommands.add_parser("set", help="Set one simple setting.")
    settings_set.add_argument("key")
    settings_set.add_argument("value")

    args = parser.parse_args(argv)
    if args.command is None:
        return _run_live_session()
    if args.command in {"start", "stop", "restart", "menu", "status", "open", "logs", "service", "settings"}:
        return _handle_simple_command(args)
    if args.command == "web":
        from web.server import run

        run()
        return 0
    if args.command == "examples":
        return _emit(CommandResult(True, render_examples(), {}), json_output=False)
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


def menu() -> None:
    raise SystemExit(cli(["menu", *sys.argv[1:]]))


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


def _handle_simple_command(args) -> int:
    if args.command == "settings":
        return _handle_settings_command(args)
    if args.command == "logs":
        return _emit(_logs(open_folder=args.open), json_output=args.json)
    if args.command == "menu":
        return _emit(_launch_menu_bar(check_only=args.check), json_output=args.json)

    settings = Settings.load_from_env()
    json_output = bool(getattr(args, "json", False))
    action = args.command
    if args.command == "service":
        action = args.service_action
        json_output = bool(getattr(args, "json", False))

    if action == "status":
        if args.command == "status":
            return _emit(_status(settings), json_output=json_output)
        return _emit(_service_result(service_status(settings)), json_output=json_output)
    if action == "start":
        return _emit(_service_result(start_service(settings)), json_output=json_output)
    if action == "stop":
        return _emit(_service_result(stop_service(settings)), json_output=json_output)
    if action == "restart":
        stop_service(settings)
        return _emit(_service_result(start_service(settings)), json_output=json_output)
    if action == "open":
        return _emit(_open_web(settings), json_output=json_output)
    raise SystemExit(f"unknown command: {args.command}")


def _handle_settings_command(args) -> int:
    settings = Settings.load_from_env()
    action = args.settings_action or "list"
    json_output = bool(args.json)
    try:
        if action == "list":
            rows = settings_snapshot(settings)
            return _emit(
                CommandResult(True, _format_settings(rows), {"settings": rows, "schema": setting_specs()}),
                json_output=json_output,
            )
        if action == "get":
            row = get_setting(settings, args.key)
            return _emit(CommandResult(True, _format_one_setting(row), {"setting": row}), json_output=json_output)
        if action == "set":
            value = write_setting(_primary_env_path(), args.key, args.value)
            message = f"Updated {args.key.upper()} to {value}.\nRestart Eyra if it is already running."
            return _emit(CommandResult(True, message, {"key": args.key.upper(), "value": value, "restartRequired": True}), json_output=json_output)
    except (KeyError, ValueError) as exc:
        return _emit(CommandResult(False, str(exc), {}), json_output=json_output)
    return _emit(CommandResult(False, f"Unknown settings action: {action}", {}), json_output=json_output)


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
    lines.append(f"AI provider: {'ready' if preflight.get('backendReachable') else 'not ready'}")
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
    if not preflight.get("backendReachable") or missing:
        lines.append("Next: run `eyra setup` for guided provider and model setup.")
    elif voice_enabled and not wh.get("available"):
        lines.append("Next: install or start Local Whisper, then rerun `eyra doctor`.")
    lines.append("Run `eyra certify` for the release matrix.")
    return "\n".join(lines)


def _status(settings: Settings) -> CommandResult:
    doctor_result = _run_async(_doctor(settings))
    service = service_status(settings)
    data = {**doctor_result.data, "service": service}
    preflight = data["preflight"]
    lines = ["Eyra status", ""]
    lines.append(f"Local model: {'Ready' if preflight.get('backendReachable') and not preflight.get('modelsMissing') else 'Needs attention'}")
    wh = preflight.get("localWhisper", {})
    if not (data["settings"]["liveListeningEnabled"] or data["settings"]["liveSpeechEnabled"]):
        voice = "Off"
    elif wh.get("available") and wh.get("listeningAvailable") is not False and wh.get("speechAvailable") is not False:
        voice = "Ready"
    elif wh.get("available"):
        voice = "Partly ready"
    else:
        voice = "Needs attention"
    lines.append(f"Voice: {voice}")
    lines.append(f"Web control: {'Running' if service['running'] else 'Stopped'}")
    lines.append(f"Local-first default: {'No data leaves your Mac by default' if _local_first_default(data['settings']) else 'Review enabled remote/network settings'}")
    lines.append(f"Network tools: {'On' if data['settings']['networkToolsEnabled'] else 'Off'}")
    lines.append(f"Mac control tools: {'On' if data['settings']['osToolsEnabled'] else 'Off'}")
    lines.append(f"Connectors: {'On' if data['settings']['connectorsEnabled'] else 'Off'}")
    lines.append(f"Realtime voice: {'On' if data['settings']['realtimeVoiceEnabled'] else 'Off'}")
    lines.append("")
    lines.append("Next:")
    if not preflight.get("backendReachable") or preflight.get("modelsMissing"):
        lines.append("- Run `eyra setup` to repair local AI/model setup.")
    elif voice == "Needs attention":
        lines.append("- Run `eyra doctor` or `/voice-diagnose` to repair voice.")
    elif not service["running"]:
        lines.append("- Run `eyra open` to start the local Web control UI.")
    else:
        lines.append("- Run `eyra` for the terminal assistant, or use the Web control UI.")
    return CommandResult(doctor_result.ok, "\n".join(lines), data)


def _local_first_default(settings: dict[str, Any]) -> bool:
    return not any(
        [
            settings["networkToolsEnabled"],
            settings["osToolsEnabled"],
            settings["mcpToolsEnabled"],
            settings["connectorsEnabled"],
            settings["agentToolsEnabled"],
            settings["externalAgentToolsEnabled"],
            settings["realtimeVoiceEnabled"],
        ]
    ) and "localhost" in settings["apiBaseUrl"]


def _service_result(payload: dict[str, Any]) -> CommandResult:
    lines = ["Eyra service", "", payload.get("message", "")]
    if payload.get("running"):
        lines.append(f"Open: {payload.get('openUrl') or payload.get('url')}")
    else:
        lines.append("Start it with: eyra start")
    lines.append(f"Log: {payload.get('log')}")
    return CommandResult(True, "\n".join(lines), {"service": payload})


def _open_web(settings: Settings) -> CommandResult:
    status = service_status(settings)
    if not status["running"]:
        status = start_service(settings)
    if status["running"]:
        url = status.get("openUrl") or web_url_with_token(settings)
        webbrowser.open(url)
        return CommandResult(True, f"Opened Eyra Web UI: {url}", {"service": status, "url": url})
    return CommandResult(False, "Eyra Web UI could not start. Run `eyra logs` or `eyra doctor` for the next step.", {"service": status})


def _logs(*, open_folder: bool) -> CommandResult:
    from main import get_log_file_path

    app_log = get_log_file_path()
    service_log = service_paths().log
    if open_folder:
        webbrowser.open(str(app_log.parent))
    message = "\n".join(
        [
            "Eyra logs",
            "",
            f"App log: {app_log}",
            f"Web service log: {service_log}",
            "Logs can include local paths and diagnostics. Do not share them without reviewing first.",
        ]
    )
    return CommandResult(True, message, {"appLog": str(app_log), "webServiceLog": str(service_log)})


def _launch_menu_bar(*, check_only: bool = False) -> CommandResult:
    resource = _find_menu_bar_resource()
    swift = shutil.which("swift")
    data = _menu_bar_resource_data(resource, swift)
    if not resource.resource_available or resource.path is None:
        return CommandResult(
            False,
            "Eyra menu bar resources are not included in this install yet. Use `eyra open` for the installed control UI.",
            data,
        )
    if resource.mode == "app-bundle":
        if check_only:
            return CommandResult(True, "Eyra menu bar app bundle is available.", data)
        subprocess.Popen(_open_app_command(resource.path), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return CommandResult(True, "Eyra menu bar app is launching.", data)
    if not swift:
        return CommandResult(
            False,
            "Eyra menu bar is a developer preview in this install and needs Swift/Xcode to run. Use `eyra open` for the installed control UI.",
            data,
        )
    if check_only:
        return CommandResult(True, "Eyra menu bar SwiftPM developer preview is available.", data)
    env = os.environ.copy()
    env["EYRA_CLI_PATH"] = _current_eyra_command()
    subprocess.Popen(
        [swift, "run", "--package-path", str(resource.path), "EyraMenuBar"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env=env,
    )
    return CommandResult(True, "Eyra menu bar developer preview is launching.", data)


def _find_menu_bar_resource() -> MenuBarResource:
    for path in _menu_bar_app_bundle_candidates():
        if (path / "Contents" / "MacOS" / "EyraMenuBar").exists():
            return MenuBarResource(True, "app-bundle", path, swift_required=False)
    for mode, path in _menu_bar_package_candidates():
        if (path / "Package.swift").exists():
            return MenuBarResource(True, mode, path, swift_required=True)
    return MenuBarResource(False, "unavailable", None, swift_required=False)


def _menu_bar_package_candidates() -> list[tuple[str, Path]]:
    home = Path.home()
    repo_package = _repo_root() / "apps" / "EyraMenuBar"
    package_resource = _package_menu_bar_resource_path()
    candidates: list[tuple[str, Path | None]] = [
        ("source-swiftpm", repo_package),
        ("package-resource", package_resource),
        ("managed-install", home / ".local" / "share" / "eyra" / "app" / "apps" / "EyraMenuBar"),
        ("homebrew", Path("/opt/homebrew/opt/eyra/libexec/apps/EyraMenuBar")),
        ("homebrew", Path("/usr/local/opt/eyra/libexec/apps/EyraMenuBar")),
        ("homebrew", Path("/opt/homebrew/var/eyra/app/apps/EyraMenuBar")),
        ("homebrew", Path("/usr/local/var/eyra/app/apps/EyraMenuBar")),
    ]
    return [(mode, path) for mode, path in candidates if path is not None]


def _package_menu_bar_resource_path() -> Path | None:
    try:
        root = resources.files("runtime").joinpath("resources", "EyraMenuBar")
    except (FileNotFoundError, ModuleNotFoundError):
        return None
    if not root.joinpath("Package.swift").is_file():
        return None
    path = Path(str(root))
    if path.exists():
        return path
    return _materialize_menu_bar_resource(root)


def _materialize_menu_bar_resource(root) -> Path | None:
    target = Path.home() / ".local" / "share" / "eyra" / "menu-bar" / "EyraMenuBar"
    try:
        if (target / "Package.swift").exists():
            return target
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        _copy_traversable(root, target)
    except OSError:
        return None
    return target if (target / "Package.swift").exists() else None


def _copy_traversable(source, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for child in source.iterdir():
        destination = target / child.name
        if child.is_dir():
            _copy_traversable(child, destination)
        else:
            destination.write_bytes(child.read_bytes())


def _menu_bar_app_bundle_candidates() -> list[Path]:
    home = Path.home()
    return [
        home / ".local" / "share" / "eyra" / "Eyra.app",
        home / ".local" / "share" / "eyra" / "app" / "dist" / "Eyra.app",
        Path("/opt/homebrew/opt/eyra/libexec/Eyra.app"),
        Path("/usr/local/opt/eyra/libexec/Eyra.app"),
        _repo_root() / "dist" / "Eyra.app",
        _repo_root() / "runtime" / "resources" / "Eyra.app",
        _repo_root() / "apps" / "EyraMenuBar.app",
        home / ".local" / "share" / "eyra" / "EyraMenuBar.app",
        home / "Applications" / "EyraMenuBar.app",
        Path("/Applications/EyraMenuBar.app"),
        Path("/opt/homebrew/opt/eyra/libexec/EyraMenuBar.app"),
        Path("/usr/local/opt/eyra/libexec/EyraMenuBar.app"),
    ]


def _current_eyra_command() -> str:
    candidate = Path(sys.argv[0]).expanduser()
    if candidate.exists():
        try:
            return str(candidate.resolve())
        except OSError:
            return str(candidate)
    resolved = shutil.which("eyra")
    return resolved or sys.argv[0] or "eyra"


def _menu_bar_resource_data(resource: MenuBarResource, swift: str | None) -> dict[str, Any]:
    return {
        "available": resource.resource_available and (resource.mode == "app-bundle" or bool(swift)),
        "resourceAvailable": resource.resource_available,
        "mode": resource.mode,
        "path": str(resource.path) if resource.path else "",
        "swiftRequired": resource.swift_required,
        "swiftAvailable": None if not resource.swift_required else bool(swift),
        "swiftPath": swift or "",
        "fallbackCommand": resource.fallback_command,
    }


def _open_app_command(app_path: Path) -> list[str]:
    return [
        "/usr/bin/open",
        "-n",
        "-g",
        "--env",
        f"EYRA_CLI_PATH={_current_eyra_command()}",
        str(app_path),
    ]


def _format_settings(rows: list[dict[str, Any]]) -> str:
    lines = ["Eyra settings", ""]
    current_category = ""
    for row in rows:
        if not row["simple"]:
            continue
        if row["category"] != current_category:
            current_category = row["category"]
            lines.extend(["", current_category])
        lines.append(f"  {row['key']}: {row['value']}")
    lines.extend(["", "Use `eyra settings get MODEL` or `eyra settings set LIVE_SPEECH_ENABLED false`."])
    lines.append("Advanced settings are still available in `eyra settings --json` and the docs.")
    return "\n".join(lines).strip()


def _format_one_setting(row: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"{row['label']} ({row['key']})",
            f"Value: {row['value']}",
            f"Category: {row['category']}",
            f"Privacy: {row['privacy']}",
            f"Restart required: {'yes' if row['restart_required'] else 'no'}",
            row["description"],
        ]
    )


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
        message = (
            f"Eyra setup wrote your local settings file at {env_path}.\n"
            "Next: run `eyra doctor` to check local AI, voice, microphone, and optional features."
        )
    elif preserved:
        message = (
            f"Eyra setup found existing settings at {env_path} and kept them.\n"
            "Next: run `eyra doctor` if you want a plain-language readiness check."
        )
    else:
        message = "No .env.example was found, so no settings were written. Run `eyra doctor` to inspect the install."
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
        for name in ("eyra", "eyra-web", "eyra-doctor", "eyra-certify", "eyra-setup", "eyra-connectors", "eyra-menu")
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
