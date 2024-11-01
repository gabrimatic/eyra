"""
Manual mode implementation for interactive chat.
Provides command-based interaction with image capture and analysis capabilities.
"""

import asyncio
from .base_mode import BaseMode
from chat.message_handler import process_message_with_image, get_completion, display_history

class ManualMode(BaseMode):
    """
    Manual mode implementation allowing interactive chat with commands.
    Supports image capture, history viewing, and natural conversation.
    """
    
    async def run(self):
        """
        Run the manual mode loop.
        
        Supports commands:
        - '#image': Capture and analyze screenshot
        - '#selfie': Capture and analyze webcam image
        - '/history': Display chat history
        - '/quit': Exit the application
        """
        print("Manual mode started. Commands:\n- '#image': Capture and include a new screenshot\n- '#selfie': Capture and include webcam image\n- '/history': Show chat history\n- '/quit': Exit")
        
        while True:
            user_input = input("\nYou: ").strip()
            
            if user_input.lower() == '/quit':
                print("\Eyra: Goodbye! Have a great day!")
                break
            elif user_input.lower() == '/history':
                display_history(self.messages)
                continue
                
            self.messages.append({"role": "user", "content": user_input})
            
            if '#selfie' in user_input:
                response = await process_message_with_image(self.client, self.messages, self.settings.IMAGE_PATH, use_selfie=True)
            elif '#image' in user_input:
                response = await process_message_with_image(self.client, self.messages, self.settings.IMAGE_PATH, use_selfie=False)
            else:
                response = get_completion(self.client, self.messages)

            self.messages.append({"role": "assistant", "content": response.content})
            print(f"\nEyra:", response.content)
