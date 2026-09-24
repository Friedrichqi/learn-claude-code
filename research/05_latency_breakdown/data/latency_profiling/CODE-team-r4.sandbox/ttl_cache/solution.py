"""LRU cache with per-entry time-to-live."""

import time
from collections import OrderedDict


class TTLCache:
    def __init__(self, capacity, clock=time.monotonic):
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        self._capacity = capacity
        self._clock = clock
        # key -> (value, expires_at or None), ordered least- to most-recently used
        self._data = OrderedDict()

    def put(self, key, value, ttl=None):
        if ttl is not None and ttl <= 0:
            raise ValueError("ttl must be positive")
        expires_at = None if ttl is None else self._clock() + ttl
        if key in self._data:
            # Replace: new value, restarted lifetime, becomes most recently used.
            del self._data[key]
        else:
            # New key: drop expired entries first, then evict LRU if still full.
            self._evict_expired()
            while len(self._data) >= self._capacity:
                self._data.popitem(last=False)
        self._data[key] = (value, expires_at)

    def get(self, key, default=None):
        entry = self._data.get(key)
        if entry is None:
            return default
        value, expires_at = entry
        if expires_at is not None and self._clock() >= expires_at:
            del self._data[key]
            return default
        self._data.move_to_end(key)
        return value

    def __contains__(self, key):
        entry = self._data.get(key)
        if entry is None:
            return False
        expires_at = entry[1]
        return expires_at is None or self._clock() < expires_at

    def __len__(self):
        self._evict_expired()
        return len(self._data)

    def keys(self):
        now = self._clock()
        return [
            key
            for key, (_, expires_at) in self._data.items()
            if expires_at is None or now < expires_at
        ]

    def evict_expired(self):
        return self._evict_expired()

    def _evict_expired(self):
        now = self._clock()
        expired = [
            key
            for key, (_, expires_at) in self._data.items()
            if expires_at is not None and now >= expires_at
        ]
        for key in expired:
            del self._data[key]
        return len(expired)
