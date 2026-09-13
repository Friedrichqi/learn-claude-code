"""LRU cache with per-entry time-to-live (TTL).

See README.md for the specification.
"""

import time
from collections import OrderedDict


class TTLCache:
    """A fixed-capacity LRU cache where each entry may have its own TTL."""

    def __init__(self, capacity, clock=time.monotonic):
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        if not callable(clock):
            raise TypeError("clock must be a zero-argument callable")
        self._capacity = capacity
        self._clock = clock
        # Ordered least-recently-used first: key -> (value, expires_at or None).
        self._entries = OrderedDict()

    # ------------------------------------------------------------------ #
    # internal helpers
    # ------------------------------------------------------------------ #
    def _expired(self, expires_at):
        """An entry stored at t with lifetime ttl expires when clock() >= t + ttl."""
        return expires_at is not None and self._clock() >= expires_at

    def _purge_expired(self):
        now = self._clock()
        dead = [
            key
            for key, (_, expires_at) in self._entries.items()
            if expires_at is not None and now >= expires_at
        ]
        for key in dead:
            del self._entries[key]
        return len(dead)

    # ------------------------------------------------------------------ #
    # public API
    # ------------------------------------------------------------------ #
    def put(self, key, value, ttl=None):
        if ttl is not None and ttl <= 0:
            raise ValueError("ttl must be greater than 0")
        expires_at = None if ttl is None else self._clock() + ttl

        entry = self._entries.get(key)
        if entry is not None:
            # Existing key: replace value, restart lifetime, become most recent.
            # An already-expired entry is treated as absent (a new insertion).
            if self._expired(entry[1]):
                del self._entries[key]
            else:
                self._entries[key] = (value, expires_at)
                self._entries.move_to_end(key)
                return

        if len(self._entries) >= self._capacity:
            # A new key would exceed capacity: drop expired entries first,
            # then fall back to evicting the least recently used entries.
            self._purge_expired()
            while len(self._entries) >= self._capacity:
                self._entries.popitem(last=False)

        self._entries[key] = (value, expires_at)

    def get(self, key, default=None):
        entry = self._entries.get(key)
        if entry is None:
            return default
        if self._expired(entry[1]):
            del self._entries[key]
            return default
        self._entries.move_to_end(key)
        return entry[0]

    def __contains__(self, key):
        entry = self._entries.get(key)
        if entry is None:
            return False
        if self._expired(entry[1]):
            del self._entries[key]
            return False
        return True  # membership must not refresh recency

    def __len__(self):
        self._purge_expired()
        return len(self._entries)

    def keys(self):
        self._purge_expired()
        return list(self._entries.keys())

    def evict_expired(self):
        return self._purge_expired()
