"""Connector registry and capability snapshots."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from runtime.connectors.certification import initial_acceptance_state, run_acceptance
from runtime.connectors.manifest import load_connector_config
from runtime.connectors.runner import ConnectorRunner, redact_output
from runtime.connectors.types import (
    AcceptanceState,
    ConnectorAcceptanceResult,
    ConnectorConfigLoadResult,
    ConnectorJobResult,
    ConnectorJobSpec,
    ConnectorManifest,
)
from runtime.jobs import DurableJobStore
from tools.approval import ApprovalManager
from utils.settings import Settings


class ConnectorRegistry:
    """Validated connector registry. Connectors are workers under Eyra policy."""

    def __init__(
        self,
        *,
        enabled: bool,
        config: ConnectorConfigLoadResult,
        approvals: ApprovalManager | None = None,
        job_store: DurableJobStore | None = None,
    ):
        self.enabled = enabled
        self.config = config
        self._manifests = {manifest.id: manifest for manifest in config.manifests}
        self._overrides: dict[str, bool] = {}
        self._acceptance: dict[str, ConnectorAcceptanceResult] = {
            manifest.id: ConnectorAcceptanceResult(
                manifest.id,
                initial_acceptance_state(manifest),
                "Connector is configured and awaiting acceptance."
                if manifest.enabled
                else "Connector is disabled in manifest.",
            )
            for manifest in config.manifests
        }
        self.runner = ConnectorRunner(approvals=approvals, job_store=job_store)

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        approvals: ApprovalManager | None = None,
        job_store: DurableJobStore | None = None,
    ) -> "ConnectorRegistry":
        enabled = bool(settings.CONNECTORS_ENABLED)
        config = load_connector_config(settings) if enabled else ConnectorConfigLoadResult(status="disabled", reason="CONNECTORS_ENABLED=false")
        return cls(enabled=enabled, config=config, approvals=approvals, job_store=job_store)

    def get(self, connector_id: str) -> ConnectorManifest | None:
        manifest = self._manifests.get(connector_id)
        if manifest is None:
            return None
        if connector_id in self._overrides:
            return replace(manifest, enabled=self._overrides[connector_id])
        return manifest

    def list_connectors(self) -> list[dict[str, Any]]:
        return [self.snapshot_for(connector_id) for connector_id in sorted(self._manifests)]

    def snapshot_for(self, connector_id: str) -> dict[str, Any]:
        manifest = self.get(connector_id)
        if manifest is None:
            return {"id": connector_id, "status": "unknown"}
        acceptance = self._acceptance.get(connector_id)
        privacy = manifest.privacy
        return {
            "id": manifest.id,
            "displayName": manifest.display_name,
            "type": manifest.type.value,
            "enabled": manifest.enabled,
            "acceptanceState": acceptance.state.value if acceptance else AcceptanceState.CONFIGURED.value,
            "acceptanceReason": acceptance.reason if acceptance else "",
            "riskTier": manifest.risk_tier.value,
            "requiresApproval": manifest.needs_approval,
            "capabilities": list(manifest.capabilities),
            "privacy": {
                "dataSent": list(privacy.data_sent if privacy else ()),
                "destination": redact_output(privacy.destination) if privacy else "",
                "leavesMachine": bool(privacy and privacy.leaves_machine),
            },
            "local": manifest.local,
            "remote": manifest.remote,
            "cwdPolicy": manifest.cwd_policy.value,
            "timeoutSeconds": manifest.timeout_seconds,
            "outputCapBytes": manifest.output_cap_bytes,
        }

    def capability_snapshot(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "config": {
                "status": self.config.status,
                "reason": self.config.reason,
                "errors": list(self.config.errors),
            },
            "connectors": self.list_connectors(),
        }

    async def run(self, spec: ConnectorJobSpec) -> ConnectorJobResult:
        if not self.enabled:
            return ConnectorJobResult(spec.connector_id, "disabled", "Connectors are disabled. Set CONNECTORS_ENABLED=true.", job_id=spec.job_id)
        manifest = self.get(spec.connector_id)
        if manifest is None:
            return ConnectorJobResult(spec.connector_id, "unknown", f"Connector '{spec.connector_id}' is not configured.", job_id=spec.job_id)
        acceptance = self._acceptance.get(spec.connector_id)
        if acceptance and acceptance.state != AcceptanceState.ACCEPTED:
            return ConnectorJobResult(
                spec.connector_id,
                "blocked",
                f"Connector must pass acceptance before use. Current state: {acceptance.state.value}.",
                job_id=spec.job_id,
            )
        try:
            return await self.runner.run(manifest, spec)
        except PermissionError as exc:
            return ConnectorJobResult(spec.connector_id, "blocked", str(exc), job_id=spec.job_id)

    def cancel(self, key: str) -> bool:
        return self.runner.cancel(key)

    async def test(self, connector_id: str) -> ConnectorAcceptanceResult:
        manifest = self.get(connector_id)
        if manifest is None:
            result = ConnectorAcceptanceResult(connector_id, AcceptanceState.NOT_CONFIGURED, "Connector is not configured.")
        elif not self.enabled:
            result = ConnectorAcceptanceResult(connector_id, AcceptanceState.NOT_CONFIGURED, "Connectors are disabled.")
        else:
            result = await run_acceptance(manifest, runner=self.runner)
        self._acceptance[connector_id] = result
        return result

    def set_enabled(self, connector_id: str, enabled: bool) -> bool:
        if connector_id not in self._manifests:
            return False
        self._overrides[connector_id] = enabled
        state = AcceptanceState.CONFIGURED if enabled else AcceptanceState.DISABLED
        self._acceptance[connector_id] = ConnectorAcceptanceResult(
            connector_id,
            state,
            "Connector enabled for this session." if enabled else "Connector disabled for this session.",
        )
        return True

    def route_trace_metadata(self, connector_id: str) -> dict[str, Any]:
        snapshot = self.snapshot_for(connector_id)
        if snapshot.get("status") == "unknown":
            return {"connectorId": connector_id, "status": "unknown"}
        return {
            "connectorId": connector_id,
            "riskTier": snapshot["riskTier"],
            "capabilities": snapshot["capabilities"],
            "privacyBoundary": snapshot["privacy"],
            "requiresApproval": snapshot["requiresApproval"],
            "acceptanceState": snapshot["acceptanceState"],
        }


def format_connector_list(registry: ConnectorRegistry) -> str:
    if not registry.enabled:
        return "Connectors are disabled. Set CONNECTORS_ENABLED=true."
    snapshot = registry.capability_snapshot()
    if snapshot["config"]["status"] != "loaded":
        return f"Connector config {snapshot['config']['status']}: {snapshot['config']['reason']}"
    connectors = snapshot["connectors"]
    if not connectors:
        return "No connectors configured."
    lines = ["Connectors"]
    for item in connectors:
        state = item["acceptanceState"]
        enabled = "on" if item["enabled"] else "off"
        caps = ", ".join(item["capabilities"]) or "none"
        lines.append(f"- {item['id']} ({enabled}, {state}, {item['riskTier']}): {caps}")
    return "\n".join(lines)


def format_connector_detail(registry: ConnectorRegistry, connector_id: str) -> str:
    item = registry.snapshot_for(connector_id)
    if item.get("status") == "unknown":
        return f"Connector '{connector_id}' is not configured."
    privacy = item["privacy"]
    return "\n".join(
        [
            f"{item['displayName']} ({item['id']})",
            f"type: {item['type']}",
            f"enabled: {'yes' if item['enabled'] else 'no'}",
            f"acceptance: {item['acceptanceState']} - {item['acceptanceReason']}",
            f"risk: {item['riskTier']}",
            f"approval required: {'yes' if item['requiresApproval'] else 'no'}",
            "capabilities: " + (", ".join(item["capabilities"]) or "none"),
            f"privacy: sends {', '.join(privacy['dataSent']) or 'nothing declared'} to {privacy['destination']}; leaves machine: {privacy['leavesMachine']}",
        ]
    )


def redact_connector_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: redact_connector_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_connector_payload(item) for item in value]
    if isinstance(value, tuple):
        return [redact_connector_payload(item) for item in value]
    if isinstance(value, str):
        return redact_output(value)
    return value
