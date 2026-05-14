"""Connector acceptance checks."""

from __future__ import annotations

import asyncio
import importlib.util
import shutil
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from runtime.connectors.runner import ConnectorRunner, redact_output
from runtime.connectors.types import (
    AcceptanceState,
    ConnectorAcceptanceResult,
    ConnectorJobSpec,
    ConnectorManifest,
    ConnectorType,
)


async def run_acceptance(
    manifest: ConnectorManifest,
    *,
    runner: ConnectorRunner,
) -> ConnectorAcceptanceResult:
    """Run local acceptance for one connector."""
    checks: list[dict] = []
    if not manifest.enabled:
        return ConnectorAcceptanceResult(manifest.id, AcceptanceState.DISABLED, "Connector is disabled.")
    executable = _check_executable_or_endpoint(manifest)
    checks.append({"name": "transport", "passed": executable, "reason": "transport available" if executable else "transport unavailable"})
    if not executable:
        return ConnectorAcceptanceResult(manifest.id, AcceptanceState.ACCEPTANCE_FAILED, "Connector transport is unavailable.", tuple(checks))
    if manifest.acceptance.health_command:
        health = await _run_health(manifest)
        checks.append(health)
        if not health["passed"]:
            return ConnectorAcceptanceResult(manifest.id, AcceptanceState.ACCEPTANCE_FAILED, health["reason"], tuple(checks))
    if manifest.acceptance.test_task:
        result = await runner.run(
            manifest,
            ConnectorJobSpec(
                connector_id=manifest.id,
                task=manifest.acceptance.test_task,
                cwd=str(manifest.default_path),
                source="acceptance",
                approval_id="",
                job_id=f"accept-{manifest.id}",
            ),
        )
        expected = manifest.acceptance.expected_output_contains
        passed = result.status in {"completed", "approval_required"} and (not expected or expected in result.output)
        checks.append({"name": "test_task", "passed": passed, "reason": redact_output(result.output[:240])})
        if not passed:
            return ConnectorAcceptanceResult(manifest.id, AcceptanceState.ACCEPTANCE_FAILED, "Acceptance test task failed.", tuple(checks))
    redacted = redact_output("token=secret-token sk-1234567890abcdefghijkl /Users/example/private")
    passed_redaction = "secret-token" not in redacted and "/Users/example" not in redacted
    checks.append({"name": "output_redaction", "passed": passed_redaction, "reason": "redaction active"})
    if not passed_redaction:
        return ConnectorAcceptanceResult(manifest.id, AcceptanceState.ACCEPTANCE_FAILED, "Output redaction failed.", tuple(checks))
    return ConnectorAcceptanceResult(manifest.id, AcceptanceState.ACCEPTED, "Connector accepted.", tuple(checks))


def initial_acceptance_state(manifest: ConnectorManifest) -> AcceptanceState:
    if not manifest.enabled:
        return AcceptanceState.DISABLED
    return AcceptanceState.AVAILABLE if _check_executable_or_endpoint(manifest) else AcceptanceState.CONFIGURED


def _check_executable_or_endpoint(manifest: ConnectorManifest) -> bool:
    if manifest.type in {ConnectorType.CLI, ConnectorType.MCP, ConnectorType.BROWSER_AGENT, ConnectorType.CODING_AGENT}:
        if not manifest.command:
            return False
        executable = manifest.command[0]
        return bool(shutil.which(executable) or Path(executable).expanduser().exists())
    if manifest.type == ConnectorType.PYTHON_MODULE:
        return bool(importlib.util.find_spec(manifest.module))
    if manifest.type in {ConnectorType.HTTP_LOCAL, ConnectorType.HTTP_REMOTE}:
        return _check_http_endpoint(manifest)
    return False


def _check_http_endpoint(manifest: ConnectorManifest) -> bool:
    parsed = urlparse(manifest.endpoint)
    if manifest.type == ConnectorType.HTTP_LOCAL and (parsed.hostname or "").lower() not in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}:
        return False
    try:
        request = Request(manifest.endpoint, method="HEAD")
        with urlopen(request, timeout=2) as response:
            return response.status < 500
    except Exception:
        try:
            with urlopen(manifest.endpoint, timeout=2) as response:
                return response.status < 500
        except Exception:
            return False


async def _run_health(manifest: ConnectorManifest) -> dict:
    try:
        proc = await asyncio.create_subprocess_exec(
            *manifest.acceptance.health_command,
            cwd=manifest.default_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return {"name": "health", "passed": False, "reason": "health executable not installed"}
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=min(15, manifest.timeout_seconds))
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return {"name": "health", "passed": False, "reason": "health check timed out"}
    output = redact_output((stdout + stderr)[: manifest.output_cap_bytes].decode(errors="replace"))
    return {"name": "health", "passed": proc.returncode == 0, "reason": output[:240] or f"exit_code={proc.returncode}"}
