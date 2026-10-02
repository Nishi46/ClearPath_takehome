import threading


class Cooldown:
    """Allow one action per `seconds`. In memory and per process, which is enough for one demo server."""

    def __init__(self, seconds):
        self.seconds = seconds
        self._last = None
        self._previous = None
        self._lock = threading.Lock()

    def acquire(self, now):
        """Return 0.0 and start the cooldown, or the seconds still to wait. Atomic across threads."""
        with self._lock:
            if self._last is not None:
                remaining = self.seconds - (now - self._last).total_seconds()
                if remaining > 0:
                    return remaining
            self._previous, self._last = self._last, now
            return 0.0

    def release(self):
        """Undo the last acquire, so a failed action does not lock out a retry."""
        with self._lock:
            self._last = self._previous

    def clear(self):
        with self._lock:
            self._last = self._previous = None
