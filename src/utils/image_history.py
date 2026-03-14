"""
Message history management utilities.
Handles message history cleanup and maintenance.
Strips base64 image payloads from older messages so text-only
requests don't accidentally resend large multipart content.
"""

def _strip_image_content(msg: dict) -> dict:
    """Replace multipart image messages with their text-only portion."""
    content = msg.get("content")
    if not isinstance(content, list):
        return msg
    text_parts = [
        item.get("text", "") for item in content if item.get("type") == "text"
    ]
    text = " ".join(t for t in text_parts if t).strip()
    return {**msg, "content": text or "[image]"}


def manage_message_history(messages: list[dict], max_turns: int = 10, max_messages: int | None = None) -> list[dict]:
    """
    Return a trimmed, cleaned copy of the message history.

    - Keeps at most *max_turns* user/assistant turn pairs plus their
      associated tool messages (tool call results, image messages).
    - Strips base64 image payloads from all but the last message
      (the last message may intentionally carry an image for the
      current request).
    """
    # Support legacy max_messages keyword
    if max_messages is not None:
        max_turns = max_messages

    if not messages:
        return []

    # Count only user and plain-assistant messages as "turns"
    _TURN_ROLES = {"user", "assistant"}
    turn_count = 0
    cut_index = 0
    for i in range(len(messages) - 1, -1, -1):
        role = messages[i].get("role", "")
        if role in _TURN_ROLES:
            turn_count += 1
            if turn_count > max_turns:
                cut_index = i + 1
                break

    recent = list(messages[cut_index:])

    # Strip image payloads from everything except the final message
    cleaned = [_strip_image_content(m) for m in recent[:-1]] if recent else []
    if recent:
        cleaned.append(recent[-1])
    return cleaned
