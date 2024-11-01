"""
Main entry point for the Artemis application.
This module initializes the application, handles mode selection,
and manages the main execution flow.
"""

import asyncio
import os
from openai import OpenAI
from config.settings import Settings
from config.mock_client import MockOpenAIClient
from modes.manual_mode import ManualMode
from modes.live_mode import LiveMode

async def simulate_typing(text: str):
    """
    Simulate a typing animation effect.
    
    Args:
        text (str): Text to be displayed during animation (currently unused)
    """
    print("\nEyra is typing", end="")
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
    settings = Settings.load_from_env()
    use_mock = os.getenv("USE_MOCK_CLIENT", "false").lower() == "true"
    client = MockOpenAIClient() if use_mock else OpenAI(api_key=settings.API_KEY)

    print("\nSelect mode:")
    print("1. Manual Mode (Interactive chat)")
    print("2. Live Mode (Automatic screenshot analysis)")
    
    while True:
        mode_choice = input("\nEnter mode number (1 or 2): ").strip()
        if mode_choice in ['1', '2']:
            break
        print("Invalid choice. Please enter 1 or 2.")

    mode = ManualMode(client, settings) if mode_choice == '1' else LiveMode(client, settings)
    await mode.run()

if __name__ == "__main__":
    asyncio.run(main())
