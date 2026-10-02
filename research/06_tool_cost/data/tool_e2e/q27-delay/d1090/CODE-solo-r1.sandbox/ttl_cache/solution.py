"""LRU cache with per-entry time-to-live."""

import time
from collections import OrderedDict


class TTLCache:
    """An LRU cache where each entry can carry a lifetime in seconds.

    Entries are stored in an OrderedDict keyed by recency: the first item
    is the least recently used, the last item the most recently used.
    Each value is stored as a (value, expires_at) pair; expires_at is None
    for entries that never expire. An entry stored at time t with lifetime
    ttl is expired when clock() >= t + ttl.
    """

    def __init__(self, capacity, clock=time.monotonic):
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        self._capacity = capacity
        self._clock = clock
        self._entries = OrderedDict()

    def _purge(self):
        """Remove every expired entry; return how many were removed."""
        now = self._clock()
        dead = [
            key
            for key, (_, expires_at) in self._entries.items()
            if expires_at is not None and now >= expires_at
        ]
        for key in dead:
            del self._entries[key]
        return len(dead)

    def put(self, key, value, ttl=None):
        if ttl is not None and ttl <= 0:
            raise ValueError("ttl must be a positive number of seconds")
        self._purge()
        now = self._clock()
        expires_at = now + ttl if ttl is not None else None
        if key in self._entries:
            # Replace value, restart lifetime, mark most recently used.
            self._entries[key] = (value, expires_at)
            self._entries.move_to_end(key)
        else:
            if len(self._entries) >= self._capacity:
                # Expired entries were already dropped; evict LRU.
                self._entries.popitem(last=False)
            self._entries[key] = (value, expires_at)

    def get(self, key, default=None):
        self._purge()
        if key not in self._entries:
            return default
        value, _ = self._entries[key]
        self._entries.move_to_end(key)
        return value

    def __contains__(self, key):
        self._purge()
        return key in self._entries

    def __len__(self):
        self._purge()
        return len(self._entries)

    def keys(self):
        """Live keys from least recently used to most recently used."""
        self._purge()
        return list(self._entries)

    def evict_expired(self):
        """Remove all expired entries and return how many were removed."""
        return self._purge()
