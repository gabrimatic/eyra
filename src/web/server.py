"""Small built-in Web UI for phone and browser access."""

from __future__ import annotations

import asyncio
import hmac
import json
import secrets
import shutil
import socketserver
import subprocess
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from chat.complexity_scorer import ComplexityScorer
from chat.message_handler import process_task_stream
from chat.session_state import InteractionStyle, QualityMode
from runtime.models import PreflightResult
from runtime.tasks import BackgroundTask, BackgroundTaskManager, TaskStatus
from runtime.tooling import build_tool_registry
from runtime.vision import analyze_screen, vision_model_name
from tools.approval import ApprovalManager
from tools.browser import BrowserSession
from utils.settings import Settings

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
_SAFE_REALTIME_TOOLS = {"get_current_time", "discover_capabilities"}


class EyraThreadingHTTPServer(ThreadingHTTPServer):
    """HTTP server that avoids reverse-DNS lookup on 0.0.0.0 binds."""

    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = str(host)
        self.server_port = int(port)


def build_health_payload(settings: Settings) -> dict[str, Any]:
    return {
        "status": "ok",
        "offlineByDefault": True,
        "web": {
            "enabled": settings.WEB_UI_ENABLED,
            "host": settings.WEB_UI_HOST,
            "port": settings.WEB_UI_PORT,
            "authRequired": web_auth_required(settings),
        },
        "model": {
            "main": settings.MODEL,
            "worker": settings.WORKER_MODEL or settings.MODEL,
            "vision": vision_model_name(settings),
        },
        "voice": {
            "localWhisper": settings.LIVE_LISTENING_ENABLED or settings.LIVE_SPEECH_ENABLED,
            "realtime": settings.REALTIME_VOICE_ENABLED,
            "realtimeModel": settings.REALTIME_MODEL,
            "realtimeTools": settings.REALTIME_TOOLS_ENABLED,
        },
        "tools": {
            "network": settings.NETWORK_TOOLS_ENABLED,
            "os": settings.OS_TOOLS_ENABLED,
            "agents": settings.AGENT_TOOLS_ENABLED,
            "mcp": settings.MCP_TOOLS_ENABLED,
        },
    }


def web_auth_required(settings: Settings) -> bool:
    mode = settings.WEB_UI_REQUIRE_TOKEN.strip().lower()
    if settings.WEB_UI_HOST not in _LOCAL_HOSTS:
        return True
    if mode == "true":
        return True
    if mode == "false":
        return False
    return False


def validate_request_size(settings: Settings, length: int) -> bool:
    return 0 <= length <= max(1, int(settings.WEB_UI_MAX_REQUEST_BYTES))


def _network_request(text: str) -> bool:
    lowered = text.lower()
    return "http://" in lowered or "https://" in lowered or any(
        phrase in lowered for phrase in ("website", "web page", "webpage", "weather", "browse", "search the web")
    )


def _background_request(text: str) -> bool:
    lowered = text.lower()
    return bool(
        "pdf" in lowered
        or any(word in lowered for word in ("summarize", "organize", "inspect", "translate", "website", "folder"))
    )


def _task_payload(task: BackgroundTask) -> dict[str, Any]:
    return {
        "id": task.id,
        "title": task.title,
        "request": task.original_request,
        "status": task.status.value,
        "progress": task.progress_summary,
        "result": task.final_result,
        "error": task.error,
        "createdAt": task.created_at,
        "updatedAt": task.updated_at,
        "needsUserInput": task.needs_user_input,
        "requiredNetwork": task.required_network,
        "requiredFilesystem": task.required_filesystem,
        "requiredVision": task.required_vision,
    }


