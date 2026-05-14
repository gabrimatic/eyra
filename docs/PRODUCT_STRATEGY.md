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
- "What would leave my machine?"
- "What can you control?"

The product promise is local coordination first. Optional network, browser, MCP, Realtime, and external-agent paths must be explicit, visible, and approval-aware.

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
| Big personal agent systems | OpenClaw-like systems, Hermes-like local agents, large all-in-one autonomous systems | Integrate only when useful. Do not copy the whole surface. Keep local voice, policy, approvals, jobs, undo, and certification in Eyra. |
| Coding agents | terminal coding agents, OpenHands, OpenDevin-style systems, OpenClaw coding skills | Treat them as optional workers. Eyra owns the request, sandbox, approval, timeout, output caps, logs, cancellation, and final status. |
| Browser agents | browser-use, Playwright agents, Operator or Computer-use-style tools | Use them only behind explicit browser/network policy. Eyra owns privacy, redaction, approval, and route traces. |
| Agent frameworks | role-based orchestration, graph runtimes, MCP ecosystems | Use configured adapters and MCP tools where they fit. Do not move Eyra's safety kernel into a framework. |

## Local-First

Default behavior: nothing leaves the machine.

Local-first matters because computer control touches private text, files, windows, clipboard content, screenshots, shell commands, browser state, and account surfaces. Eyra should make the local path useful enough that cloud paths stay optional, not required.

Cloud providers, network tools, browser tools, Realtime voice, MCP bridges, and external agents can be enabled. When enabled, Eyra must say what may leave the machine and which route allowed it.

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
