"""LRU cache with per-entry time-to-live."""

import time
from collections import OrderedDict


class TTLCache:
    """An LRU cache whose entries can each carry their own lifetime.

    Parameters
    ----------
    capacity:
        Maximum number of live entries. Must be >= 1.
    clock:
        Zero-argument callable returning the current time in seconds.
    """

    def __init__(self, capacity, clock=time.monotonic):
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self._capacity = capacity
        self._clock = clock
        # key -> [value, expiry]; expiry is None for entries that never expire.
        self._entries = OrderedDict()

    @staticmethod
    def _is_expired(expiry, now):
        return expiry is not None and now >= expiry

    def _purge_expired(self, now):
        stale = [key for key, (_, expiry) in self._entries.items()
                 if self._is_expired(expiry, now)]
        for key in stale:
            del self._entries[key]
        return len(stale)

    def put(self, key, value, ttl=None):
        now = self._clock()
        if ttl is not None:
            if ttl <= 0:
                raise ValueError("ttl must be positive")
            expiry = now + ttl
        else:
            expiry = None

        entries = self._entries
        if key in entries:
            # Replace value, restart lifetime, make most recently used.
            entries[key] = [value, expiry]
            entries.move_to_end(key)
            return

        if len(entries) >= self._capacity:
            self._purge_expired(now)
            if len(entries) >= self._capacity:
                entries.popitem(last=False)  # evict least recently used
        entries[key] = [value, expiry]

    def get(self, key, default=None):
        entry = self._entries.get(key)
        if entry is None:
            return default
        value, expiry = entry
        if self._is_expired(expiry, self._clock()):
            del self._entries[key]
            return default
        self._entries.move_to_end(key)
        return value

    def __contains__(self, key):
        entry = self._entries.get(key)
        if entry is None:
            return False
        _, expiry = entry
        if self._is_expired(expiry, self._clock()):
            del self._entries[key]
            return False
        # Intentionally do NOT move_to_end: membership must not change recency.
        return True

    def __len__(self):
        self._purge_expired(self._clock())
        return len(self._entries)

    def keys(self):
        self._purge_expired(self._clock())
        return list(self._entries.keys())

    def evict_expired(self):
        return self._purge_expired(self._clock())
