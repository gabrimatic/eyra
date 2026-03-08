# manual_mode.py

"""
Manual mode for interactive chat with command-based UI.
Supports text-only tasks, image capture tasks, and chat history.
"""

import asyncio
import logging
from typing import List, Dict, Optional, Any

from .base_mode import BaseMode
from chat.message_handler import process_task_stream
from prompt_toolkit import PromptSession
from prompt_toolkit.styles import Style
from prompt_toolkit.completion import WordCompleter


class ManualMode(BaseMode):
    """
    ManualMode allows interactive text-based commands:
      - /quit to exit
      - /history to view messages
      - #image / #selfie to capture images
    """

    def __init__(
        self,
        settings: Any,
        messages: Optional[List[Dict[str, Any]]] = None,
        complexity_scorer: Optional[Any] = None,
    ):
        super().__init__(settings)
        self.messages = messages if messages else []
        self.complexity_scorer = complexity_scorer
        self.switch_requested = False

        # Create a command completer for user convenience
        self.command_completer = WordCompleter(
            [
                "/quit",
                "/history",
                "#image",
                "#selfie",
                # Common Questions
                "What is this?",
                "How does this work?",
                "Can you explain this?",
                "Why is this important?",
                "Who is this?",
                "What does this mean?",
                "Where is this from?",
                "When was this made?",
                "What is the purpose of this?",
                "What are the key details here?",
                "Can you summarize this?",
                "What should I focus on?",
                "What are the pros and cons?",
                "Is this accurate?",
                "What are the next steps?",
                "How can I fix this?",
                "What alternatives are there?",
                "What are the key takeaways?",
                "Can you compare this to another example?",
                # Common Words
                "yes",
                "no",
                "okay",
                "cancel",
                "confirm",
                "next",
                "previous",
                "start",
                "stop",
                "submit",
                "edit",
                "delete",
                "add",
                "remove",
                "select",
                "copy",
                "paste",
                "download",
                "upload",
                "file",
                "image",
                "video",
                "text",
                "document",
                "list",
                "help",
                "search",
                "filter",
                "sort",
                # General Analysis Prompts
                "Describe this image in detail",
                "What do you observe in this image?",
                "Provide a detailed breakdown of this image",
                "Analyze the visual elements thoroughly",
                "Summarize the key points in this image",
                "What stands out the most?",
                "Highlight unique or unusual features",
                "What is the main focus of this image?",
                "Can you identify all visible elements?",
                "Explain the image's context or story",
                # Object and Scene Analysis
                "What are the primary objects visible?",
                "Identify the background and foreground elements",
                "Describe the relationships between objects",
                "Are there any people, and what are they doing?",
                "What environmental factors are present?",
                "Assess the lighting and shadows",
                "What can you infer about the setting?",
                "Identify any patterns or repeated elements",
                "Point out any hidden or subtle details",
                # Text and Data Extraction
                "Extract all text visible in this image",
                "Are there logos or brand elements present?",
                "Identify any numbers, codes, or symbols",
                "Check for misspellings or errors in text",
                "Read and analyze any captions or labels",
                "Find and interpret annotations or markings",
                # Emotional and Thematic Analysis
                "What mood or tone does this image convey?",
                "Describe the atmosphere and emotions evoked",
                "What cultural or symbolic references can you find?",
                "Analyze the image's narrative or theme",
                "Does this image evoke any specific feelings?",
                # Comparison and Improvement Prompts
                "Compare this image with the previous one",
                "Identify improvements made between images",
                "Suggest enhancements for this image",
                "Point out issues or inconsistencies",
                "Offer constructive criticism for this image",
                "Analyze trends or patterns across similar images",
                # Technical and Design Analysis
                "Evaluate the technical quality of this image",
                "Analyze the composition and layout",
                "Check for color balance and harmony",
                "Inspect for pixelation or resolution issues",
                "Assess visual hierarchy and focus",
                "Review use of typography and fonts",
                "Examine spacing, alignment, and proportions",
                "Check adherence to design principles",
                "Identify potential accessibility issues",
                "Review usability and user experience (UI/UX)",
                "Measure contrast and color accessibility",
                "Analyze the interaction between elements",
                "Review the visual rhythm and balance",
                "Evaluate consistency in design style",
            ],
            ignore_case=True,
        )

        self.prompt_style = Style.from_dict({"prompt": "ansigreen bold"})
        self.session = PromptSession(
            completer=self.command_completer,
            style=self.prompt_style,
            complete_while_typing=True,
            enable_suspend=True,
            color_depth="DEPTH_24_BIT",
        )

        self.logger = logging.getLogger(self.__class__.__name__)
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            "[%(asctime)s] %(levelname)s - %(message)s", "%Y-%m-%d %H:%M:%S"
        )
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)
        self.logger.setLevel(logging.INFO)

    async def run(self) -> None:
        """
        Entry point for Manual Mode. We loop until user quits or we switch modes.
        """
        self.logger.info("Running Manual Mode...")
        while True:
            self.switch_requested = False

            # Display help text once each cycle
            self._show_help()

            # Enter main loop
            should_exit = await self._main_loop()

            if self.switch_requested:
                from .live_mode import LiveMode

                live_mode = LiveMode(
                    self.settings,
                    messages=self.messages,
                    complexity_scorer=self.complexity_scorer,
                )
                await live_mode.run()

                # If live mode requests switch back, continue
                if live_mode.switch_requested:
                    self.logger.info("Switching back to Manual Mode.")
                    continue
                else:
                    break
            if should_exit:
                break

        self.logger.info("Exiting Manual Mode.")

    async def _main_loop(self) -> bool:
        """
        Main user input loop for ManualMode.
        Returns True if the user chooses to quit.
        """
        try:
            while not self.switch_requested:
                user_input = await self.session.prompt_async(
                    "You: ", complete_while_typing=True
                )
                user_input = user_input.strip()
                if not user_input:
                    continue

                self.logger.info(f"User input: {user_input}")
                if user_input.lower() == "/quit":
                    print("Goodbye! Have a great day!")
                    return True
                elif user_input.lower() == "/history":
                    self._show_history()
                    continue

                # Add user message to history
                self.messages.append({"role": "user", "content": user_input})

                try:
                    if "#selfie" in user_input or "#image" in user_input:
                        await self._handle_image_task(user_input)
                    else:
                        await self._handle_text_task(user_input)

                except Exception as e:
                    self.logger.error(f"Request failed: {e}")
                    print("\n[Error] Request failed. Please try again.")
                finally:
                    self._print_separator()
        except (KeyboardInterrupt, asyncio.CancelledError):
            self.logger.info(
                "Keyboard interrupt or session cancelled. Exiting manual mode."
            )
            return True
        return False

    async def _handle_image_task(self, user_input: str) -> None:
        """
        Handle image-related tasks: #image or #selfie.
        """
        use_selfie = "#selfie" in user_input
        print("\nEyra:", end="", flush=True)
        full_response = ""
        async for chunk in process_task_stream(
            task_type="image",
            text_content=user_input,
            complexity_scorer=self.complexity_scorer,
            settings=self.settings,
            messages=self.messages,
            use_selfie=use_selfie,
        ):
            print(chunk, end="", flush=True)
            full_response += chunk
        print()
        if full_response:
            self.messages.append({"role": "assistant", "content": full_response})
        else:
            print("\n[Error] No valid response received. Please try again.")

    async def _handle_text_task(self, user_input: str) -> None:
        """
        Handle text-only requests (non-image).
        """
        print("\nEyra:", end="", flush=True)
        full_response = ""
        async for chunk in process_task_stream(
            task_type="text",
            text_content=user_input,
            complexity_scorer=self.complexity_scorer,
            settings=self.settings,
            messages=self.messages,
        ):
            print(chunk, end="", flush=True)
            full_response += chunk
        print()

        if full_response:
            self.messages.append({"role": "assistant", "content": full_response})
        else:
            print("\n[Error] No valid response received. Please try again.")

    def _show_help(self) -> None:
        """
        Print help text once each run cycle.
        """
        help_text = """
Available Commands
- #image: Capture and include a new screenshot
- #selfie: Capture and include webcam image
- /history: Show chat history
- Ctrl+Shift+L: Switch to live mode
- /quit: Exit
        """
        print(help_text)

    def _show_history(self) -> None:
        """
        Print the chat history for the user.
        """
        print("\n[History] Displaying chat history...")
        print("\nChat History:")
        for msg in self.messages:
            role = msg.get("role", "Unknown").title()
            content = msg.get("content", "")
            print(f"{role}: {content}")

    def _print_separator(self) -> None:
        print("\n" + "=" * 50 + "\n")
