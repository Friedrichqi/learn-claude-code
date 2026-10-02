"""LRU cache with per-entry time-to-live."""

import time
from collections import OrderedDict


class TTLCache:
    def __init__(self, capacity, clock=time.monotonic):
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        self._capacity = capacity
        self._clock = clock
        # key -> (value, expiry or None); ordered LRU -> MRU
        self._data = OrderedDict()

    def _is_expired(self, entry):
        expiry = entry[1]
        return expiry is not None and self._clock() >= expiry

    def put(self, key, value, ttl=None):
        if ttl is not None and ttl <= 0:
            raise ValueError("ttl must be positive")
        expiry = None if ttl is None else self._clock() + ttl
        if key in self._data:
            self._data[key] = (value, expiry)
            self._data.move_to_end(key)
            return
        if len(self._data) >= self._capacity:
            self.evict_expired()
            if len(self._data) >= self._capacity:
                self._data.popitem(last=False)  # evict LRU
        self._data[key] = (value, expiry)

    def get(self, key, default=None):
        if key not in self._data:
            return default
        if self._is_expired(self._data[key]):
            del self._data[key]
            return default
        self._data.move_to_end(key)
        return self._data[key][0]

    def _purge(self):
        dead = [key for key, entry in self._data.items() if self._is_expired(entry)]
        for key in dead:
            del self._data[key]

    def __contains__(self, key):
        if key not in self._data:
            return False
        if self._is_expired(self._data[key]):
            del self._data[key]
            return False
        return True

    def __len__(self):
        self._purge()
        return len(self._data)

    def keys(self):
        self._purge()
        return list(self._data)

    def evict_expired(self):
        before = len(self._data)
        self._purge()
        return before - len(self._data)
