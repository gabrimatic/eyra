"""
Mock OpenAI client for testing purposes.
Simulates the behavior of the OpenAI API for development and testing.
"""

class MockOpenAIClient:
    """Mock OpenAI client for testing purposes."""

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
                message (MockOpenAIClient.Message): Mock message instance
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
            def create(model, messages, max_tokens):
                """
                Simulate the completion creation.
                
                Args:
                    model (str): Model name
                    messages (list): List of messages
                    max_tokens (int): Maximum number of tokens
                
                Returns:
                    MockOpenAIClient.Response: Mock response instance
                """
                message = MockOpenAIClient.Message("This is a mock response. This is a test. Hahah!!", "assistant")
                choice = MockOpenAIClient.Choice(message)
                return MockOpenAIClient.Response([choice])

    def __init__(self):
        self.chat = self.Chat()
        self.chat.completions = self.Chat.Completions()
