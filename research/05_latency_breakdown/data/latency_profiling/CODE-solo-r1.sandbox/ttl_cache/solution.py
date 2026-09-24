"""LRU cache with per-entry time-to-live."""

import time
from collections import OrderedDict


class TTLCache:
    def __init__(self, capacity, clock=time.monotonic):
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self._capacity = capacity
        self._clock = clock
        self._entries = OrderedDict()  # key -> (expire_time or None, value)

    def _expired(self, expire_at, now):
        return expire_at is not None and now >= expire_at

    def put(self, key, value, ttl=None):
        if ttl is not None and ttl <= 0:
            raise ValueError("ttl must be positive")
        now = self._clock()
        if key in self._entries:
            del self._entries[key]
        elif len(self._entries) >= self._capacity:
            self.evict_expired()
            while len(self._entries) >= self._capacity:
                self._entries.popitem(last=False)
        expire_at = None if ttl is None else now + ttl
        self._entries[key] = (expire_at, value)

    def get(self, key, default=None):
        entry = self._entries.get(key)
        if entry is None:
            return default
        expire_at, value = entry
        if self._expired(expire_at, self._clock()):
            del self._entries[key]
            return default
        self._entries.move_to_end(key)
        return value

    def evict_expired(self):
        now = self._clock()
        expired = [
            key
            for key, (expire_at, _) in self._entries.items()
            if self._expired(expire_at, now)
        ]
        for key in expired:
            del self._entries[key]
        return len(expired)

    def keys(self):
        self.evict_expired()
        return list(self._entries.keys())

    def __contains__(self, key):
        entry = self._entries.get(key)
        if entry is None:
            return False
        if self._expired(entry[0], self._clock()):
            del self._entries[key]
            return False
        return True

    def __len__(self):
        self.evict_expired()
        return len(self._entries)
