"""LRU cache with per-entry time-to-live."""

import time
from collections import OrderedDict


class TTLCache:
    def __init__(self, capacity, clock=time.monotonic):
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self._capacity = int(capacity)
        self._clock = clock
        # key -> (value, expires); expires is None or an absolute timestamp.
        # OrderedDict order is recency: first = LRU, last = MRU.
        self._store = OrderedDict()

    # ------------------------------------------------------------------
    def _is_live(self, expires):
        if expires is None:
            return True
        return self._clock() < expires

    def _purge_expired(self):
        now = self._clock()
        dead = [
            key
            for key, (_value, expires) in self._store.items()
            if expires is not None and now >= expires
        ]
        for key in dead:
            del self._store[key]
        return len(dead)

    def put(self, key, value, ttl=None):
        if ttl is not None and ttl <= 0:
            raise ValueError("ttl must be positive")
        now = self._clock()
        expires = None if ttl is None else now + ttl
        if key in self._store:
            # Replace value, restart lifetime, mark most recently used.
            self._store[key] = (value, expires)
            self._store.move_to_end(key)
        else:
            if len(self._store) >= self._capacity:
                self._purge_expired()
            if len(self._store) >= self._capacity:
                # Still full: evict least recently used live entry.
                self._store.popitem(last=False)
            self._store[key] = (value, expires)

    def get(self, key, default=None):
        entry = self._store.get(key)
        if entry is None:
            return default
        value, expires = entry
        if not self._is_live(expires):
            del self._store[key]
            return default
        self._store.move_to_end(key)
        return value

    def evict_expired(self):
        return self._purge_expired()

    # ------------------------------------------------------------------
    def __contains__(self, key):
        entry = self._store.get(key)
        if entry is None:
            return False
        # Must not change recency.
        return self._is_live(entry[1])

    def __len__(self):
        # Reporting the live count also purges stale entries.
        self._purge_expired()
        return len(self._store)

    def keys(self):
        return [key for key, (_value, expires) in self._store.items() if self._is_live(expires)]
