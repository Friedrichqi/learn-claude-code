"""LRU cache with per-entry time-to-live (see README.md)."""

import time
from collections import OrderedDict


class TTLCache:
    """An LRU cache whose entries can expire after a per-entry lifetime."""

    _MISSING = object()

    def __init__(self, capacity, clock=time.monotonic):
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self.capacity = capacity
        self.clock = clock
        # key -> (value, expires_at or None); ordered from least to most recent
        self._entries = OrderedDict()

    @staticmethod
    def _is_expired(expires_at, now):
        return expires_at is not None and now >= expires_at

    def _purge_expired(self):
        now = self.clock()
        dead = [
            key
            for key, (_, expires_at) in self._entries.items()
            if self._is_expired(expires_at, now)
        ]
        for key in dead:
            del self._entries[key]
        return len(dead)

    def put(self, key, value, ttl=None):
        if ttl is not None and ttl <= 0:
            raise ValueError("ttl must be positive")
        now = self.clock()
        expires_at = None if ttl is None else now + ttl
        if key in self._entries:
            # Replace: restart lifetime and bump recency.
            del self._entries[key]
        else:
            # New key: drop expired entries first, then LRU-evict if needed.
            if len(self._entries) >= self.capacity:
                self._purge_expired()
            while len(self._entries) >= self.capacity:
                self._entries.popitem(last=False)
        self._entries[key] = (value, expires_at)

    def get(self, key, default=None):
        entry = self._entries.get(key, self._MISSING)
        if entry is self._MISSING:
            return default
        value, expires_at = entry
        if self._is_expired(expires_at, self.clock()):
            del self._entries[key]
            return default
        self._entries.move_to_end(key)
        return value

    def __contains__(self, key):
        entry = self._entries.get(key, self._MISSING)
        if entry is self._MISSING:
            return False
        _, expires_at = entry
        if self._is_expired(expires_at, self.clock()):
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
