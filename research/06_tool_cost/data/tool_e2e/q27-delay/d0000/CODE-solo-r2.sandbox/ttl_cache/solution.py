"""LRU cache with per-entry time-to-live."""

import time
from collections import OrderedDict


class TTLCache:
    def __init__(self, capacity, clock=time.monotonic):
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        self._capacity = capacity
        self._clock = clock
        # key -> [value, expires] with expires None meaning "never expires"
        self._entries = OrderedDict()

    def _purge_expired(self):
        now = self._clock()
        dead = [key for key, (_value, expires) in self._entries.items()
                if expires is not None and now >= expires]
        for key in dead:
            del self._entries[key]
        return len(dead)

    def put(self, key, value, ttl=None):
        if ttl is not None and ttl <= 0:
            raise ValueError("ttl must be positive or None")
        now = self._clock()
        expires = None if ttl is None else now + ttl
        if key in self._entries:
            # Replace value, restart lifetime, mark most recently used.
            self._entries[key] = (value, expires)
            self._entries.move_to_end(key)
            return
        if len(self._entries) >= self._capacity:
            self._purge_expired()
            if len(self._entries) >= self._capacity:
                self._entries.popitem(last=False)
        self._entries[key] = (value, expires)

    def get(self, key, default=None):
        entry = self._entries.get(key)
        if entry is None:
            return default
        value, expires = entry
        if expires is not None and self._clock() >= expires:
            del self._entries[key]
            return default
        self._entries.move_to_end(key)
        return value

    def __contains__(self, key):
        entry = self._entries.get(key)
        if entry is None:
            return False
        _value, expires = entry
        if expires is None:
            return True
        return self._clock() < expires

    def __len__(self):
        self._purge_expired()
        return len(self._entries)

    def keys(self):
        self._purge_expired()
        return list(self._entries)

    def evict_expired(self):
        return self._purge_expired()
