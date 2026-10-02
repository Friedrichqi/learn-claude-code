"""LRU cache with per-entry time-to-live."""

import time
from collections import OrderedDict


class TTLCache:
    def __init__(self, capacity, clock=time.monotonic):
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self.capacity = capacity
        self.clock = clock
        self._data = OrderedDict()  # key -> (value, expiry or None)

    def _expiry(self, ttl):
        if ttl is None:
            return None
        return self.clock() + ttl

    def _purge_expired(self):
        now = self.clock()
        expired = [k for k, (_, e) in self._data.items()
                   if e is not None and now >= e]
        for k in expired:
            del self._data[k]
        return len(expired)

    def put(self, key, value, ttl=None):
        if ttl is not None and ttl <= 0:
            raise ValueError("ttl must be positive")
        if key in self._data:
            del self._data[key]
        elif len(self._data) >= self.capacity:
            self._purge_expired()
            if len(self._data) >= self.capacity:
                self._data.popitem(last=False)
        self._data[key] = (value, self._expiry(ttl))

    def get(self, key, default=None):
        entry = self._data.get(key)
        if entry is None:
            return default
        value, expiry = entry
        if expiry is not None and self.clock() >= expiry:
            del self._data[key]
            return default
        self._data.move_to_end(key)
        return value

    def __contains__(self, key):
        entry = self._data.get(key)
        if entry is None:
            return False
        _, expiry = entry
        if expiry is not None and self.clock() >= expiry:
            del self._data[key]
            return False
        return True

    def __len__(self):
        self._purge_expired()
        return len(self._data)

    def keys(self):
        self._purge_expired()
        return list(self._data.keys())

    def evict_expired(self):
        return self._purge_expired()
