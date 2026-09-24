"""LRU cache with per-entry time-to-live."""

import time
from collections import OrderedDict


class TTLCache:
    """A fixed-capacity LRU cache where each entry may have its own TTL."""

    def __init__(self, capacity, clock=time.monotonic):
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self._capacity = capacity
        self._clock = clock
        # key -> (value, expires_at) where expires_at is None for no expiry
        self._data = OrderedDict()
        self._expires = {}

    # -- helpers ---------------------------------------------------------
    def _expired(self, key):
        expires_at = self._expires.get(key)
        if expires_at is None:
            return False
        return self._clock() >= expires_at

    def _remove(self, key):
        self._data.pop(key, None)
        self._expires.pop(key, None)

    # -- public API ------------------------------------------------------
    def put(self, key, value, ttl=None):
        if ttl is not None and ttl <= 0:
            raise ValueError("ttl must be > 0 or None")

        now = self._clock()

        if key not in self._data:
            if len(self._data) >= self._capacity:
                self.evict_expired()
            if len(self._data) >= self._capacity:
                oldest, _ = self._data.popitem(last=False)
                self._expires.pop(oldest, None)

        self._data[key] = value
        self._data.move_to_end(key)
        self._expires[key] = None if ttl is None else now + ttl

    def get(self, key, default=None):
        if key not in self._data:
            return default
        if self._expired(key):
            self._remove(key)
            return default
        self._data.move_to_end(key)
        return self._data[key]

    def __contains__(self, key):
        if key not in self._data:
            return False
        if self._expired(key):
            self._remove(key)
            return False
        return True

    def __len__(self):
        self.evict_expired()
        return len(self._data)

    def keys(self):
        self.evict_expired()
        return list(self._data.keys())

    def evict_expired(self):
        now = self._clock()
        expired = [
            key
            for key, expires_at in self._expires.items()
            if expires_at is not None and now >= expires_at
        ]
        for key in expired:
            self._remove(key)
        return len(expired)
