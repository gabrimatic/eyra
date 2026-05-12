"""Small observe-plan-act-verify helpers for controller-owned local actions."""

from __future__ import annotations

from pathlib import Path


def build_file_move_operator_loop(
    source: str,
    destination: str,
    action_result: str,
    *,
    source_existed_before: bool | None = None,
    destination_existed_before: bool | None = None,
) -> dict:
    """Build structured observe-plan-act-verify-recover evidence for a file move."""
    src = Path(source).expanduser()
    dest = Path(destination).expanduser()
    observation = {
        "source": str(src),
        "destination": str(dest),
        "source_exists": src.exists() if source_existed_before is None else source_existed_before,
        "destination_exists": dest.exists() if destination_existed_before is None else destination_existed_before,
    }
    checks = {
        "source_removed": not src.exists(),
        "destination_exists": dest.exists(),
    }
    moved = action_result.startswith("Moved:")
    verification = {
        "passed": moved and checks["source_removed"] and checks["destination_exists"],
        "checks": checks,
    }
    recovery_needed = not verification["passed"]
    if "Source does not exist:" in action_result:
        next_step = "Ask for or apply a corrected source filename, then retry the same move."
    elif "Destination already exists:" in action_result:
        next_step = "Ask whether to overwrite, rename, or choose a different destination."
    elif recovery_needed:
        next_step = "Report the failed verification and keep undo/retry context available."
    else:
        next_step = ""
    return {
        "phase": "verified" if verification["passed"] else "recovery",
        "observation": observation,
        "plan": [
            "Observe source and destination",
            "Move source to destination",
            "Verify source is removed and destination exists",
            "Recover with correction or retry guidance if verification fails",
        ],
        "action": {"type": "file.move", "result": action_result},
        "verification": verification,
        "recovery": {"needed": recovery_needed, "next_step": next_step},
    }
