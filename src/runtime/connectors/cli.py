"""Command-line entry point for connector validation and acceptance."""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, replace

from runtime.connectors.registry import ConnectorRegistry, format_connector_list, redact_connector_payload
from utils.settings import Settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eyra connectors")
    parser.add_argument("action", choices=("validate", "test", "list"))
    parser.add_argument("connector_id", nargs="?")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)

    try:
        settings = Settings.load_from_env()
    except ValueError as exc:
        print(f"Configuration error: {exc}")
        return 2
    registry_settings = replace(settings, CONNECTORS_ENABLED=True) if args.action in {"validate", "list", "test"} else settings
    registry = ConnectorRegistry.from_settings(registry_settings)
    if args.action == "validate":
        payload = registry.capability_snapshot()
        if args.json_output:
            print(json.dumps(redact_connector_payload(payload), indent=2, sort_keys=True))
        else:
            print(format_connector_list(registry))
        return 0 if payload["config"]["status"] in {"loaded", "disabled", "missing"} else 1
    if args.action == "list":
        payload = {"connectors": registry.list_connectors()}
        if args.json_output:
            print(json.dumps(redact_connector_payload(payload), indent=2, sort_keys=True))
        else:
            print(format_connector_list(registry))
        return 0
    if args.action == "test":
        if not args.connector_id:
            print("connector_id is required for test")
            return 2
        result = asyncio.run(registry.test(args.connector_id))
        if args.json_output:
            print(json.dumps(redact_connector_payload(asdict(result)), indent=2, sort_keys=True))
        else:
            print(f"{result.connector_id}: {result.state.value} - {result.reason}")
        return 0 if result.state.value == "accepted" else 1
    return 2
