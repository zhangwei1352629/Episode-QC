"""Bounded, replaceable read-ahead work; never build up a navigation backlog."""
from __future__ import annotations

import logging
import threading


class PlaybackQueue:
    def __init__(self, run):
        self._run = run
        self._condition = threading.Condition()
        self._pending = []
        self._active = None
        self._closed = False
        self._thread = None

    def replace(self, jobs):
        with self._condition:
            if self._closed:
                return
            self._pending = [job for job in dict.fromkeys(jobs) if job != self._active][:3]
            if self._thread is None:
                self._thread = threading.Thread(target=self._work, daemon=True, name="qc-playback-read-ahead")
                self._thread.start()
            self._condition.notify()

    def _work(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or self._pending)
                if self._closed:
                    return
                self._active = self._pending.pop(0)
                job = self._active
            try:
                self._run(*job)
            except Exception:
                logging.getLogger(__name__).exception("playback read-ahead failed: %s", job)
            finally:
                with self._condition:
                    self._active = None

    def close(self):
        with self._condition:
            self._closed = True
            self._pending.clear()
            self._condition.notify_all()
