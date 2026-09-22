"""LRU cache with per-entry time-to-live (see README.md in this directory)."""

import time
from collections import OrderedDict

_MISSING = object()


class TTLCache:
    """An LRU cache in which entries can carry an individual lifetime."""

    def __init__(self, capacity, clock=time.monotonic):
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        self.capacity = capacity
        self.clock = clock
        # key -> (value, expires_at); expires_at is None for immortal entries.
        # OrderedDict preserves least-recently-used -> most-recently-used order.
        self._data = OrderedDict()

    @staticmethod
    def _expired(expires_at, now):
        return expires_at is not None and now >= expires_at

    def _purge(self, now):
        """Delete every expired entry; return how many were removed."""
        expired = [k for k, (_v, exp) in self._data.items()
                   if self._expired(exp, now)]
        for key in expired:
            del self._data[key]
        return len(expired)

    def put(self, key, value, ttl=None):
        if ttl is not None and ttl <= 0:
            raise ValueError("ttl must be positive")
        now = self.clock()
        expires_at = None if ttl is None else now + ttl
        if key in self._data:
            # Replace value, restart lifetime, bump to most recently used.
            self._data.move_to_end(key)
            self._data[key] = (value, expires_at)
            return
        if len(self._data) >= self.capacity:
            self._purge(now)
            if len(self._data) >= self.capacity:
                self._data.popitem(last=False)  # evict least recently used
        self._data[key] = (value, expires_at)

    def get(self, key, default=None):
        entry = self._data.get(key, _MISSING)
        if entry is _MISSING:
            return default
        value, expires_at = entry
        if self._expired(expires_at, self.clock()):
            del self._data[key]
            return default
        self._data.move_to_end(key)
        return value

    def __contains__(self, key):
        entry = self._data.get(key, _MISSING)
        if entry is _MISSING:
            return False
        # Intentionally does not change recency and does not purge.
        return not self._expired(entry[1], self.clock())

    def __len__(self):
        self._purge(self.clock())
        return len(self._data)

    def keys(self):
        self._purge(self.clock())
        return list(self._data.keys())

    def evict_expired(self):
        return self._purge(self.clock())
