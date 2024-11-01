"""
Image capture and processing utilities.
Handles screenshot capture, webcam capture, and image optimization.
"""

import os
import base64
from PIL import Image
import asyncio
from utils.sound_player import play_sound

def optimize_image(image_path, max_size=(800, 600), quality=70):
    """
    Optimize image size and quality for API transmission.
    
    Args:
        image_path (str): Path to the image file
        max_size (tuple): Maximum dimensions (width, height)
        quality (int): JPEG quality (0-100)
    """
    with Image.open(image_path) as img:
        # Convert to RGB if necessary
        if img.mode in ('RGBA', 'P'):
            img = img.convert('RGB')
        # Resize if larger than max_size
        if img.size[0] > max_size[0] or img.size[1] > max_size[1]:
            img.thumbnail(max_size, Image.Resampling.LANCZOS)
        # Save optimized image
        img.save(image_path, 'JPEG', quality=quality, optimize=True)

async def capture_screenshot(image_path):
    """
    Capture screen and save to specified path.
    
    Args:
        image_path (str): Path where screenshot will be saved
        
    Plays camera sound and optimizes image after capture.
    """
    try:
        # Play sound first
        await play_sound("camera")
        # Capture screen
        os.system(f"screencapture -x {image_path}")
        # Optimize after capture
        optimize_image(image_path)
    except Exception as e:
        print(f"Error during screenshot capture: {e}")

async def capture_selfie(image_path):
    """
    Capture webcam image and save to specified path.
    
    Args:
        image_path (str): Path where selfie will be saved
        
    Plays camera sound and optimizes image after capture.
    """
    try:
        # Play sound first
        await play_sound("camera")
        # Capture selfie
        os.system(f"imagesnap {image_path}")
        # Optimize after capture
        optimize_image(image_path)
    except Exception as e:
        print(f"Error during selfie capture: {e}")

def encode_image(image_path):
    """Encode image file to base64 string."""
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')
