"""LRU cache with per-entry time-to-live."""

import time
from collections import OrderedDict


class TTLCache:
    def __init__(self, capacity, clock=time.monotonic):
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        self._capacity = capacity
        self._clock = clock
        # key -> (value, expires_at or None); ordered LRU -> MRU
        self._entries = OrderedDict()

    @staticmethod
    def _is_expired(expires_at, now):
        return expires_at is not None and now >= expires_at

    def _purge_expired(self):
        now = self._clock()
        expired = [
            key
            for key, (_, expires_at) in self._entries.items()
            if self._is_expired(expires_at, now)
        ]
        for key in expired:
            del self._entries[key]
        return len(expired)

    def put(self, key, value, ttl=None):
        if ttl is not None and ttl <= 0:
            raise ValueError("ttl must be positive")
        expires_at = None if ttl is None else self._clock() + ttl
        if key in self._entries:
            self._entries[key] = (value, expires_at)
            self._entries.move_to_end(key)
            return
        if len(self._entries) >= self._capacity:
            self._purge_expired()
            if len(self._entries) >= self._capacity:
                self._entries.popitem(last=False)
        self._entries[key] = (value, expires_at)

    def get(self, key, default=None):
        entry = self._entries.get(key)
        if entry is None:
            return default
        value, expires_at = entry
        if self._is_expired(expires_at, self._clock()):
            del self._entries[key]
            return default
        self._entries.move_to_end(key)
        return value

    def __contains__(self, key):
        entry = self._entries.get(key)
        if entry is None:
            return False
        return not self._is_expired(entry[1], self._clock())

    def __len__(self):
        self._purge_expired()
        return len(self._entries)

    def keys(self):
        self._purge_expired()
        return list(self._entries.keys())

    def evict_expired(self):
        return self._purge_expired()
