"""
Message handling and processing utilities.
Manages chat messages, image processing, and API interactions.
"""

from typing import List, Dict
from openai import OpenAI
from image.capture import capture_screenshot, capture_selfie, encode_image
from chat.loading_animator import LoadingAnimator
from config.settings import Settings

async def process_message_with_image(client: OpenAI, messages: List[Dict], image_path: str, use_selfie: bool = False) -> Dict:
    """
    Process a message that includes image capture and API interaction.
    
    Args:
        client (OpenAI): OpenAI client instance
        messages (List[Dict]): Message history
        image_path (str): Path for saving captured image
        use_selfie (bool): Whether to use webcam instead of screenshot
        
    Returns:
        Dict: API response message
    """
    # Clean up old image data from previous messages
    for message in messages:
        if isinstance(message["content"], list):
            text_content = next((content["text"] for content in message["content"] if content["type"] == "text"), "")
            message["content"] = text_content

    # Capture new image based on mode
    if use_selfie:
        await capture_selfie(image_path)
    else:
        await capture_screenshot(image_path)
        
    base64_image = encode_image(image_path)
    
    messages[-1] = {
        "role": "user",
        "content": [
            {"type": "text", "text": messages[-1]["content"].replace('#image', '').replace('#selfie', '').strip()},
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{base64_image}",
                    #"detail": "low",
                },
            },
        ],
    }
    return get_completion(client, messages)

def display_history(messages: List[Dict]):
    """Display chat history."""
    print("\nChat History:")
    for msg in messages:
        role = msg["role"]
        if isinstance(msg["content"], list):
            # Message contains image
            text = next((content["text"] for content in msg["content"] if content["type"] == "text"), "")
            base64_data = next((content["image_url"]["url"] for content in msg["content"] if content["type"] == "image_url"), "")
            if base64_data:
                base64_preview = base64_data[:50] + "..."
                print(f"{role}: {text} [Image: {base64_preview}]")
            else:
                print(f"{role}: {text}")
        else:
            # Regular text message
            print(f"{role}: {msg['content']}")
    print()

def get_completion(client: OpenAI, messages: List[Dict]) -> Dict:
    """Get completion from OpenAI API."""
    loading = LoadingAnimator()
    loading.start()
    try:
        settings = Settings.load_from_env()
        response = client.chat.completions.create(
            model=settings.MODEL_NAME,
            messages=messages,
            max_tokens=settings.MAX_TOKENS,
        )
    finally:
        loading.stop()
    return response.choices[0].message
