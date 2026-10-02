"""LRU cache with per-entry time-to-live."""

import time
from collections import OrderedDict


class TTLCache:
    def __init__(self, capacity, clock=time.monotonic):
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        self._capacity = capacity
        self._clock = clock
        # key -> [value, expires_at or None]
        self._entries = OrderedDict()

    def _purge_expired(self):
        now = self._clock()
        dead = [k for k, (v, exp) in self._entries.items()
                if exp is not None and now >= exp]
        for k in dead:
            del self._entries[k]
        return len(dead)

    def put(self, key, value, ttl=None):
        if ttl is not None and ttl <= 0:
            raise ValueError("ttl must be positive")
        now = self._clock()
        if key in self._entries:
            self._entries.move_to_end(key)
        else:
            if len(self._entries) >= self._capacity:
                self._purge_expired()
                if len(self._entries) >= self._capacity:
                    self._entries.popitem(last=False)
        exp = None if ttl is None else now + ttl
        self._entries[key] = [value, exp]

    def get(self, key, default=None):
        if key not in self._entries:
            return default
        value, exp = self._entries[key]
        if exp is not None and self._clock() >= exp:
            del self._entries[key]
            return default
        self._entries.move_to_end(key)
        return value

    def __contains__(self, key):
        if key not in self._entries:
            return False
        _, exp = self._entries[key]
        if exp is not None and self._clock() >= exp:
            del self._entries[key]
            return False
        return True

    def __len__(self):
        self._purge_expired()
        return len(self._entries)

    def keys(self):
        self._purge_expired()
        return list(self._entries.keys())

    def evict_expired(self):
        return self._purge_expired()
