"""Small, shared download budget which yields to interactive work."""
import threading
import time


class DownloadBudget:
    def __init__(self, active_mib=16, idle_mib=64, *, clock=time.monotonic, sleep=time.sleep):
        self.active_rate = max(1, float(active_mib)) * 1024**2
        self.idle_rate = max(self.active_rate, float(idle_mib) * 1024**2)
        self._clock, self._sleep = clock, sleep
        self._active_until = 0.0
        self._tokens = 0.0
        self._updated = clock()
        self._lock = threading.Lock()

    def touch(self):
        with self._lock:
            now = self._clock()
            # Do not carry an idle burst into a new interactive session.
            if now >= self._active_until:
                self._tokens = 0.0
                self._updated = now
            self._active_until = now + 30

    def consume(self, size):
        remaining = size
        while remaining >= 1:
            with self._lock:
                now = self._clock()
                rate = self.active_rate if now < self._active_until else self.idle_rate
                self._tokens = min(1024**2, self._tokens + max(0, now - self._updated) * rate)
                self._updated = now
                spent = min(remaining, self._tokens)
                remaining -= spent
                self._tokens -= spent
                delay = min(0.05, remaining / rate)
            if remaining >= 1:
                self._sleep(delay)
