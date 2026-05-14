# Product Strategy

Eyra is a local-first, voice-first macOS computer-control layer. You speak or type, Eyra plans the route locally, applies privacy and approval rules, coordinates tools or jobs, records what changed, and keeps you able to cancel, undo, or inspect the work.

Eyra is not a general autonomous agent framework. It should not clone every browser agent, coding agent, workflow graph, or multi-agent orchestration library. Those systems can be useful workers; Eyra stays the coordinator.

## Position

Eyra's wedge is not local model support by itself. Local models are becoming common.

Eyra's stronger wedge is the safety and coordination layer around local voice-to-computer work:

- Voice UX: short spoken interaction, barge-in handling, dictation, status, and recovery.
- Route policy: deterministic local planning before any model or tool call.
- Privacy boundaries: clear answers to what stays local and what would leave the machine.
- Approvals: exact-action, server-side approval for risky work.
- Jobs: durable local task lifecycle, logs, artifacts, status, cancellation, and retry.
- Operation ledger: local record of computer-changing actions.
- Undo: reversible local actions are undoable from the ledger.
- Certification: release checks prove the actual configured surface instead of assuming it works.
- Connectors: external services, CLIs, MCP servers, browser agents, coding agents, and local automations can run only as structured workers under Eyra's policy.
- Delegation: specialist agents can run only as optional workers under Eyra's policy.

## What Eyra Is

Eyra is the trusted local coordinator for no-hands macOS operation.

It should help someone control a computer when keyboard and mouse use is inconvenient or unavailable. The core prompts are practical:

- "What am I looking at?"
- "Read the options."
- "Choose number two."
- "Move the latest downloaded file to Documents."
- "Undo that."
- "Start dictation."
- "End dictation."
- "Remind me in 10 minutes."
- "What are you doing?"
- "Cancel that."
- "Approve that."
- "Reject that."
- "Start a coding job with a configured terminal coding agent."
- "Ask OpenClawNew to inspect this folder."
- "Cancel the OpenClawNew job."
- "What would leave my machine?"
- "What can you control?"

The product promise is local coordination first. Optional network, browser, MCP, connector, Realtime, and external-agent paths must be explicit, visible, and approval-aware.

## What Eyra Is Not

Eyra is not a clone of:

- OpenClaw-like personal-agent systems.
- OpenHands or OpenDevin-style coding workspaces.
- browser-use or Playwright agent stacks.
- graph-based and role-based agent frameworks.
- MCP ecosystems as a product surface by themselves.

Eyra should not compete by adding a larger pile of generic tools. It should compete by deciding when a tool is allowed, who approved it, where data goes, what changed, and how to recover.

## Competitor Categories

| Category | Examples | Eyra stance |
| --- | --- | --- |
| Big personal agent systems | OpenClaw-like systems, Hermes-like local agents, large all-in-one autonomous systems | Integrate only when useful through connector manifests. Do not copy the whole surface. Keep local voice, policy, approvals, jobs, undo, and certification in Eyra. |
| Coding agents | terminal coding agents, OpenHands, OpenDevin-style systems, OpenClaw coding skills | Treat them as optional workers. Eyra owns the request, sandbox, approval, timeout, output caps, logs, cancellation, and final status. |
| Browser agents | browser-use, Playwright agents, Operator or Computer-use-style tools | Use them only behind explicit browser/network or connector policy. Eyra owns privacy, redaction, approval, and route traces. |
| Agent frameworks | role-based orchestration, graph runtimes, MCP ecosystems | Use configured connectors and MCP tools where they fit. Do not move Eyra's safety kernel into a framework. |

## Local-First

Default behavior: nothing leaves the machine.

Local-first matters because computer control touches private text, files, windows, clipboard content, screenshots, shell commands, browser state, and account surfaces. Eyra should make the local path useful enough that cloud paths stay optional, not required.

Cloud providers, network tools, browser tools, Realtime voice, MCP bridges, connectors, and external agents can be enabled. When enabled, Eyra must say what may leave the machine and which route allowed it.

## Voice-First

