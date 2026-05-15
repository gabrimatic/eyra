"""Connector job execution under Eyra policy."""

from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from runtime.connectors.types import (
    ConnectorCwdPolicy,
    ConnectorInputMode,
    ConnectorJobResult,
    ConnectorJobSpec,
    ConnectorManifest,
    ConnectorOutputMode,
    ConnectorType,
)
from runtime.jobs import DurableJobStore, JobStatus, RiskLevel
from tools.approval import ApprovalManager, approval_required_message

_SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|token|secret|password)([\"'\s:=]+)([^\s,}]+)"),
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"(?i)(token=)[^&\s]+"),
    re.compile(r"Bearer\s+[A-Za-z0-9_\-./+=]{16,}"),
)


class ConnectorRunner:
    """Run connector jobs with approval, timeout, output caps, and cancellation."""

    def __init__(
        self,
        *,
        approvals: ApprovalManager | None = None,
        job_store: DurableJobStore | None = None,
    ):
        self.approvals = approvals or ApprovalManager()
        self.job_store = job_store
        self._running: dict[str, asyncio.subprocess.Process] = {}
        self._cancelled: set[str] = set()

    async def run(self, manifest: ConnectorManifest, spec: ConnectorJobSpec) -> ConnectorJobResult:
        if not manifest.enabled:
            return ConnectorJobResult(manifest.id, "disabled", f"Connector {manifest.id} is disabled.", job_id=spec.job_id)
        try:
            cwd = self._resolve_cwd(manifest, spec.cwd)
        except (PermissionError, ValueError) as exc:
            return ConnectorJobResult(manifest.id, "blocked", str(exc), job_id=spec.job_id)
        approval = self._approval_or_none(manifest, spec, cwd)
        if approval is not None:
            self._record_job_state(spec, JobStatus.WAITING_FOR_APPROVAL, approval.content)
            return ConnectorJobResult(
                manifest.id,
                "approval_required",
                approval.content,
                job_id=spec.job_id,
                approval_id=approval.approval_id,
            )
        self._record_job_state(spec, JobStatus.RUNNING, "Connector job started.")
        if manifest.type in {
            ConnectorType.CLI,
            ConnectorType.MCP,
            ConnectorType.BROWSER_AGENT,
            ConnectorType.CODING_AGENT,
            ConnectorType.PYTHON_MODULE,
        }:
            result = await self._run_cli(manifest, spec, cwd)
        elif manifest.type in {ConnectorType.HTTP_LOCAL, ConnectorType.HTTP_REMOTE}:
            result = await asyncio.to_thread(self._run_http, manifest, spec, cwd)
        else:
            result = ConnectorJobResult(manifest.id, "blocked", f"Connector type {manifest.type.value} is not runnable locally yet.", job_id=spec.job_id)
        self._record_result(spec, result, manifest)
        return result

    def cancel(self, key: str) -> bool:
        proc = self._running.get(key)
        if proc is None or proc.returncode is not None:
            return False
        for alias, running_proc in list(self._running.items()):
            if running_proc is proc:
                self._cancelled.add(alias)
        proc.kill()
        return True

    async def _run_cli(self, manifest: ConnectorManifest, spec: ConnectorJobSpec, cwd: Path) -> ConnectorJobResult:
        started = time.time()
        argv = manifest.command or ((sys.executable, "-m", manifest.module) if manifest.type == ConnectorType.PYTHON_MODULE else ())
        if not argv:
            return ConnectorJobResult(manifest.id, "blocked", "Connector has no static transport.", job_id=spec.job_id)
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=cwd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            return ConnectorJobResult(manifest.id, "unavailable", f"Connector command is not installed: {argv[0]}", job_id=spec.job_id)
        except OSError as exc:
            return ConnectorJobResult(manifest.id, "failed", f"Could not start connector: {exc}", job_id=spec.job_id)
        keys = {manifest.id}
        if spec.job_id:
            keys.add(spec.job_id)
        for key in keys:
            self._running[key] = proc
        try:
            try:
                payload = self._input_bytes(manifest, spec, cwd)
            except (PermissionError, ValueError) as exc:
                proc.kill()
                await proc.wait()
                return ConnectorJobResult(manifest.id, "blocked", str(exc), job_id=spec.job_id, exit_code=proc.returncode)
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(payload), timeout=manifest.timeout_seconds)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return ConnectorJobResult(
                    manifest.id,
                    "timeout",
                    f"Connector timed out after {manifest.timeout_seconds}s.",
                    job_id=spec.job_id,
                    exit_code=proc.returncode,
                )
        finally:
            for key in keys:
                if self._running.get(key) is proc:
                    self._running.pop(key, None)
        was_cancelled = any(key in self._cancelled for key in keys)
        for key in keys:
            self._cancelled.discard(key)
        if was_cancelled:
            return ConnectorJobResult(
                manifest.id,
                "cancelled",
                "Connector job cancelled.",
                job_id=spec.job_id,
                exit_code=proc.returncode,
                logs=({"level": "info", "message": "Connector process cancelled.", "exitCode": proc.returncode},),
            )
        raw = stdout + stderr
        clipped = raw[: manifest.output_cap_bytes]
        suffix = "\n[output clipped]" if len(raw) > len(clipped) else ""
        output = redact_output(clipped.decode(errors="replace")) + suffix
        if manifest.output_mode == ConnectorOutputMode.STDOUT_JSON and stdout:
            try:
                parsed = json.loads(stdout[: manifest.output_cap_bytes].decode(errors="replace"))
            except json.JSONDecodeError:
                if proc.returncode == 0:
                    return ConnectorJobResult(manifest.id, "failed", "Connector returned invalid JSON.", job_id=spec.job_id, exit_code=proc.returncode)
            else:
                output = redact_output(json.dumps(parsed, indent=2, sort_keys=True)) + suffix
        status = "completed" if proc.returncode == 0 else "failed"
        return ConnectorJobResult(
            manifest.id,
            status,
            output,
            job_id=spec.job_id,
            exit_code=proc.returncode,
            artifacts=(
                {
                    "connectorId": manifest.id,
                    "cwd": redact_output(str(cwd)),
                    "outputBytes": len(raw),
                    "durationSeconds": round(time.time() - started, 3),
                },
            ),
            logs=({"level": "info", "message": "Connector process exited.", "exitCode": proc.returncode},),
        )

    def _run_http(self, manifest: ConnectorManifest, spec: ConnectorJobSpec, cwd: Path) -> ConnectorJobResult:
        parsed = urlparse(manifest.endpoint)
        if manifest.type == ConnectorType.HTTP_LOCAL and (parsed.hostname or "").lower() not in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}:
            return ConnectorJobResult(manifest.id, "blocked", "http_local connector endpoint is not local.", job_id=spec.job_id)
        try:
            payload = self._input_bytes(manifest, spec, cwd) or b"{}"
        except (PermissionError, ValueError) as exc:
            return ConnectorJobResult(manifest.id, "blocked", str(exc), job_id=spec.job_id)
        req = Request(manifest.endpoint, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        started = time.time()
        try:
            with urlopen(req, timeout=manifest.timeout_seconds) as response:
                raw = response.read(manifest.output_cap_bytes + 1)
        except Exception as exc:
            return ConnectorJobResult(manifest.id, "failed", f"HTTP connector failed: {exc}", job_id=spec.job_id)
        clipped = raw[: manifest.output_cap_bytes]
        suffix = "\n[output clipped]" if len(raw) > len(clipped) else ""
        return ConnectorJobResult(
            manifest.id,
            "completed",
            redact_output(clipped.decode(errors="replace")) + suffix,
            job_id=spec.job_id,
            artifacts=(
                {
                    "connectorId": manifest.id,
                    "endpoint": _redact_endpoint(manifest.endpoint),
                    "outputBytes": len(raw),
                    "durationSeconds": round(time.time() - started, 3),
                },
            ),
            logs=({"level": "info", "message": "HTTP connector completed."},),
        )

    def _input_bytes(self, manifest: ConnectorManifest, spec: ConnectorJobSpec, cwd: Path) -> bytes | None:
        if manifest.input_mode == ConnectorInputMode.NONE:
            return None
        payload = self._input_payload(manifest, spec, cwd)
        if manifest.input_mode == ConnectorInputMode.STDIN_TEXT:
            if "task" not in payload:
                raise ValueError("Connector privacy.dataSent must include 'task' for text input.")
            return str(payload["task"]).encode()
        return json.dumps(payload, separators=(",", ":")).encode()

    def _input_payload(self, manifest: ConnectorManifest, spec: ConnectorJobSpec, cwd: Path) -> dict[str, object]:
        declared = set(manifest.privacy.data_sent if manifest.privacy else ())
        payload: dict[str, object] = {
            "connectorId": manifest.id,
            "jobId": spec.job_id,
        }
        if spec.task:
            if "task" not in declared:
                raise ValueError("Connector task is not declared in privacy.dataSent.")
            payload["task"] = spec.task
        if "cwd" in declared:
            payload["cwd"] = str(cwd)
        if spec.selected_files:
            if "selected_files" not in declared:
                raise ValueError("Connector selected files are not declared in privacy.dataSent.")
            payload["selectedFiles"] = [str(self._resolve_selected_file(manifest, item)) for item in spec.selected_files]
        if "file_contents" in declared:
            raise ValueError("Connector file_contents payloads are not supported by this runner yet.")
        forbidden = declared.intersection({"screenshot", "clipboard", "pdf", "pdf_text"})
        if forbidden:
            raise ValueError(f"Connector runner cannot send declared data class: {sorted(forbidden)[0]}.")
        return payload

    def _resolve_selected_file(self, manifest: ConnectorManifest, selected: str) -> Path:
        candidate = Path(selected).expanduser().resolve()
        if not candidate.is_file():
            raise PermissionError(f"Access denied: selected file {redact_output(str(candidate))} is not a file.")
        if not any(candidate == root or root in candidate.parents for root in manifest.allowed_roots):
            raise PermissionError(f"Access denied: selected file {redact_output(str(candidate))} is outside connector sandbox.")
        return candidate

    def _resolve_cwd(self, manifest: ConnectorManifest, requested: str) -> Path:
        base = manifest.default_path
        if manifest.cwd_policy == ConnectorCwdPolicy.REQUEST and requested:
            base = Path(requested).expanduser()
        elif manifest.cwd_policy == ConnectorCwdPolicy.MANIFEST:
            base = Path(manifest.cwd).expanduser()
        resolved = base.resolve()
        if not any(resolved == root or root in resolved.parents for root in manifest.allowed_roots):
            raise PermissionError(f"Access denied: {redact_output(str(resolved))} is outside connector sandbox.")
        return resolved

    def _approval_or_none(self, manifest: ConnectorManifest, spec: ConnectorJobSpec, cwd: Path) -> _ApprovalResult | None:
        if not manifest.needs_approval:
            return None
        details = {
            "connectorId": manifest.id,
            "riskTier": manifest.risk_tier.value,
            "capabilities": list(manifest.capabilities),
            "privacy": {
                "dataSent": list(manifest.privacy.data_sent if manifest.privacy else ()),
                "destination": manifest.privacy.destination if manifest.privacy else "",
                "leavesMachine": bool(manifest.privacy and manifest.privacy.leaves_machine),
            },
            "cwd": redact_output(str(cwd)),
            "taskLength": len(spec.task),
        }
        title = f"connector job: {manifest.display_name}"
        if spec.approval_id and self.approvals.consume(spec.approval_id, "run_connector_task", title, details):
            return None
        pending = self.approvals.request("run_connector_task", title, details)
        return _ApprovalResult(content=approval_required_message(pending), approval_id=pending.id)

    def _record_job_state(self, spec: ConnectorJobSpec, status: JobStatus, message: str) -> None:
        if self.job_store is None or not spec.job_id:
            return
        self.job_store.update_job(spec.job_id, status=status, current_step=message)
        self.job_store.record_log(spec.job_id, message, data={"connectorId": spec.connector_id})

    def _record_result(self, spec: ConnectorJobSpec, result: ConnectorJobResult, manifest: ConnectorManifest) -> None:
        if self.job_store is None or not spec.job_id:
            return
        status = {
            "completed": JobStatus.COMPLETED,
            "failed": JobStatus.FAILED,
            "timeout": JobStatus.FAILED,
            "cancelled": JobStatus.CANCELLED,
            "blocked": JobStatus.BLOCKED,
            "unavailable": JobStatus.BLOCKED,
        }.get(result.status, JobStatus.FAILED)
        self.job_store.update_job(
            spec.job_id,
            status=status,
            final_result=result.output,
            artifacts=list(result.artifacts),
            error=None if status == JobStatus.COMPLETED else result.output,
        )
        for log in result.logs:
            self.job_store.record_log(
                spec.job_id,
                str(log.get("message", "")),
                level=str(log.get("level", "info")),
                data={key: value for key, value in log.items() if key not in {"message", "level"}},
            )
        self.job_store.record_operation(
            job_id=spec.job_id,
            user_request="connector job",
            normalized_action={"type": "connector.run", "connector_id": spec.connector_id},
            capability="connector",
            target=spec.connector_id,
            before_state={"status": "queued"},
            after_state={"status": result.status},
            risk_level=_job_risk(manifest),
            success=status == JobStatus.COMPLETED,
            undo={},
            error=None if status == JobStatus.COMPLETED else result.output,
        )


class _ApprovalResult:
    def __init__(self, *, content: str, approval_id: str):
        self.content = content
        self.approval_id = approval_id


def redact_output(text: str) -> str:
    redacted = text
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(lambda match: _redact_match(match), redacted)
    redacted = re.sub(r"/Users/[^/\s]+", "~/[user]", redacted)
    redacted = re.sub(r"(?:/private)?/var/folders/[^\s,}\"']+", "~/[temp]", redacted)
    return re.sub(r"/tmp/[^\s,}\"']+", "~/[temp]", redacted)


def _redact_match(match: re.Match) -> str:
    if match.lastindex and match.lastindex >= 2:
        return f"{match.group(1)}{match.group(2)}[REDACTED]"
    if match.lastindex == 1:
        return f"{match.group(1)}[REDACTED]"
    return "[REDACTED]"


def _job_risk(manifest: ConnectorManifest) -> RiskLevel:
    if manifest.remote:
        return RiskLevel.IRREVERSIBLE_OR_EXTERNAL
    if manifest.can_mutate_files or manifest.can_control_ui or manifest.can_run_shell:
        return RiskLevel.MEDIUM_RISK_CHANGE
    if manifest.can_read_files:
        return RiskLevel.READ_ONLY
    return RiskLevel.READ_ONLY


def _redact_endpoint(endpoint: str) -> str:
    parsed = urlparse(endpoint)
    if not parsed.scheme or not parsed.netloc:
        return redact_output(endpoint)
    return f"{parsed.scheme}://{parsed.hostname or '[host]'}"
