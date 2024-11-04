"""
Provides animated loading indicator for chat interactions.
"""

import sys
import time
import threading
import itertools


class LoadingAnimator:
    """Provides animated loading indicator."""

    def __init__(self):
        self.stop_loading = False
        self.thread = None

    def animate(self):
        """Animation loop for loading indicator."""
        chars = itertools.cycle(["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"])
        while not self.stop_loading:
            sys.stdout.write("\r" + "Thinking " + next(chars))
            sys.stdout.flush()
            time.sleep(0.1)
        sys.stdout.write("\r")
        sys.stdout.flush()

    def start(self):
        """Start loading animation in separate thread."""
        self.stop_loading = False
        self.thread = threading.Thread(target=self.animate)
        self.thread.daemon = True
        self.thread.start()

    def stop(self):
        """Stop loading animation."""
        self.stop_loading = True
        if self.thread:
            self.thread.join(timeout=1)
