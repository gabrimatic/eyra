"""
Mock AI client for testing purposes.
Simulates the behavior of the AI API for development and testing.
"""

from typing import List, Dict, Optional
from clients.base_client import BaseAIClient


class MockAIClient(BaseAIClient):
    """Mock client for testing without using the actual API."""

    async def generate_completion(
        self, messages: List[Dict], model_name: Optional[str] = None
    ) -> Dict:
        """Mock completion generation."""
        return {"content": "This is a mock response for testing purposes."}

    async def generate_completion_with_image(
        self,
        messages: List[Dict],
        image_base64: str,
        model_name: Optional[str] = None,
    ) -> Dict:
        """Mock image-based completion generation."""
        return {
            "content": "This is a mock response for an image-based query for testing purposes."
        }

    class Message:
        def __init__(self, content, role):
            """
            Initialize a mock message.

            Args:
                content (str): Message content
                role (str): Role of the message sender (e.g., 'user', 'assistant')
            """
            self.content = content
            self.role = role

    class Choice:
        def __init__(self, message):
            """
            Initialize a mock choice.

            Args:
                message (MockAIClient.Message): Mock message instance
            """
            self.message = message

    class Response:
        def __init__(self, choices):
            """
            Initialize a mock response.

            Args:
                choices (list): List of mock choices
            """
            self.choices = choices

    class Chat:
        class Completions:
            @staticmethod
            def create(model, messages):
                """
                Simulate the completion creation.

                Args:
                    model (str): Model name
                    messages (list): List of messages

                Returns:
                    MockAIClient.Response: Mock response instance
                """
                message = MockAIClient.Message(
                    "This is a mock response. This is a test. Hahah!!", "assistant"
                )
                choice = MockAIClient.Choice(message)
                return MockAIClient.Response([choice])

    def __init__(self):
        self.chat = self.Chat()
        self.chat.completions = self.Chat.Completions()
