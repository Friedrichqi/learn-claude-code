"""LRU cache with per-entry time-to-live."""

import time
from collections import OrderedDict


class TTLCache:
    def __init__(self, capacity, clock=time.monotonic):
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        self._capacity = capacity
        self._clock = clock
        # key -> (value, expires_at); None expires_at means never expires.
        self._d = OrderedDict()

    def _is_expired(self, expires_at):
        return expires_at is not None and self._clock() >= expires_at

    def _live(self, key):
        """Return (value, expires_at) if the key is live, else None."""
        entry = self._d.get(key)
        if entry is None:
            return None
        if self._is_expired(entry[1]):
            del self._d[key]
            return None
        return entry

    def put(self, key, value, ttl=None):
        if ttl is not None and ttl <= 0:
            raise ValueError("ttl must be positive")
        now = self._clock()
        expires_at = None if ttl is None else now + ttl
        if key in self._d:
            self._d[key] = (value, expires_at)
            self._d.move_to_end(key)
            return
        if len(self._d) >= self._capacity:
            self._purge_expired()
            while len(self._d) >= self._capacity:
                self._d.popitem(last=False)
        self._d[key] = (value, expires_at)

    def get(self, key, default=None):
        entry = self._live(key)
        if entry is None:
            return default
        self._d.move_to_end(key)
        return entry[0]

    def __contains__(self, key):
        entry = self._d.get(key)
        if entry is None:
            return False
        return not self._is_expired(entry[1])

    def __len__(self):
        self._purge_expired()
        return len(self._d)

    def keys(self):
        return [k for k, e in self._d.items() if not self._is_expired(e[1])]

    def evict_expired(self):
        return self._purge_expired()

    def _purge_expired(self):
        now = self._clock()
        expired = [
            k for k, (_, expires_at) in self._d.items()
            if expires_at is not None and now >= expires_at
        ]
        for k in expired:
            del self._d[k]
        return len(expired)
