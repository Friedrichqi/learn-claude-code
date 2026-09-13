"""LRU cache with per-entry time-to-live (TTL).

See README.md in this directory for the full specification.
"""

import time
from collections import OrderedDict


class TTLCache:
    """A fixed-capacity LRU cache whose entries may carry a TTL."""

    def __init__(self, capacity, clock=time.monotonic):
        if not isinstance(capacity, int) or isinstance(capacity, bool):
            raise ValueError("capacity must be an integer >= 1")
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        if not callable(clock):
            raise ValueError("clock must be a zero-argument callable")
        self._capacity = capacity
        self._clock = clock
        # Ordered least-recently-used -> most-recently-used.
        # Maps key -> (value, expires) where expires is None (never) or an
        # absolute deadline in seconds returned by the clock.
        self._entries = OrderedDict()

    # ------------------------------------------------------------------ #
    # internal helpers
    # ------------------------------------------------------------------ #
    def _is_expired(self, entry):
        expires = entry[1]
        return expires is not None and self._clock() >= expires

    def _purge_expired(self):
        """Drop every expired entry; return how many were removed."""
        expired = [
            key
            for key, entry in self._entries.items()
            if self._is_expired(entry)
        ]
        for key in expired:
            del self._entries[key]
        return len(expired)

    # ------------------------------------------------------------------ #
    # public API
    # ------------------------------------------------------------------ #
    def put(self, key, value, ttl=None):
        if ttl is not None and ttl <= 0:
            raise ValueError("ttl must be positive")
        expires = None if ttl is None else self._clock() + ttl

        if key in self._entries:
            # Re-storing an existing key refreshes value, lifetime and recency.
            self._entries[key] = (value, expires)
            self._entries.move_to_end(key)
            return

        if len(self._entries) >= self._capacity:
            # Make room for a new key: expired entries go first.
            self._purge_expired()
        if len(self._entries) >= self._capacity:
            # Still full: evict the least recently used entry.
            self._entries.popitem(last=False)
        self._entries[key] = (value, expires)

    def get(self, key, default=None):
        entry = self._entries.get(key)
        if entry is None:
            return default
        if self._is_expired(entry):
            del self._entries[key]
            return default
        self._entries.move_to_end(key)
        return entry[0]

    def __contains__(self, key):
        entry = self._entries.get(key)
        return entry is not None and not self._is_expired(entry)

    def __len__(self):
        self._purge_expired()
        return len(self._entries)

    def keys(self):
        self._purge_expired()
        return list(self._entries)

    def evict_expired(self):
        return self._purge_expired()