class WebAssistantRuntime:
    """Standalone assistant runtime for the built-in Web UI."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.scorer = ComplexityScorer()
        self.conversation: list[dict[str, str]] = []
        self.browser_session = BrowserSession()
        self.approvals = ApprovalManager()
        self.registry = build_tool_registry(
            settings,
            browser_session=self.browser_session,
            approval_manager=self.approvals,
        )
        self.model_semaphore = asyncio.Semaphore(max(1, int(settings.MODEL_CONCURRENCY)))
        self.task_manager = BackgroundTaskManager(
            max_concurrent=max(1, int(settings.MAX_BACKGROUND_TASKS)),
            task_timeout_seconds=max(1, int(settings.TASK_TIMEOUT_SECONDS)),
        )
        self.preflight = PreflightResult(
            backend_reachable=True,
            models_ready=settings.all_model_names,
            screen_capture_available=bool(shutil.which("screencapture")),
        )
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, name="eyra-web-runtime", daemon=True)
        self._thread.start()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def run_sync(self, coro, timeout: float = 30.0):
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    async def handle_message(self, text: str, voice_mode: str = "text") -> dict[str, Any]:
        text = " ".join(text.strip().split())
        if not text:
            return {"reply": "Message is empty."}
        if _network_request(text) and not self.settings.NETWORK_TOOLS_ENABLED:
            return {
                "reply": (
                    "Network tools are disabled. Enable NETWORK_TOOLS_ENABLED=true before asking Eyra to browse, "
                    "summarize websites, or check weather."
                )
            }
        self.conversation.append({"role": "user", "content": text})
        if _background_request(text):
            task = self.task_manager.create_task(
                title=text[:48] or "Task",
                original_request=text,
                worker=lambda task: self._run_worker_task(task, text, voice_mode),
                related_context=list(self.conversation[-6:]),
                used_tools=True,
                required_network=_network_request(text),
                required_filesystem=any(word in text.lower() for word in ("pdf", "file", "folder")),
                required_vision="screen" in text.lower() or "looking at" in text.lower(),
            )
            return {"reply": f"Task {task.id} accepted: {task.title}", "taskId": task.id}
        reply = await self._chat(text, voice_mode)
        return {"reply": reply}

    async def _chat(self, text: str, voice_mode: str = "text") -> str:
        interaction = InteractionStyle.VOICE if voice_mode in ("local", "realtime") else InteractionStyle.TEXT
        chunks: list[str] = []
        async with self.model_semaphore:
            async for chunk in process_task_stream(
                text_content=text,
                complexity_scorer=self.scorer,
                settings=self.settings,
                messages=self.conversation,
                quality_mode=QualityMode.BALANCED,
                interaction_style=interaction,
                tool_registry=self.registry,
            ):
                chunks.append(chunk)
        reply = "".join(chunks).strip() or "No response."
        self.conversation.append({"role": "assistant", "content": reply})
        return reply

    async def _run_worker_task(self, task: BackgroundTask, text: str, voice_mode: str) -> str:
        task.mark_progress("Working")
        if "screen" in text.lower() or "looking at" in text.lower():
            task.mark_progress("Capturing screenshot locally")
            return await analyze_screen(
                settings=self.settings,
                prompt=text,
                conversation_messages=list(task.related_context),
                current_goal=None,
                model_semaphore=self.model_semaphore,
                preflight=self.preflight,
            )
        chunks: list[str] = []
        interaction = InteractionStyle.VOICE if voice_mode in ("local", "realtime") else InteractionStyle.TEXT
        async with self.model_semaphore:
            async for chunk in process_task_stream(
                text_content=text,
                complexity_scorer=self.scorer,
                settings=self.settings,
                messages=list(task.related_context) or [{"role": "user", "content": text}],
                quality_mode=QualityMode.BALANCED,
                interaction_style=interaction,
                tool_registry=self.registry,
                require_tools=True,
            ):
                chunks.append(chunk)
                if len("".join(chunks)) > 120 and task.progress_summary == "Working":
                    task.mark_progress("Preparing final answer")
        return "".join(chunks).strip() or "Task finished."

    async def list_tasks(self) -> dict[str, Any]:
        return {"tasks": [_task_payload(task) for task in self.task_manager.list_tasks(include_recent=True)]}

    async def task_detail(self, task_id: str) -> dict[str, Any]:
        task = self.task_manager.get_task(task_id)
        if task is None:
            return {"error": "No task found."}
        return {"task": _task_payload(task)}

    async def cancel_task(self, task_id: str) -> dict[str, Any]:
        task = self.task_manager.get_task(task_id)
        if task is None:
            return {"error": "No task found.", "status": "missing"}
        if task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
            return {"status": task.status.value}
        self.task_manager.cancel_task(task_id)
        await self.task_manager.wait_for_task(task_id)
        return {"status": task.status.value}

    async def list_approvals(self) -> dict[str, Any]:
        return {
            "approvals": [
                {
                    "id": approval.id,
                    "tool": approval.tool_name,
                    "title": approval.title,
                    "details": approval.details,
                    "expiresAt": approval.expires_at,
                }
                for approval in self.approvals.list_pending()
            ]
        }

    async def approve(self, approval_id: str) -> dict[str, Any]:
        return {"approved": self.approvals.approve(approval_id)}

    async def reject(self, approval_id: str) -> dict[str, Any]:
        return {"rejected": self.approvals.reject(approval_id)}

    async def shutdown(self) -> None:
        await self.task_manager.shutdown()
        await self.browser_session.close()

    def close(self) -> None:
        try:
            self.run_sync(self.shutdown(), timeout=10)
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=2)


def render_index_html(settings: Settings) -> str:
    realtime_label = "Realtime" if settings.REALTIME_VOICE_ENABLED else "Realtime off"
    local_label = "Local Whisper"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <title>Eyra</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #0b0f14;
      --panel: #101820;
      --panel-2: #16212b;
      --text: #edf6f9;
      --muted: #94a8b4;
      --line: #263746;
      --accent: #55d6be;
      --accent-2: #f6bd60;
      --danger: #f28482;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at 20% -10%, rgba(85, 214, 190, 0.16), transparent 30%),
        linear-gradient(180deg, #0b0f14 0%, #0f151c 100%);
      color: var(--text);
    }}
    main {{
      width: min(920px, 100%);
      min-height: 100vh;
      margin: 0 auto;
      display: grid;
      grid-template-rows: auto 1fr auto;
      padding: 18px;
      gap: 14px;
    }}
    header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      border-bottom: 1px solid var(--line);
      padding-bottom: 14px;
    }}
    h1 {{
      margin: 0;
      font-size: 22px;
      letter-spacing: 0;
    }}
    .status {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      justify-content: flex-end;
      color: var(--muted);
      font-size: 13px;
    }}
    .pill {{
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 999px;
      padding: 6px 10px;
      white-space: nowrap;
    }}
    #messages {{
      overflow: auto;
      display: flex;
      flex-direction: column;
      gap: 10px;
      padding: 4px 0;
    }}
    .msg {{
      max-width: 86%;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 11px 12px;
      line-height: 1.45;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }}
    .user {{
      align-self: flex-end;
      background: #13352f;
      border-color: rgba(85, 214, 190, 0.32);
    }}
    .eyra {{
      align-self: flex-start;
      background: var(--panel);
    }}
    .error {{
      border-color: rgba(242, 132, 130, 0.65);
      color: #ffd7d7;
    }}
    #tasks {{
      border-top: 1px solid var(--line);
      padding-top: 10px;
      display: grid;
      gap: 8px;
      color: var(--muted);
      font-size: 13px;
    }}
    .task {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 8px;
      align-items: center;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 8px 10px;
      background: rgba(16, 24, 32, 0.74);
    }}
    .task button {{
      min-height: 34px;
      padding: 0 10px;
    }}
    form {{
      display: grid;
      grid-template-columns: 154px minmax(0, 1fr) 56px 92px;
      gap: 10px;
      align-items: end;
      border-top: 1px solid var(--line);
      padding-top: 14px;
    }}
    textarea {{
      width: 100%;
      min-height: 48px;
      max-height: 160px;
      resize: vertical;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      color: var(--text);
      padding: 12px;
      font: inherit;
    }}
    button, select {{
      min-height: 48px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel-2);
      color: var(--text);
      font: inherit;
      padding: 0 14px;
    }}
    #micButton {{
      padding: 0;
      width: 56px;
    }}
    button.primary {{
      background: var(--accent);
      color: #06211d;
      border-color: var(--accent);
      font-weight: 700;
    }}
    #micButton.active {{
      border-color: var(--accent-2);
      color: var(--accent-2);
    }}
    @media (max-width: 640px) {{
      main {{ padding: 12px; }}
      header {{ align-items: flex-start; flex-direction: column; }}
      .status {{ justify-content: flex-start; }}
      .msg {{ max-width: 94%; }}
      form {{ grid-template-columns: 1fr auto; }}
      select {{ grid-column: 1 / -1; }}
      textarea {{ grid-column: 1 / 2; }}
      #micButton {{ grid-column: 2 / 3; }}
      button.primary {{ grid-column: 1 / -1; }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>Eyra</h1>
      <div class="status">
        <span class="pill">{escape(local_label)}</span>
        <span class="pill">{escape(realtime_label)}</span>
        <span class="pill">Network {'on' if settings.NETWORK_TOOLS_ENABLED else 'off'}</span>
        <span class="pill">OS tools {'on' if settings.OS_TOOLS_ENABLED else 'off'}</span>
        <span class="pill">MCP {'on' if settings.MCP_TOOLS_ENABLED else 'off'}</span>
      </div>
    </header>
    <section id="messages" aria-live="polite"></section>
    <section id="tasks" aria-live="polite"></section>
    <form id="chatForm">
      <select id="voiceMode" aria-label="Voice mode">
        <option value="text">Text</option>
        <option value="local">Local Whisper</option>
        <option value="realtime">Realtime</option>
      </select>
      <textarea id="prompt" name="prompt" placeholder="Ask Eyra about this Mac..." autocomplete="off"></textarea>
      <button id="micButton" type="button" title="Voice input">Mic</button>
      <button class="primary" type="submit">Send</button>
    </form>
  </main>
  <script>
    const messages = document.getElementById('messages');
    const form = document.getElementById('chatForm');
    const prompt = document.getElementById('prompt');
    const micButton = document.getElementById('micButton');
    const voiceMode = document.getElementById('voiceMode');
    const tasks = document.getElementById('tasks');

    function addMessage(role, text, extraClass = '') {{
      const el = document.createElement('div');
      el.className = `msg ${{role}} ${{extraClass}}`;
      el.textContent = text;
      messages.appendChild(el);
      messages.scrollTop = messages.scrollHeight;
      return el;
    }}

    async function send(text) {{
      addMessage('user', text);
      const reply = addMessage('eyra', 'Thinking...');
      try {{
        const response = await fetch('/api/chat', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json', 'X-Eyra-Web-Token': webToken }},
          body: JSON.stringify({{ text, voiceMode: voiceMode.value }}),
        }});
        const data = await response.json();
        reply.textContent = data.reply || data.error || '';
        if (!response.ok) reply.classList.add('error');
        loadTasks();
      }} catch (error) {{
        reply.textContent = 'Could not reach Eyra on this machine.';
        reply.classList.add('error');
      }}
    }}

    async function loadTasks() {{
      try {{
        const response = await fetch('/api/tasks', {{ headers: {{ 'X-Eyra-Web-Token': webToken }} }});
        if (!response.ok) return;
        const data = await response.json();
        tasks.replaceChildren();
        for (const task of (data.tasks || []).slice(0, 8)) {{
          const row = document.createElement('div');
          row.className = 'task';
          const label = document.createElement('div');
          label.textContent = `${{task.id}} · ${{task.status}} · ${{task.title}}`;
          row.appendChild(label);
          if (['queued', 'running'].includes(task.status)) {{
            const button = document.createElement('button');
            button.textContent = 'Cancel';
            button.onclick = async () => {{
              await fetch('/api/cancel', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json', 'X-Eyra-Web-Token': webToken }},
                body: JSON.stringify({{ taskId: task.id }}),
              }});
              loadTasks();
            }};
            row.appendChild(button);
          }}
          tasks.appendChild(row);
        }}
      }} catch (_) {{}}
    }}

    form.addEventListener('submit', (event) => {{
      event.preventDefault();
      const text = prompt.value.trim();
      if (!text) return;
      prompt.value = '';
      send(text);
    }});

    let recorder = null;
    let chunks = [];
    let realtime = null;
    const webToken = new URLSearchParams(window.location.search).get('token') || sessionStorage.getItem('eyraWebToken') || '';
    if (webToken) sessionStorage.setItem('eyraWebToken', webToken);

    async function sendLocalAudio(blob) {{
      const reply = addMessage('eyra', 'Listening...');
      try {{
        const response = await fetch('/api/local-voice-turn', {{
          method: 'POST',
          headers: {{ 'Content-Type': blob.type || 'application/octet-stream', 'X-Eyra-Web-Token': webToken }},
          body: blob,
        }});
        const data = await response.json();
        reply.textContent = data.reply || data.error || '';
        if (data.transcript) addMessage('user', data.transcript);
        if (data.reply) speakLocal(data.reply);
        if (!response.ok) reply.classList.add('error');
      }} catch (_) {{
        reply.textContent = 'Local voice failed on this machine.';
        reply.classList.add('error');
      }}
    }}

    async function speakLocal(text) {{
      try {{
        await fetch('/api/local-speak', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json', 'X-Eyra-Web-Token': webToken }},
          body: JSON.stringify({{ text }}),
        }});
      }} catch (_) {{}}
    }}

    async function toggleLocalRecording() {{
      if (recorder && recorder.state === 'recording') {{
        recorder.stop();
        micButton.classList.remove('active');
        micButton.textContent = 'Mic';
        return;
      }}
      if (!navigator.mediaDevices || !window.MediaRecorder) {{
        addMessage('eyra', 'This browser cannot record audio for Local Whisper.', 'error');
        return;
      }}
      const stream = await navigator.mediaDevices.getUserMedia({{ audio: true }});
      chunks = [];
      recorder = new MediaRecorder(stream);
      recorder.ondataavailable = (event) => {{
        if (event.data && event.data.size) chunks.push(event.data);
      }};
      recorder.onstop = () => {{
        stream.getTracks().forEach((track) => track.stop());
        sendLocalAudio(new Blob(chunks, {{ type: recorder.mimeType || 'audio/webm' }}));
      }};
      recorder.start();
      micButton.classList.add('active');
      micButton.textContent = 'Stop';
    }}

    async function callRealtimeTool(event, dc) {{
      const response = await fetch('/api/realtime-tool-call', {{
        method: 'POST',
        headers: {{
          'Content-Type': 'application/json',
          'X-Eyra-Web-Token': webToken,
          'X-Eyra-Realtime-Tool-Token': realtime?.toolToken || '',
        }},
        body: JSON.stringify({{ name: event.name, arguments: event.arguments || '{{}}' }}),
      }});
      const data = await response.json();
      dc.send(JSON.stringify({{
        type: 'conversation.item.create',
        item: {{
          type: 'function_call_output',
          call_id: event.call_id,
          output: data.output || data.error || ''
        }}
      }}));
      dc.send(JSON.stringify({{ type: 'response.create' }}));
    }}

    async function toggleRealtime() {{
      if (realtime) {{
        realtime.stream.getTracks().forEach((track) => track.stop());
        realtime.pc.close();
        realtime = null;
        micButton.classList.remove('active');
        micButton.textContent = 'Mic';
        addMessage('eyra', 'Realtime voice stopped.');
        return;
      }}
      if (!navigator.mediaDevices || !window.RTCPeerConnection) {{
        addMessage('eyra', 'This browser does not support Realtime voice.', 'error');
        return;
      }}
      const tokenResponse = await fetch('/api/realtime-session', {{
        method: 'POST',
        headers: {{ 'X-Eyra-Web-Token': webToken }},
      }});
      const tokenData = await tokenResponse.json();
      const ephemeralKey = tokenData.value || tokenData.client_secret?.value || tokenData.client_secret;
      const toolToken = tokenData.eyra_tool_token || '';
      if (!tokenResponse.ok || !ephemeralKey) {{
        addMessage('eyra', tokenData.error || 'Realtime setup failed.', 'error');
        return;
      }}
      const pc = new RTCPeerConnection();
      const stream = await navigator.mediaDevices.getUserMedia({{ audio: true }});
      stream.getTracks().forEach((track) => pc.addTrack(track, stream));
      const audio = document.createElement('audio');
      audio.autoplay = true;
      pc.ontrack = (event) => {{ audio.srcObject = event.streams[0]; }};
      const dc = pc.createDataChannel('oai-events');
      dc.addEventListener('message', (message) => {{
        const event = JSON.parse(message.data);
        if (event.type === 'response.audio_transcript.done' && event.transcript) {{
          addMessage('eyra', event.transcript);
        }}
        if (event.type === 'response.function_call_arguments.done') {{
          callRealtimeTool(event, dc);
        }}
      }});
      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);
      const sdpResponse = await fetch('https://api.openai.com/v1/realtime/calls', {{
        method: 'POST',
        body: offer.sdp,
        headers: {{
          Authorization: `Bearer ${{ephemeralKey}}`,
          'Content-Type': 'application/sdp',
        }},
      }});
      if (!sdpResponse.ok) {{
        stream.getTracks().forEach((track) => track.stop());
        addMessage('eyra', 'Realtime SDP exchange failed.', 'error');
        return;
      }}
      await pc.setRemoteDescription({{ type: 'answer', sdp: await sdpResponse.text() }});
      realtime = {{ pc, stream, dc, audio, toolToken }};
      micButton.classList.add('active');
      micButton.textContent = 'Stop';
      addMessage('eyra', 'Realtime voice connected.');
    }}

    micButton.addEventListener('click', async () => {{
      if (voiceMode.value === 'realtime') {{
        try {{
          await toggleRealtime();
        }} catch (error) {{
          addMessage('eyra', 'Realtime voice failed to start.', 'error');
        }}
        return;
      }}
      try {{
        await toggleLocalRecording();
      }} catch (_) {{
        addMessage('eyra', 'Local voice failed to start.', 'error');
      }}
    }});
    loadTasks();
    setInterval(loadTasks, 3000);
  </script>
</body>
</html>"""


