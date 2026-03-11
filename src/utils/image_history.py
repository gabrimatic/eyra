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


def manage_message_history(messages: list[dict], max_messages: int = 10) -> list[dict]:
    """
    Return a trimmed, cleaned copy of the message history.

    - Keeps at most *max_messages* recent entries.
    - Strips base64 image payloads from all but the last message
      (the last message may intentionally carry an image for the
      current request).
    """
    recent = messages[-max_messages:] if len(messages) > max_messages else list(messages)

    # Strip image payloads from everything except the final message
    cleaned = [_strip_image_content(m) for m in recent[:-1]] if recent else []
    if recent:
        cleaned.append(recent[-1])
    return cleaned
