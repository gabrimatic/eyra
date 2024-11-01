"""
Live mode implementation for continuous screen analysis.
Provides automated screenshot capture and analysis with voice feedback.
"""

import asyncio
from datetime import datetime
from .base_mode import BaseMode
from chat.message_handler import process_message_with_image
from utils.text_to_speech import speak_text

class LiveMode(BaseMode):
    """
    Live mode implementation for continuous screen analysis.
    Captures and analyzes screenshots at regular intervals.
    """
    
    async def run(self):
        """
        Run the live mode loop.
        
        Continuously:
        - Captures screenshots
        - Processes them with the AI
        - Provides voice feedback
        Until interrupted by user (Ctrl+C)
        """
        print("Live mode started. Press Ctrl+C to exit.")
        
        while True:
            try:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                print(f"\n[{timestamp}] Capturing screenshot...")
                
                self.messages.append({
                    "role": "user", 
                    "content": "Tell me what you see generally in the photo. Keep it short. Max 20 words."
                })
                
                response = await process_message_with_image(
                    self.client, 
                    self.messages, 
                    self.settings.IMAGE_PATH, 
                    use_selfie=False
                )
                
                self.messages.append({"role": "assistant", "content": response.content})
                print(f"[{timestamp}] Eyra: {response.content}")
                
                print("[Speaking...]")
                # Wait for speech to complete
                await speak_text(response.content)
                print("[Speech completed]")

                # Add delay after speech is complete
                # await asyncio.sleep(0.5)
                
            except KeyboardInterrupt:
                print("\nExiting live mode...")
                break
