"""LRU cache with per-entry time-to-live."""

import time
from collections import OrderedDict


class TTLCache:
    def __init__(self, capacity, clock=time.monotonic):
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self._capacity = int(capacity)
        self._clock = clock
        # key -> [value, deadline]; deadline is None for entries that never expire.
        self._entries = OrderedDict()

    def _is_expired(self, deadline, now=None):
        if deadline is None:
            return False
        if now is None:
            now = self._clock()
        return now >= deadline

    def _purge_expired(self, now=None):
        if now is None:
            now = self._clock()
        removed = [k for k, (_, deadline) in self._entries.items()
                   if self._is_expired(deadline, now)]
        for key in removed:
            del self._entries[key]
        return len(removed)

    def put(self, key, value, ttl=None):
        if ttl is not None and ttl <= 0:
            raise ValueError("ttl must be positive")
        now = self._clock()
        deadline = None if ttl is None else now + ttl
        if key in self._entries:
            self._entries[key] = [value, deadline]
            self._entries.move_to_end(key)
            return
        if len(self._entries) >= self._capacity:
            self._purge_expired(now)
            if len(self._entries) >= self._capacity:
                self._entries.popitem(last=False)
        self._entries[key] = [value, deadline]

    def get(self, key, default=None):
        if key in self._entries:
            value, deadline = self._entries[key]
            if self._is_expired(deadline):
                del self._entries[key]
                return default
            self._entries.move_to_end(key)
            return value
        return default

    def __contains__(self, key):
        if key not in self._entries:
            return False
        _, deadline = self._entries[key]
        return not self._is_expired(deadline)

    def __len__(self):
        self._purge_expired()
        return len(self._entries)

    def keys(self):
        self._purge_expired()
        return list(self._entries.keys())

    def evict_expired(self):
        return self._purge_expired()
