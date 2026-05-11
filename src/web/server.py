"""Small built-in Web UI for phone and browser access."""

from __future__ import annotations

import asyncio
import hmac
import json
import secrets
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from chat.complexity_scorer import ComplexityScorer
from chat.message_handler import process_task_stream
from chat.session_state import InteractionStyle, QualityMode
from runtime.tooling import build_tool_registry
from utils.settings import Settings


def build_health_payload(settings: Settings) -> dict[str, Any]:
    return {
        "status": "ok",
        "offlineByDefault": True,
        "web": {"enabled": settings.WEB_UI_ENABLED, "host": settings.WEB_UI_HOST, "port": settings.WEB_UI_PORT},
        "voice": {
            "localWhisper": settings.LIVE_LISTENING_ENABLED or settings.LIVE_SPEECH_ENABLED,
            "realtime": settings.REALTIME_VOICE_ENABLED,
            "realtimeModel": settings.REALTIME_MODEL,
        },
        "tools": {
            "network": settings.NETWORK_TOOLS_ENABLED,
            "os": settings.OS_TOOLS_ENABLED,
            "agents": settings.AGENT_TOOLS_ENABLED,
            "mcp": settings.MCP_TOOLS_ENABLED,
        },
    }


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
      </div>
    </header>
    <section id="messages" aria-live="polite"></section>
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
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ text, voiceMode: voiceMode.value }}),
        }});
        const data = await response.json();
        reply.textContent = data.reply || data.error || '';
        if (!response.ok) reply.classList.add('error');
      }} catch (error) {{
        reply.textContent = 'Could not reach Eyra on this machine.';
        reply.classList.add('error');
      }}
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
          headers: {{ 'Content-Type': blob.type || 'application/octet-stream' }},
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
          headers: {{ 'Content-Type': 'application/json' }},
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
  </script>
