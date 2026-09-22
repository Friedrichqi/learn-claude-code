"""LRU cache with per-entry time-to-live."""

import time
from collections import OrderedDict


class TTLCache:
    def __init__(self, capacity, clock=time.monotonic):
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        self.capacity = capacity
        self.clock = clock
        # key -> (value, expiry); expiry is None for non-expiring entries.
        # Dict order goes from least recently used to most recently used.
        self._entries = OrderedDict()

    def _purge_expired(self):
        now = self.clock()
        dead = [
            key
            for key, (value, expiry) in self._entries.items()
            if expiry is not None and now >= expiry
        ]
        for key in dead:
            del self._entries[key]
        return len(dead)

    def put(self, key, value, ttl=None):
        if ttl is not None and ttl <= 0:
            raise ValueError("ttl must be positive")
        expiry = None if ttl is None else self.clock() + ttl
        if key in self._entries:
            self._entries[key] = (value, expiry)
            self._entries.move_to_end(key)
        else:
            if len(self._entries) >= self.capacity:
                self._purge_expired()
                if len(self._entries) >= self.capacity:
                    self._entries.popitem(last=False)
            self._entries[key] = (value, expiry)
            self._entries.move_to_end(key)

    def get(self, key, default=None):
        entry = self._entries.get(key)
        if entry is None:
            return default
        value, expiry = entry
        if expiry is not None and self.clock() >= expiry:
            del self._entries[key]
            return default
        self._entries.move_to_end(key)
        return value

    def __contains__(self, key):
        entry = self._entries.get(key)
        if entry is None:
            return False
        value, expiry = entry
        return expiry is None or self.clock() < expiry

    def __len__(self):
        self._purge_expired()
        return len(self._entries)

    def keys(self):
        self._purge_expired()
        return list(self._entries)

    def evict_expired(self):
        return self._purge_expired()
