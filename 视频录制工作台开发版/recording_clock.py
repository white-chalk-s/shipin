"""Monotonic active recording time, excluding pauses and encoder finalization."""
import threading
import time


class RecordingClock:
    def __init__(self, now=time.perf_counter_ns):
        self._now = now
        self._lock = threading.Lock()
        self._started = None
        self._pause_started = None
        self._paused_ns = 0
        self._stopped = None
        self._pause_pending = False

    def start(self):
        with self._lock:
            if self._started is None and self._stopped is None:
                self._started = self._now()
                if self._pause_pending:
                    self._pause_started = self._started

    def pause(self):
        with self._lock:
            if self._started is None and self._stopped is None:
                self._pause_pending = True
            if self._started is not None and self._stopped is None and self._pause_started is None:
                self._pause_started = self._now()

    def resume(self):
        with self._lock:
            self._pause_pending = False
            if self._pause_started is not None and self._stopped is None:
                self._paused_ns += self._now() - self._pause_started
                self._pause_started = None

    def stop(self):
        with self._lock:
            if self._stopped is None:
                self._stopped = self._now()

    def elapsed_ns(self, *, only_when_active=False):
        with self._lock:
            if self._started is None:
                return None if only_when_active else 0
            if only_when_active and (self._pause_started is not None or self._stopped is not None):
                return None
            end = self._stopped if self._stopped is not None else self._now()
            if self._pause_started is not None:
                end = min(end, self._pause_started)
            return max(0, end - self._started - self._paused_ns)
