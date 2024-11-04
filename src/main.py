"""
Main entry point for the Artemis application.
This module initializes the application, handles mode selection,
and manages the main execution flow.
"""

import asyncio
import os
import sys
from openai import OpenAI
from config.settings import Settings
from config.mock_client import MockOpenAIClient
from modes.manual_mode import ManualMode
from modes.live_mode import LiveMode


def check_accessibility_permissions():
    return True


def show_permission_instructions():
    """Display instructions for enabling accessibility permissions."""
    print("\nAccessibility permissions required!")
    print("Please follow these steps:")
    print("1. Open System Preferences")
    print("2. Go to Security & Privacy > Privacy > Accessibility")
    print("3. Click the lock icon to make changes")
    print("4. Add and enable Terminal (or your Python IDE)")
    print("\nAfter granting permissions, restart the application.")


async def simulate_typing(text: str):
    """
    Simulate a typing animation effect.

    Args:
        text (str): Text to be displayed during animation (currently unused)
    """
    for _ in range(3):
        await asyncio.sleep(0.1)
        print(".", end="", flush=True)
    print("\n")


async def main():
    """
    Main application entry point.

    Handles:
    - Loading configuration
    - Client initialization (real or mock)
    - Mode selection
    - Application execution
    """
    # Check permissions first
    if not check_accessibility_permissions():
        show_permission_instructions()
        return

    settings = Settings.load_from_env()
    use_mock = os.getenv("USE_MOCK_CLIENT", "false").lower() == "true"
    client = MockOpenAIClient() if use_mock else OpenAI(api_key=settings.API_KEY)

    print("\nSelect mode:")
    print("1. Manual Mode (Interactive chat)")
    print("2. Live Mode (Automatic screenshot analysis)")

    messages = []  # Initialize shared message history
    while True:
        mode_choice = input("\nEnter mode number (1 or 2): ").strip()
        if mode_choice in ["1", "2"]:
            mode = (
                ManualMode(client, settings, messages)
                if mode_choice == "1"
                else LiveMode(client, settings, messages)
            )
            await mode.run()
            if not mode.switch_requested:
                break
        else:
            print("Invalid choice. Please enter 1 or 2.")


if __name__ == "__main__":
    asyncio.run(main())