class _EyraWebHandler(BaseHTTPRequestHandler):
    settings: Settings
    runtime: WebAssistantRuntime
    web_session_token: str
    realtime_tool_token: str

    def log_message(self, *_):
        return

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            self._send(200, render_index_html(self.settings), "text/html; charset=utf-8")
            return
        if parsed.path == "/api/health":
            self._send_json(200, build_health_payload(self.settings))
            return
        if parsed.path == "/favicon.ico":
            self._send(204, "", "image/x-icon")
            return
        if parsed.path == "/api/tasks":
            if not self._authorized():
                return
            self._send_json(200, self.runtime.run_sync(self.runtime.list_tasks()))
            return
        if parsed.path == "/api/approvals":
            if not self._authorized():
                return
            self._send_json(200, self.runtime.run_sync(self.runtime.list_approvals()))
            return
        if parsed.path.startswith("/api/task/"):
            if not self._authorized():
                return
            task_id = parsed.path.rsplit("/", 1)[-1]
            payload = self.runtime.run_sync(self.runtime.task_detail(task_id))
            self._send_json(200 if "task" in payload else 404, payload)
            return
        self._send_json(404, {"error": "Not found."})

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in {
            "/api/chat",
            "/api/local-voice-turn",
            "/api/local-speak",
            "/api/realtime-session",
            "/api/realtime-tool-call",
            "/api/cancel",
            "/api/approve",
            "/api/reject",
        } and not self._authorized():
            return
        if parsed.path == "/api/chat":
            payload = self._read_json()
            text = str(payload.get("text", "")).strip()
            if not text:
                self._send_json(400, {"error": "Message is empty."})
                return
            try:
                result = self.runtime.run_sync(
                    self.runtime.handle_message(text, str(payload.get("voiceMode", "text"))),
                    timeout=30,
                )
            except Exception:
                self._send_json(500, {"error": "Eyra could not answer that request. Check the terminal logs."})
                return
            self._send_json(200, result)
            return
        if parsed.path == "/api/cancel":
            payload = self._read_json()
            task_id = str(payload.get("taskId", "")).strip()
            if not task_id:
                self._send_json(400, {"error": "taskId is required."})
                return
            result = self.runtime.run_sync(self.runtime.cancel_task(task_id))
            self._send_json(200 if result.get("status") != "missing" else 404, result)
            return
        if parsed.path == "/api/approve":
            payload = self._read_json()
            approval_id = str(payload.get("approvalId", "")).strip()
            if not approval_id:
                self._send_json(400, {"error": "approvalId is required."})
                return
            self._send_json(200, self.runtime.run_sync(self.runtime.approve(approval_id)))
            return
        if parsed.path == "/api/reject":
            payload = self._read_json()
            approval_id = str(payload.get("approvalId", "")).strip()
            if not approval_id:
                self._send_json(400, {"error": "approvalId is required."})
                return
            self._send_json(200, self.runtime.run_sync(self.runtime.reject(approval_id)))
            return
        if parsed.path == "/api/local-voice-turn":
            payload = self._read_bytes(max_bytes=25 * 1024 * 1024)
            if not payload:
                self._send_json(400, {"error": "No audio was received."})
                return
            transcript = transcribe_local_audio(payload)
            if transcript.startswith("Local Whisper error:"):
                self._send_json(500, {"error": transcript})
                return
            try:
                result = self.runtime.run_sync(self.runtime.handle_message(transcript, "local"), timeout=30)
            except Exception:
                self._send_json(500, {"error": "Eyra could not answer that voice request.", "transcript": transcript})
                return
            result["transcript"] = transcript
            self._send_json(200, result)
            return
        if parsed.path == "/api/local-speak":
            payload = self._read_json()
            text = str(payload.get("text", "")).strip()
            if not text:
                self._send_json(400, {"error": "No text was provided."})
                return
            message = speak_local_text(text)
            status = 200 if message == "Local speech started." else 500
            self._send_json(status, {"status": message})
            return
        if parsed.path == "/api/realtime-session":
            status, payload = create_realtime_session_payload(self.settings)
            if status == 200 and self.settings.REALTIME_TOOLS_ENABLED:
                payload["eyra_tool_token"] = self.realtime_tool_token
            self._send_json(status, payload)
            return
        if parsed.path == "/api/realtime-tool-call":
            web_token = self.headers.get("X-Eyra-Web-Token", "")
            token = self.headers.get("X-Eyra-Realtime-Tool-Token", "")
            if not validate_web_session_token(web_token, self.web_session_token) or not validate_realtime_tool_token(
                self.settings,
                token,
                self.realtime_tool_token,
            ):
                self._send_json(403, {"error": "Realtime tool calls are disabled or unauthorized."})
                return
            payload = self._read_json()
            output = self.runtime.run_sync(call_realtime_tool(self.settings, payload))
            self._send_json(200, {"output": output})
            return
        self._send_json(404, {"error": "Not found."})

    def do_PUT(self):
        self._send_json(405, {"error": "Method not allowed."})

    def do_DELETE(self):
        self._send_json(405, {"error": "Method not allowed."})

    def _authorized(self) -> bool:
        if not web_auth_required(self.settings):
            return True
        provided = self.headers.get("X-Eyra-Web-Token", "")
        if validate_web_session_token(provided, self.web_session_token):
            return True
        self._send_json(401, {"error": "Web UI session token is required."})
        return False

    def _read_json(self) -> dict[str, Any]:
        raw = self._read_bytes()
        try:
            return json.loads(raw.decode())
        except json.JSONDecodeError:
            return {}

    def _read_bytes(self, max_bytes: int = 1_000_000) -> bytes:
        length = int(self.headers.get("content-length", "0"))
        limit = min(max_bytes, max(1, int(self.settings.WEB_UI_MAX_REQUEST_BYTES)))
        if max_bytes > int(self.settings.WEB_UI_MAX_REQUEST_BYTES) and max_bytes > 1_000_000:
            limit = max_bytes
        if length <= 0 or length > limit:
            return b""
        return self.rfile.read(length)

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        self._send(status, json.dumps(payload), "application/json; charset=utf-8")

    def _send(self, status: int, body: str, content_type: str) -> None:
        raw = body.encode()
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(raw)))
        self.send_header("cache-control", "no-store")
        self.end_headers()
        self.wfile.write(raw)


