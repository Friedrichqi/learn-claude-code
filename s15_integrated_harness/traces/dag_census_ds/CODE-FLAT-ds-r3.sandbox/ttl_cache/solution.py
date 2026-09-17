"""LRU cache with per-entry time-to-live."""

import time
from collections import OrderedDict


class TTLCache:
    """A fixed-capacity LRU cache where each entry may expire after a TTL."""

    def __init__(self, capacity, clock=time.monotonic):
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self._capacity = capacity
        self._clock = clock
        # key -> [value, expiry] where expiry is None for entries that never expire
        self._data = OrderedDict()

    def _is_expired(self, expiry):
        if expiry is None:
            return False
        return self._clock() >= expiry

    def _purge_expired(self):
        expired = [k for k, (_, exp) in self._data.items() if self._is_expired(exp)]
        for key in expired:
            del self._data[key]
        return len(expired)

    def put(self, key, value, ttl=None):
        if ttl is not None and ttl <= 0:
            raise ValueError("ttl must be positive or None")
        expiry = None if ttl is None else self._clock() + ttl
        if key in self._data:
            del self._data[key]
        else:
            if len(self._data) >= self._capacity:
                self._purge_expired()
            if len(self._data) >= self._capacity:
                # still full: evict the least recently used entry
                self._data.popitem(last=False)
        self._data[key] = (value, expiry)

    def get(self, key, default=None):
        try:
            value, expiry = self._data[key]
        except KeyError:
            return default
        if self._is_expired(expiry):
            del self._data[key]
            return default
        self._data.move_to_end(key)
        return value

    def __contains__(self, key):
        try:
            _, expiry = self._data[key]
        except KeyError:
            return False
        if self._is_expired(expiry):
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
