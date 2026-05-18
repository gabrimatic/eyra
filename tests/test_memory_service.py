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


def _write_fake_memory_mcp(path):
    path.write_text(
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
            command = args.get("command")
            if command == "upsert":
                section = args.get("section", "user_preferences")
                data.setdefault("sections", {}).setdefault(section, [])
                fact = {"key": args["key"], "value": args["value"]}
                data["sections"][section] = [fact]
                save(data)
                text = f"ok {fact['key']}: {fact['value']}"
            elif command == "view":
                text = json.dumps(data)
            else:
                text = "ok"
        else:
            facts = data.get("sections", {}).get("user_preferences", [])
            lines = [f"1. {item['key']}: {item['value']}" for item in facts]
            text = "User Preferences:\\n" + "\\n".join(lines) if lines else "No memories stored yet."
        send({"jsonrpc": "2.0", "id": msg["id"], "result": {"content": [{"type": "text", "text": text}]}})
""".strip()
    )


def _memory_settings(tmp_path, **overrides):
    server = tmp_path / "fake_memory_mcp.py"
    _write_fake_memory_mcp(server)
    values = {
        "USE_MOCK_CLIENT": False,
        "MEMORY_MCP_COMMAND": sys.executable,
        "MEMORY_MCP_ARGS": str(server),
        "MEMORY_PATH": str(tmp_path / "memory.json"),
    }
    values.update(overrides)
    return Settings(**values)

    async def generate_completion_stream(self, messages, **_):
        self.messages = messages
        yield "ok"


def test_memory_service_stores_and_reads_compact_fact_through_mcp(tmp_path):
    settings = _memory_settings(tmp_path)

    result = _run(MemoryService(settings).remember("I prefer short answers with no filler"))
    summary = _run(MemoryService(settings).show())

    assert "prefer_short_answers_no_filler" in result
    assert "User Preferences:" in summary
    assert "I prefer short answers with no filler" in summary


def test_memory_status_runs_real_context_health_check(tmp_path):
    settings = _memory_settings(tmp_path)

    status = _run(MemoryService(settings).status())

    assert status["ready"] is True
    assert status["commandAvailable"] is True
    assert status["contextAvailable"] is True
    assert status["health"] == "ready"
    assert status["writeRequiresConfirmation"] is False


def test_memory_status_reports_missing_command(tmp_path):
    settings = Settings(MEMORY_MCP_COMMAND="eyra-missing-memory-command", MEMORY_PATH=str(tmp_path / "memory.json"))

    status = _run(MemoryService(settings).status())

    assert status["ready"] is False
    assert status["commandAvailable"] is False
    assert status["contextAvailable"] is False
    assert status["health"] == "command_missing"


def test_memory_status_reports_mcp_context_failure(tmp_path):
    server = tmp_path / "failing_mcp.py"
    server.write_text(
        """
import json
import sys

def send(payload):
    sys.stdout.write(json.dumps(payload) + "\\n")
    sys.stdout.flush()

for line in sys.stdin:
    msg = json.loads(line)
    if msg.get("method") == "initialize":
        send({"jsonrpc": "2.0", "id": msg["id"], "result": {"serverInfo": {"name": "fake", "version": "1"}}})
    elif msg.get("method") == "tools/call":
        send({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32000, "message": "boom"}})
""".strip()
    )
    settings = Settings(MEMORY_MCP_COMMAND=sys.executable, MEMORY_MCP_ARGS=str(server), MEMORY_PATH=str(tmp_path / "memory.json"))

    status = _run(MemoryService(settings).status())

    assert status["ready"] is False
    assert status["commandAvailable"] is True
    assert status["contextAvailable"] is False
    assert status["health"] == "mcp_error"
    assert "boom" in status["error"]


def test_memory_status_reports_memory_file_error(tmp_path):
    server = tmp_path / "bad_file_mcp.py"
    server.write_text(
        """
import json
import sys

def send(payload):
    sys.stdout.write(json.dumps(payload) + "\\n")
    sys.stdout.flush()

for line in sys.stdin:
    msg = json.loads(line)
    if msg.get("method") == "initialize":
        send({"jsonrpc": "2.0", "id": msg["id"], "result": {"serverInfo": {"name": "fake", "version": "1"}}})
    elif msg.get("method") == "tools/call":
        send({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32000, "message": "JSON parse error in memory file"}})
""".strip()
    )
    settings = Settings(MEMORY_MCP_COMMAND=sys.executable, MEMORY_MCP_ARGS=str(server), MEMORY_PATH=str(tmp_path / "memory.json"))

    status = _run(MemoryService(settings).status())

    assert status["ready"] is False
    assert status["contextAvailable"] is False
    assert status["health"] == "memory_file_error"


def test_auto_save_writes_when_confirmation_is_not_required(tmp_path):
    settings = _memory_settings(tmp_path, MEMORY_WRITE_REQUIRE_CONFIRMATION=False)

    _run(MemoryService(settings).maybe_auto_remember("I prefer compact memory tests"))
    summary = _run(MemoryService(settings).show())

    assert "prefer_compact_memory_tests" in summary


def test_auto_save_skips_when_confirmation_is_required(tmp_path):
    settings = _memory_settings(tmp_path, MEMORY_WRITE_REQUIRE_CONFIRMATION=True)

    _run(MemoryService(settings).maybe_auto_remember("I prefer compact memory tests"))
    summary = _run(MemoryService(settings).show())

    assert "No memories stored yet" in summary


def test_explicit_remember_writes_when_confirmation_is_required(tmp_path):
    settings = _memory_settings(tmp_path, MEMORY_WRITE_REQUIRE_CONFIRMATION=True)

    result = _run(MemoryService(settings).remember("I prefer compact memory tests"))

    assert "prefer_compact_memory_tests" in result


def test_memory_disabled_writes_nothing(tmp_path):
    settings = _memory_settings(tmp_path, MEMORY_ENABLED=False)

    _run(MemoryService(settings).maybe_auto_remember("I prefer compact memory tests"))
    result = _run(MemoryService(settings).remember("I prefer compact memory tests"))

    assert result.startswith("Memory is off.")
    assert not os.path.exists(settings.MEMORY_PATH)


def test_rejected_sensitive_memory_writes_nothing(tmp_path):
    settings = _memory_settings(tmp_path)

    result = _run(MemoryService(settings).remember("My API key is sk-test_1234567890abcdef"))

    assert "did not save" in result
    assert not os.path.exists(settings.MEMORY_PATH)


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