</body>
</html>"""


class _EyraWebHandler(BaseHTTPRequestHandler):
    settings: Settings
    scorer: ComplexityScorer
    conversation: list[dict[str, str]]
    web_session_token: str
    realtime_tool_token: str

    def log_message(self, *_):
        return

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/?"):
            self._send(200, render_index_html(self.settings), "text/html; charset=utf-8")
            return
        if self.path == "/api/health":
            self._send_json(200, build_health_payload(self.settings))
            return
        if self.path == "/favicon.ico":
            self._send(204, "", "image/x-icon")
            return
        self._send_json(404, {"error": "Not found."})

    def do_POST(self):
        if self.path == "/api/chat":
            payload = self._read_json()
            text = str(payload.get("text", "")).strip()
            if not text:
                self._send_json(400, {"error": "Message is empty."})
                return
            try:
                reply = asyncio.run(self._chat(text, payload))
            except Exception:
                self._send_json(500, {"error": "Eyra could not answer that request. Check the terminal logs."})
                return
            self._send_json(200, {"reply": reply})
            return
        if self.path == "/api/local-voice-turn":
            payload = self._read_bytes(max_bytes=25 * 1024 * 1024)
            if not payload:
                self._send_json(400, {"error": "No audio was received."})
                return
            transcript = transcribe_local_audio(payload)
            if transcript.startswith("Local Whisper error:"):
                self._send_json(500, {"error": transcript})
                return
            try:
                reply = asyncio.run(self._chat(transcript, {"voiceMode": "local"}))
            except Exception:
                self._send_json(500, {"error": "Eyra could not answer that voice request.", "transcript": transcript})
                return
            self._send_json(200, {"transcript": transcript, "reply": reply})
            return
        if self.path == "/api/local-speak":
            payload = self._read_json()
            text = str(payload.get("text", "")).strip()
            if not text:
                self._send_json(400, {"error": "No text was provided."})
                return
            message = speak_local_text(text)
            status = 200 if message == "Local speech started." else 500
            self._send_json(status, {"status": message})
            return
        if self.path == "/api/realtime-session":
            if not validate_web_session_token(self.headers.get("X-Eyra-Web-Token", ""), self.web_session_token):
                self._send_json(403, {"error": "Web UI session token is required for Realtime voice."})
                return
            status, payload = create_realtime_session_payload(self.settings)
            if status == 200:
                payload["eyra_tool_token"] = self.realtime_tool_token
            self._send_json(status, payload)
            return
        if self.path == "/api/realtime-tool-call":
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
            output = asyncio.run(call_realtime_tool(self.settings, payload))
            self._send_json(200, {"output": output})
            return
        self._send_json(404, {"error": "Not found."})

    async def _chat(self, text: str, payload: dict[str, Any]) -> str:
        self.conversation.append({"role": "user", "content": text})
        registry = build_tool_registry(self.settings)
        interaction = InteractionStyle.VOICE if payload.get("voiceMode") in ("local", "realtime") else InteractionStyle.TEXT
        chunks: list[str] = []
        async for chunk in process_task_stream(
            text_content=text,
            complexity_scorer=self.scorer,
            settings=self.settings,
            messages=self.conversation,
            quality_mode=QualityMode.BALANCED,
            interaction_style=interaction,
            tool_registry=registry,
        ):
            chunks.append(chunk)
        reply = "".join(chunks).strip()
        if reply:
            self.conversation.append({"role": "assistant", "content": reply})
        return reply or "No response."

    def _read_json(self) -> dict[str, Any]:
        raw = self._read_bytes()
        try:
            return json.loads(raw.decode())
        except json.JSONDecodeError:
            return {}

    def _read_bytes(self, max_bytes: int = 1_000_000) -> bytes:
        length = int(self.headers.get("content-length", "0"))
        if length <= 0 or length > max_bytes:
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
    body = json.dumps(
        {
            "session": {
                "type": "realtime",
                "model": settings.REALTIME_MODEL,
                "instructions": (
                    "You are Eyra, a local-first macOS assistant. Use tools for current OS facts and actions. "
                    "Keep spoken replies short and clear."
                ),
                "audio": {"output": {"voice": settings.REALTIME_VOICE}},
                "tools": realtime_tools(settings),
                "tool_choice": "auto",
            }
        }
    ).encode()
    request = urllib.request.Request(
        "https://api.openai.com/v1/realtime/client_secrets",
        data=body,
        method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, {"error": "Realtime session request failed.", "detail": e.read().decode(errors="replace")}
    except OSError as e:
        return 502, {"error": f"Could not reach OpenAI Realtime: {e}"}


def validate_realtime_tool_token(settings: Settings, provided: str, expected: str) -> bool:
    if not settings.REALTIME_VOICE_ENABLED:
        return False
    if not provided or not expected:
        return False
    return hmac.compare_digest(provided, expected)


def validate_web_session_token(provided: str, expected: str) -> bool:
    if not provided or not expected:
        return False
    return hmac.compare_digest(provided, expected)


def realtime_tools(settings: Settings) -> list[dict[str, Any]]:
    tools = []
    for tool in build_tool_registry(settings).to_openai_tools(include_costly=True):
        fn = tool.get("function", {})
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
    web_session_token = secrets.token_urlsafe(24)
    realtime_tool_token = secrets.token_urlsafe(32)
    handler = type(
        "EyraWebHandler",
        (_EyraWebHandler,),
        {
            "settings": settings,
            "scorer": ComplexityScorer(),
            "conversation": [],
            "web_session_token": web_session_token,
            "realtime_tool_token": realtime_tool_token,
        },
    )
    server = ThreadingHTTPServer((settings.WEB_UI_HOST, settings.WEB_UI_PORT), handler)
    print(f"Eyra web UI: http://{settings.WEB_UI_HOST}:{settings.WEB_UI_PORT}")
    if settings.WEB_UI_HOST not in {"127.0.0.1", "localhost", "::1"}:
        print(f"Eyra web UI token URL: http://{settings.WEB_UI_HOST}:{settings.WEB_UI_PORT}/?token={web_session_token}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nEyra web UI stopped.")
    finally:
        server.server_close()


def run() -> None:
    run_web_server(Settings.load_from_env())


if __name__ == "__main__":
    run()
