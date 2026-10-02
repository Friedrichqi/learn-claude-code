"""LRU cache with per-entry time-to-live."""

import time
from collections import OrderedDict


class TTLCache:
    def __init__(self, capacity, clock=time.monotonic):
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        self._capacity = capacity
        self._clock = clock
        # key -> (value, deadline); deadline is None for entries that never
        # expire. OrderedDict preserves recency: least recent at the front.
        self._entries = OrderedDict()

    def _purge_expired(self):
        now = self._clock()
        expired = [
            key
            for key, (_value, deadline) in self._entries.items()
            if deadline is not None and now >= deadline
        ]
        for key in expired:
            del self._entries[key]
        return len(expired)

    def put(self, key, value, ttl=None):
        if ttl is not None and ttl <= 0:
            raise ValueError("ttl must be positive")
        if key not in self._entries:
            self._purge_expired()
            if len(self._entries) >= self._capacity:
                self._entries.popitem(last=False)
        deadline = None if ttl is None else self._clock() + ttl
        self._entries[key] = (value, deadline)
        self._entries.move_to_end(key)

    def get(self, key, default=None):
        entry = self._entries.get(key)
        if entry is None:
            return default
        value, deadline = entry
        if deadline is not None and self._clock() >= deadline:
            del self._entries[key]
            return default
        self._entries.move_to_end(key)
        return value

    def __contains__(self, key):
        entry = self._entries.get(key)
        if entry is None:
            return False
        _value, deadline = entry
        return deadline is None or self._clock() < deadline

    def __len__(self):
        self._purge_expired()
        return len(self._entries)

    def keys(self):
        self._purge_expired()
        return list(self._entries)

    def evict_expired(self):
        return self._purge_expired()