Voice is not a wrapper around chat. Voice changes the product contract.

Eyra needs concise spoken status, interruptible speech, no keyboard-only approval paths for common flows, and direct local handling for commands like stop, cancel, approve, reject, choose a numbered option, start dictation, end dictation, undo, and status.

True acoustic barge-in is hardware-dependent. Eyra can run a normal TTS barge-in path with an echo guard, but physical certification must stay separate from synthetic and code-path tests.

## Deterministic Routing

Every request goes through the local policy router before model execution.

The route decides:

- execution class,
- effort estimate,
- selected model and reason,
- required capabilities,
- risk tier,
- tool allowlist,
- denied tool reasons,
- fallback message,
- privacy summary,
- redacted trace.

Complexity routing is only model-tier selection. It is not the safety boundary.

## Operation Ledger And Undo

Computer-changing actions need durable evidence.

The ledger should record what the user asked for, the normalized action, target, before state, after state, approval id, result, and undo metadata when available. Undo should be honest: reversible file moves and Trash operations can be undone; destructive, remote, OS, MCP, browser, and external-agent actions may require manual recovery.

## External Agents

External agents are workers, not masters.

An adapter must declare:

- name,
- capabilities,
- local or remote behavior,
- config requirements,
- risk tier,
- whether it can mutate files,
- whether it can use network,
- approval requirements,
- timeout,
- output cap,
- cancellation support where possible.

Adapters run only when enabled and configured. They use static argv, sandboxed cwd, redacted capped output, and explicit approval for mutation or delegation. Realtime voice must not call external agents by default.

## Installation And Distribution

Installer UX is part of the product, not a developer afterthought. The first contact should explain what stays local, what is missing, and what is deliberately disabled.

Distribution should stay compatible with the current license and release policy:

- Source checkout remains the developer and private-beta path.
- A release installer can install GitHub release archives, but private repositories require authenticated access.
- Homebrew should use a custom `gabrimatic/eyra` tap, not `homebrew/core`, unless the license and release policy change.
- `uv tool` and `pipx` installs should expose the same commands as source installs: `eyra`, `eyra web`, `eyra doctor`, `eyra setup`, `eyra certify`, `eyra update`, `eyra uninstall`, `eyra version`, and `eyra paths`.
- Updates must preserve `.env`, jobs, triggers, logs, artifacts, and the operation ledger.
- Uninstall must remove command shims first and leave user data alone unless the user explicitly chooses data removal.

Install diagnostics should be support-ready. `eyra doctor --json` should report version, install source, local backend state, model state, Local Whisper state, microphone summary, screen capture state, sandbox roots, Web UI config, optional tool flags, and redacted paths without logging secrets.

## Universal Connectors

Connectors are the general integration contract. They let a user attach a tool or service without Eyra adding custom code for that exact system.

The connector contract should stay narrow:

- manifest-driven configuration,
- static transport such as argv or a declared endpoint,
- explicit local or remote boundary,
- declared data classes,
- declared capabilities,
- risk tier,
- approval policy,
- bounded timeout,
- capped and redacted output,
- acceptance checks before use,
- durable job logs and artifacts,
- cancellation where possible.

Connectors must not become a marketplace or a plugin sandbox escape. They cannot define arbitrary commands from the model, self-approve, disable Eyra policy, bypass filesystem roots, receive undeclared private data, or appear in Realtime voice by default.

Useful connector examples include:

- a local CLI worker such as `openclawnew`,
- an MCP server wrapped as a declared worker,
- a local HTTP automation endpoint,
- a coding agent that can inspect or edit a sandboxed folder after approval,
- a browser agent that runs only when browser/network policy allows it.

Remote connectors are a privacy boundary, not a default feature. They require explicit opt-in and must make the destination and data classes visible before use.

## Release Bar

Every meaningful new capability needs:

- route policy,
- opt-in config where needed,
- safety model,
- approval model when risky,
- job lifecycle,
- cancellation,
- logs or artifacts for long-running work,
- privacy boundary,
- tests,
- documentation,
- certification coverage.
