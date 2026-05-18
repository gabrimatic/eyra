import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from chat.complexity_scorer import ComplexityScorer
from chat.message_handler import process_task_stream
from runtime.memory.service import MemoryService
from tools.registry import ToolRegistry
from utils.settings import Settings


def _run(coro):
    return asyncio.run(coro)


class _RecordingClient:
    def __init__(self):
        self.messages = []

    async def stream_with_tools(self, messages, **_):
        self.messages = messages
        yield "ok"

    async def generate_completion_stream(self, messages, **_):
        self.messages = messages
        yield "ok"


def test_memory_service_stores_and_reads_compact_fact_through_mcp(tmp_path):
    server = tmp_path / "fake_memory_mcp.py"
    memory_path = tmp_path / "memory.json"
    server.write_text(
        """
import json
import os
import sys

path = os.environ["MEMORY_PATH"]

def load():
    if os.path.exists(path):
        return json.load(open(path))
    return {"sections": {"user_preferences": []}}

def save(data):
    with open(path, "w") as fh:
        json.dump(data, fh)

def send(payload):
    sys.stdout.write(json.dumps(payload) + "\\n")
    sys.stdout.flush()

for line in sys.stdin:
    msg = json.loads(line)
    if msg.get("method") == "initialize":
        send({"jsonrpc": "2.0", "id": msg["id"], "result": {"serverInfo": {"name": "fake", "version": "1"}}})
    elif msg.get("method") == "tools/call":
        params = msg.get("params", {})
        args = params.get("arguments", {})
        data = load()
        if params.get("name") == "memory":
            fact = {"key": args["key"], "value": args["value"]}
            data["sections"]["user_preferences"] = [fact]
            save(data)
            text = f"ok {fact['key']}: {fact['value']}"
        else:
            lines = [f"1. {item['key']}: {item['value']}" for item in data["sections"]["user_preferences"]]
            text = "User Preferences:\\n" + "\\n".join(lines) if lines else "No memories stored yet."
        send({"jsonrpc": "2.0", "id": msg["id"], "result": {"content": [{"type": "text", "text": text}]}})
""".strip()
    )
    settings = Settings(
        USE_MOCK_CLIENT=True,
        MEMORY_MCP_COMMAND=sys.executable,
        MEMORY_MCP_ARGS=str(server),
        MEMORY_PATH=str(memory_path),
    )

    result = _run(MemoryService(settings).remember("I prefer short answers with no filler"))
    summary = _run(MemoryService(settings).show())

    assert "prefer_short_answers_no_filler" in result
    assert "User Preferences:" in summary
    assert "I prefer short answers with no filler" in summary


def test_process_task_stream_loads_compact_instruction_files(monkeypatch, tmp_path):
    agents = tmp_path / "AGENTS.md"
    personality = tmp_path / "personality.md"
    agents.write_text("- Always be concise.")
    personality.write_text("Warm and direct.")
    client = _RecordingClient()
    monkeypatch.setattr("chat.message_handler.get_ai_client", lambda *_, **__: client)

    settings = Settings(
        USE_MOCK_CLIENT=True,
        MEMORY_ENABLED=False,
        AGENTS_FILE=str(agents),
        PERSONALITY_FILE=str(personality),
    )
    chunks = _run(_collect(process_task_stream(
        "hi",
        complexity_scorer=ComplexityScorer(),
        settings=settings,
        messages=[{"role": "user", "content": "hi"}],
        tool_registry=ToolRegistry(),
    )))

    assert chunks == ["ok"]
    system_context = "\n".join(message["content"] for message in client.messages if message["role"] == "system")
    assert "Always be concise" in system_context
    assert "Warm and direct" in system_context


async def _collect(stream):
    return [chunk async for chunk in stream]
