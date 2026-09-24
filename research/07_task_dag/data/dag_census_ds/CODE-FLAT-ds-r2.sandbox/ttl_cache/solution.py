"""An LRU cache with per-entry time-to-live, per the README specification."""

import time
from collections import OrderedDict


class TTLCache:
    """LRU cache whose entries may expire after a per-entry lifetime."""

    def __init__(self, capacity, clock=time.monotonic):
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        self.capacity = capacity
        self.clock = clock
        # key -> [value, expiry]; expiry is None for entries that never expire.
        # Order runs from least recently used to most recently used.
        self._data = OrderedDict()

    def _expired(self, expiry, now):
        return expiry is not None and now >= expiry

    def _purge(self):
        """Remove expired entries, returning how many were removed."""
        now = self.clock()
        stale = [k for k, (_, e) in self._data.items() if self._expired(e, now)]
        for key in stale:
            del self._data[key]
        return len(stale)

    def put(self, key, value, ttl=None):
        if ttl is not None and ttl <= 0:
            raise ValueError("ttl must be positive or None")
        expiry = None if ttl is None else self.clock() + ttl

        if key in self._data:
            self._data[key] = [value, expiry]
            self._data.move_to_end(key)
            return

        if len(self._data) >= self.capacity:
            self._purge()
            if len(self._data) >= self.capacity:
                self._data.popitem(last=False)
        self._data[key] = [value, expiry]

    def get(self, key, default=None):
        if key not in self._data:
            return default
        value, expiry = self._data[key]
        if self._expired(expiry, self.clock()):
            del self._data[key]
            return default
        self._data.move_to_end(key)
        return value

    def __contains__(self, key):
        if key not in self._data:
            return False
        _, expiry = self._data[key]
        return not self._expired(expiry, self.clock())

    def __len__(self):
        self._purge()
        return len(self._data)

    def keys(self):
        self._purge()
        return list(self._data.keys())

    def evict_expired(self):
        return self._purge()
