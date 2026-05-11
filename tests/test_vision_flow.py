"""Tests for deterministic screen capture and configurable vision routing."""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from chat.complexity_scorer import ComplexityScorer
from runtime.live_session import LiveSession
from runtime.models import LiveRuntimeState, PreflightResult
from runtime.tasks import TaskStatus
from utils.settings import Settings


def _run(coro):
    return asyncio.run(coro)


def _session(*, model: str = "qwen3:4b", vision_model: str = "gemma3:4b", tool_capable: bool = False) -> LiveSession:
    settings = Settings(
        USE_MOCK_CLIENT=True,
        MODEL=model,
        WORKER_MODEL=model,
        VISION_MODEL=vision_model,
        LIVE_LISTENING_ENABLED=False,
        LIVE_SPEECH_ENABLED=False,
    )
    checked_models = [model, vision_model]
    preflight = PreflightResult(
        backend_reachable=True,
        models_ready=checked_models,
        screen_capture_available=True,
        tool_capable_models=[model] if tool_capable else [],
        tool_capability_checked_models=checked_models,
        vision_capable_models=[vision_model],
        vision_capability_checked_models=checked_models,
    )
    state = LiveRuntimeState.from_preflight(preflight, settings=settings)
    session = LiveSession(settings, preflight, state, ComplexityScorer())
    session.speech = MagicMock()
    session.speech.interrupt = AsyncMock()
    session.speech.speak = AsyncMock()
    session.speech.wait_for_speech = AsyncMock()
    session.speech.cancel_listen = MagicMock()
    return session


class TestDeterministicVisionFlow:
    def test_screen_question_uses_configured_vision_model_without_main_model_vision_or_tools(self, monkeypatch):
        seen = {}

        async def fake_analyze_screen(*, settings, prompt, conversation_messages, current_goal, model_semaphore, preflight):
            seen["model"] = settings.VISION_MODEL
            seen["prompt"] = prompt
            seen["preflight"] = preflight
            return "I can see the terminal window."

        import runtime.live_session as live_session

        monkeypatch.setattr(live_session, "analyze_screen", fake_analyze_screen)
        session = _session(model="qwen3:4b", vision_model="gemma3:4b", tool_capable=True)

        async def run():
            await session._handle_user_input("What do you see on the screen?")
            tasks = session.task_manager.list_tasks(include_recent=True)
            assert len(tasks) == 1
            await session.task_manager.wait_for_task(tasks[0].id)
            return tasks[0]

        task = _run(run())

        assert seen["model"] == "gemma3:4b"
        assert task.status == TaskStatus.COMPLETED
        assert task.final_result == "I can see the terminal window."

    def test_screen_question_works_when_main_model_has_vision_but_no_tools(self, monkeypatch):
        async def fake_analyze_screen(**_kwargs):
            return "The screen shows a document."

        import runtime.live_session as live_session

        monkeypatch.setattr(live_session, "analyze_screen", fake_analyze_screen)
        session = _session(model="gemma3:4b", vision_model="gemma3:4b", tool_capable=False)

        async def run():
            await session._handle_user_input("Explain what I'm looking at.")
            task = session.task_manager.list_tasks(include_recent=True)[0]
            await session.task_manager.wait_for_task(task.id)
            return task

        task = _run(run())

        assert task.status == TaskStatus.COMPLETED
        assert task.final_result == "The screen shows a document."

    def test_screen_question_without_vision_model_fails_clearly(self, capsys):
        settings = Settings(
            USE_MOCK_CLIENT=True,
            MODEL="qwen3:4b",
            VISION_MODEL="gemma3:4b",
            LIVE_LISTENING_ENABLED=False,
            LIVE_SPEECH_ENABLED=False,
        )
        preflight = PreflightResult(
            backend_reachable=True,
            models_ready=["qwen3:4b", "gemma3:4b"],
            screen_capture_available=True,
            tool_capable_models=["qwen3:4b"],
            tool_capability_checked_models=["qwen3:4b", "gemma3:4b"],
            vision_capable_models=[],
            vision_capability_checked_models=["qwen3:4b", "gemma3:4b"],
        )
        state = LiveRuntimeState.from_preflight(preflight, settings=settings)
        session = LiveSession(settings, preflight, state, ComplexityScorer())
        session.speech = MagicMock()
        session.speech.interrupt = AsyncMock()
        session.speech.speak = AsyncMock()
        session.speech.wait_for_speech = AsyncMock()
        session.speech.cancel_listen = MagicMock()

        _run(session._handle_user_input("What do you see on the screen?"))

        assert session.task_manager.list_tasks(include_recent=True) == []
        assert "vision-capable model" in capsys.readouterr().out
