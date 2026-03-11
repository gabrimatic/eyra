"""Tests for message history management and image payload stripping."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils.image_history import _strip_image_content, manage_message_history


class TestStripImageContent:
    def test_plain_text_unchanged(self):
        msg = {"role": "user", "content": "hello"}
        assert _strip_image_content(msg) == msg

    def test_multipart_stripped_to_text(self):
        msg = {
            "role": "user",
            "content": [
                {"type": "text", "text": "Describe this image"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA..."}}
            ]
        }
        result = _strip_image_content(msg)
        assert result["content"] == "Describe this image"
        assert result["role"] == "user"

    def test_multipart_no_text_becomes_placeholder(self):
        msg = {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA..."}}
            ]
        }
        result = _strip_image_content(msg)
        assert result["content"] == "[image]"


class TestManageMessageHistory:
    def test_within_limit_returns_copy(self):
        msgs = [{"role": "user", "content": "hi"}]
        result = manage_message_history(msgs, max_messages=10)
        assert result == msgs
        assert result is not msgs  # must be a copy

    def test_trims_to_max(self):
        msgs = [{"role": "user", "content": f"msg {i}"} for i in range(20)]
        result = manage_message_history(msgs, max_messages=5)
        assert len(result) == 5
        assert result[-1]["content"] == "msg 19"

    def test_strips_images_from_older_messages(self):
        image_msg = {
            "role": "user",
            "content": [
                {"type": "text", "text": "Look at this"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,HUGE..."}}
            ]
        }
        text_msg = {"role": "assistant", "content": "I see a cat"}
        last_msg = {"role": "user", "content": "what else?"}

        result = manage_message_history([image_msg, text_msg, last_msg])

        # First message (image) should be stripped
        assert isinstance(result[0]["content"], str)
        assert "base64" not in result[0]["content"]
        # Last message preserved as-is
        assert result[-1] == last_msg

    def test_last_message_preserves_image(self):
        """The last message may carry an image for the current request."""
        image_msg = {
            "role": "user",
            "content": [
                {"type": "text", "text": "Describe this"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}
            ]
        }
        result = manage_message_history([image_msg])
        assert isinstance(result[0]["content"], list)

    def test_does_not_mutate_original(self):
        image_msg = {
            "role": "user",
            "content": [
                {"type": "text", "text": "Look at this"},
                {"type": "image_url", "image_url": {"url": "data:base64,BIG"}}
            ]
        }
        msgs = [image_msg, {"role": "user", "content": "next"}]
        manage_message_history(msgs)
        # Original should still have the image payload
        assert isinstance(msgs[0]["content"], list)