def create_realtime_session_payload(settings: Settings) -> tuple[int, dict[str, Any]]:
    if not settings.REALTIME_VOICE_ENABLED:
        return 400, {"error": "Realtime voice is disabled. Set REALTIME_VOICE_ENABLED=true to use online voice."}
    api_key = settings.OPENAI_API_KEY
    if not api_key:
        return 400, {"error": "OPENAI_API_KEY is not configured for Realtime voice."}
    session: dict[str, Any] = {
        "type": "realtime",
        "model": settings.REALTIME_MODEL,
        "instructions": (
            "You are Eyra, a local-first macOS assistant. Realtime voice is an online mode. "
            "Keep spoken replies short and clear."
        ),
        "audio": {"output": {"voice": settings.REALTIME_VOICE}},
    }
    tools = realtime_tools(settings)
    if tools:
        session["tools"] = tools
        session["tool_choice"] = "auto"
    body = json.dumps(
        {
            "session": session,
        }
    ).encode()
    request = urllib.request.Request(
        "https://api.openai.com/v1/realtime/client_secrets",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "OpenAI-Safety-Identifier": "eyra-local-session",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, {"error": "Realtime session request failed.", "detail": e.read().decode(errors="replace")}
    except OSError as e:
        return 502, {"error": f"Could not reach OpenAI Realtime: {e}"}


def validate_realtime_tool_token(settings: Settings, provided: str, expected: str) -> bool:
    if not settings.REALTIME_VOICE_ENABLED or not settings.REALTIME_TOOLS_ENABLED:
        return False
    if not provided or not expected:
        return False
    return hmac.compare_digest(provided, expected)


def validate_web_session_token(provided: str, expected: str) -> bool:
    if not provided or not expected:
        return False
    return hmac.compare_digest(provided, expected)


def realtime_tools(settings: Settings) -> list[dict[str, Any]]:
    if not settings.REALTIME_TOOLS_ENABLED:
        return []
    configured = {name.strip() for name in settings.REALTIME_ALLOWED_TOOLS.split(",") if name.strip()}
    allowed = configured or _SAFE_REALTIME_TOOLS
    tools = []
    for tool in build_tool_registry(settings).to_openai_tools(include_costly=False):
        fn = tool.get("function", {})
        if fn.get("name") not in allowed:
            continue
        tools.append(
            {
                "type": "function",
                "name": fn.get("name"),
                "description": fn.get("description", ""),
                "parameters": fn.get("parameters", {"type": "object", "properties": {}}),
            }
        )
    return tools


async def call_realtime_tool(settings: Settings, payload: dict[str, Any]) -> str:
    name = str(payload.get("name", ""))
    allowed = {
        tool.get("name")
        for tool in realtime_tools(settings)
        if isinstance(tool, dict) and tool.get("type") == "function"
    }
    if name not in allowed:
        return f"Realtime tool is not allowed: {name}"
    raw_arguments = payload.get("arguments", "{}")
    if isinstance(raw_arguments, str):
        arguments = raw_arguments
    else:
        arguments = json.dumps(raw_arguments)
    result = await build_tool_registry(settings).execute(name, arguments)
    return result.content


def transcribe_local_audio(audio: bytes) -> str:
    wh = resolve_wh_bin()
    if not wh:
        return "Local Whisper error: wh is not installed or not on PATH."
    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".webm") as temp:
            temp.write(audio)
            temp_path = temp.name
        completed = subprocess.run(
            [wh, "transcribe", temp_path, "--raw"],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        if completed.returncode != 0:
            return "Local Whisper error: " + (completed.stderr.strip() or completed.stdout.strip() or "transcription failed")
        transcript = completed.stdout.strip()
        return transcript or "Local Whisper error: no speech was detected."
    except Exception as e:
        return f"Local Whisper error: {e}"
    finally:
        if temp_path:
            try:
                import os

                os.unlink(temp_path)
            except OSError:
                pass


def resolve_wh_bin() -> str | None:
    candidates = [
        shutil.which("wh"),
        "/opt/homebrew/bin/wh",
        "/usr/local/bin/wh",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).expanduser().is_file():
            return str(Path(candidate).expanduser())
    return None


def speak_local_text(text: str) -> str:
    wh = resolve_wh_bin()
    if not wh:
        return "Local Whisper error: wh is not installed or not on PATH."
    try:
        completed = subprocess.run(
            [wh, "whisper", text[:500]],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except Exception as e:
        return f"Local Whisper error: {e}"
    if completed.returncode != 0:
        return "Local Whisper error: " + (completed.stderr.strip() or "speech failed")
    return "Local speech started."


def run_web_server(settings: Settings) -> None:
    web_session_token = settings.WEB_UI_TOKEN.strip() or secrets.token_urlsafe(32)
    realtime_tool_token = secrets.token_urlsafe(32)
    runtime = WebAssistantRuntime(settings)
    handler = type(
        "EyraWebHandler",
        (_EyraWebHandler,),
        {
            "settings": settings,
            "runtime": runtime,
            "web_session_token": web_session_token,
            "realtime_tool_token": realtime_tool_token,
        },
    )
    server = EyraThreadingHTTPServer((settings.WEB_UI_HOST, settings.WEB_UI_PORT), handler)
    print(f"Eyra web UI: http://{settings.WEB_UI_HOST}:{settings.WEB_UI_PORT}")
    if web_auth_required(settings):
        print(f"Eyra web UI token URL: http://{settings.WEB_UI_HOST}:{settings.WEB_UI_PORT}/?token={web_session_token}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nEyra web UI stopped.")
    finally:
        server.server_close()
        runtime.close()


def run() -> None:
    run_web_server(Settings.load_from_env())


if __name__ == "__main__":
    run()
