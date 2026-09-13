"""LRU cache with per-entry time-to-live."""

import collections
import time


class TTLCache:
    """LRU cache where each entry has an optional lifetime."""

    def __init__(self, capacity, clock=time.monotonic):
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self._capacity = capacity
        self._clock = clock
        self._data = collections.OrderedDict()  # key -> (value, expires_at or None)

    def _expired(self, expires_at, now=None):
        if expires_at is None:
            return False
        if now is None:
            now = self._clock()
        return now >= expires_at

    def put(self, key, value, ttl=None):
        if ttl is not None and ttl <= 0:
            raise ValueError("ttl must be positive")
        expires_at = None if ttl is None else self._clock() + ttl
        if key in self._data:
            self._data[key] = (value, expires_at)
            self._data.move_to_end(key)
            return
        if len(self._data) + 1 > self._capacity:
            self.evict_expired()
        if len(self._data) + 1 > self._capacity:
            self._data.popitem(last=False)
        self._data[key] = (value, expires_at)

    def get(self, key, default=None):
        entry = self._data.get(key)
        if entry is None:
            return default
        value, expires_at = entry
        if self._expired(expires_at):
            del self._data[key]
            return default
        self._data.move_to_end(key)
        return value

    def __contains__(self, key):
        entry = self._data.get(key)
        return entry is not None and not self._expired(entry[1])

    def _purge_expired(self):
        now = self._clock()
        dead = [k for k, (_v, exp) in self._data.items() if self._expired(exp, now)]
        for k in dead:
            del self._data[k]
        return len(dead)

    def __len__(self):
        self._purge_expired()
        return len(self._data)

    def keys(self):
        self._purge_expired()
        return list(self._data.keys())

    def evict_expired(self):
        return self._purge_expired()
