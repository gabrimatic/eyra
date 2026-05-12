"""Tests for local trigger persistence."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from runtime.triggers import TriggerStatus, TriggerStore


def test_trigger_store_persists_file_move_trigger(tmp_path):
    db_path = tmp_path / "triggers.sqlite3"
    first = TriggerStore(db_path)
    trigger = first.create_file_exists_trigger(
        title="Move download",
        source_path=str(tmp_path / "Downloads" / "a.txt"),
        action={"type": "file.move", "destination": str(tmp_path / "Documents" / "a.txt")},
        original_request="When a.txt appears in Downloads, move it to Documents.",
    )
    first.close()

    second = TriggerStore(db_path)
    restored = second.get_trigger(trigger.id)

    assert restored is not None
    assert restored.title == "Move download"
    assert restored.status == TriggerStatus.ACTIVE
    assert restored.kind == "file_exists"
    assert restored.condition["path"].endswith("Downloads/a.txt")
    assert restored.action["type"] == "file.move"
    assert second.list_triggers()[0].id == trigger.id
    second.close()


def test_trigger_store_marks_cancelled_and_completed(tmp_path):
    store = TriggerStore(tmp_path / "triggers.sqlite3")
    trigger = store.create_file_exists_trigger(
        title="Move download",
        source_path=str(tmp_path / "Downloads" / "a.txt"),
        action={"type": "file.move", "destination": str(tmp_path / "Documents" / "a.txt")},
        original_request="When a.txt appears in Downloads, move it to Documents.",
    )

    store.mark_cancelled(trigger.id)
    assert store.get_trigger(trigger.id).status == TriggerStatus.CANCELLED

    second = store.create_file_exists_trigger(
        title="Move other download",
        source_path=str(tmp_path / "Downloads" / "b.txt"),
        action={"type": "file.move", "destination": str(tmp_path / "Documents" / "b.txt")},
        original_request="When b.txt appears in Downloads, move it to Documents.",
    )
    store.mark_completed(second.id)
    assert store.get_trigger(second.id).status == TriggerStatus.COMPLETED
    store.close()


def test_trigger_store_pauses_and_resumes_active_trigger(tmp_path):
    store = TriggerStore(tmp_path / "triggers.sqlite3")
    trigger = store.create_file_exists_trigger(
        title="Move download",
        source_path=str(tmp_path / "Downloads" / "a.txt"),
        action={"type": "file.move", "destination": str(tmp_path / "Documents" / "a.txt")},
        original_request="When a.txt appears in Downloads, move it to Documents.",
    )

    store.mark_paused(trigger.id)
    assert store.get_trigger(trigger.id).status == TriggerStatus.PAUSED

    store.mark_active(trigger.id)
    assert store.get_trigger(trigger.id).status == TriggerStatus.ACTIVE
    store.close()


def test_trigger_store_persists_one_time_reminder(tmp_path):
    store = TriggerStore(tmp_path / "triggers.sqlite3")
    trigger = store.create_timer_trigger(
        title="Reminder: stretch",
        fire_at=1234.5,
        action={"type": "notify", "message": "stretch"},
        original_request="Remind me in 10 minutes to stretch.",
    )
    store.close()

    restored_store = TriggerStore(tmp_path / "triggers.sqlite3")
    restored = restored_store.get_trigger(trigger.id)

    assert restored is not None
    assert restored.kind == "timer"
    assert restored.condition["fire_at"] == 1234.5
    assert restored.action["type"] == "notify"
    assert restored.action["message"] == "stretch"
    restored_store.close()


def test_trigger_store_persists_recurring_reminder(tmp_path):
    store = TriggerStore(tmp_path / "triggers.sqlite3")
    trigger = store.create_recurring_timer_trigger(
        title="Recurring reminder: stretch",
        interval_seconds=60,
        next_fire_at=1234.5,
        action={"type": "notify", "message": "stretch"},
        original_request="Every 1 minute remind me to stretch.",
    )
    store.close()

    restored_store = TriggerStore(tmp_path / "triggers.sqlite3")
    restored = restored_store.get_trigger(trigger.id)

    assert restored is not None
    assert restored.kind == "recurring_timer"
    assert restored.condition["interval_seconds"] == 60
    assert restored.condition["next_fire_at"] == 1234.5
    assert restored.condition["fire_count"] == 0
    restored_store.close()


def test_trigger_store_updates_recurring_fire_state(tmp_path):
    store = TriggerStore(tmp_path / "triggers.sqlite3")
    trigger = store.create_recurring_timer_trigger(
        title="Recurring reminder: stretch",
        interval_seconds=60,
        next_fire_at=1234.5,
        action={"type": "notify", "message": "stretch"},
        original_request="Every 1 minute remind me to stretch.",
    )

    store.record_recurring_fire(trigger.id, last_fire_at=1234.5, next_fire_at=1294.5)
    restored = store.get_trigger(trigger.id)

    assert restored.condition["fire_count"] == 1
    assert restored.condition["last_fire_at"] == 1234.5
    assert restored.condition["next_fire_at"] == 1294.5
    assert restored.status == TriggerStatus.ACTIVE
    store.close()
