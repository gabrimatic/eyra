"""Compact local memory and user instruction context for Eyra."""

from runtime.memory.files import ensure_instruction_files, instruction_context_messages
from runtime.memory.service import MemoryService, memory_context_messages

__all__ = [
    "MemoryService",
    "ensure_instruction_files",
    "instruction_context_messages",
    "memory_context_messages",
]
