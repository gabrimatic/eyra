"""
Message history management utilities.
Handles message history cleanup and maintenance.
For clients that support image processing, removes old image messages.
"""

from typing import List, Dict


def manage_message_history(messages: List[Dict], max_messages: int = 10) -> List[Dict]:
    """
    Manage message history by removing old messages and cleaning up image data.
    
    Args:
        messages (List[Dict]): List of message dictionaries
        max_messages (int): Maximum number of messages to keep
        
    Returns:
        List[Dict]: Cleaned message history
    """
    if len(messages) <= max_messages:
        return messages

    # Keep only the most recent messages
    return messages[-max_messages:]
