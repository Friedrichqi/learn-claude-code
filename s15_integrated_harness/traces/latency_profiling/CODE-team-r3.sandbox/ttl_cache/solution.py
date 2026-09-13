"""LRU cache with per-entry time-to-live (TTL)."""

import time
from collections import OrderedDict


class TTLCache:
    """Least-recently-used cache where each entry may carry its own TTL.

    Entries with ``ttl=None`` never expire.  An entry stored at time ``t``
    with lifetime ``ttl`` is expired once ``clock() >= t + ttl``.
    """

    def __init__(self, capacity, clock=time.monotonic):
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        self._capacity = capacity
        self._clock = clock
        # key -> (value, expires_at) ; expires_at is None for immortal entries
        self._entries = OrderedDict()

    # -- internal helpers -------------------------------------------------

    def _expired_keys(self, now):
        return [
            key
            for key, (_, expires_at) in self._entries.items()
            if expires_at is not None and now >= expires_at
        ]

    def _purge_expired(self, now):
        for key in self._expired_keys(now):
            del self._entries[key]

    # -- public API -------------------------------------------------------

    def put(self, key, value, ttl=None):
        if ttl is not None and ttl <= 0:
            raise ValueError("ttl must be greater than 0")
        now = self._clock()
        expires_at = None if ttl is None else now + ttl
        if key in self._entries:
            del self._entries[key]
        else:
            # A *new* key may need room: first drop expired entries, then
            # evict from the least recently used end.
            if len(self._entries) >= self._capacity:
                self._purge_expired(now)
            while len(self._entries) >= self._capacity:
                self._entries.popitem(last=False)
        self._entries[key] = (value, expires_at)

    def get(self, key, default=None):
        entry = self._entries.get(key)
        if entry is None:
            return default
        value, expires_at = entry
        if expires_at is not None and self._clock() >= expires_at:
            del self._entries[key]
            return default
        self._entries.move_to_end(key)
        return value

    def __contains__(self, key):
        entry = self._entries.get(key)
        if entry is None:
            return False
        expires_at = entry[1]
        return not (expires_at is not None and self._clock() >= expires_at)

    def __len__(self):
        self._purge_expired(self._clock())
        return len(self._entries)

    def keys(self):
        self._purge_expired(self._clock())
        return list(self._entries)

    def evict_expired(self):
        now = self._clock()
        expired = self._expired_keys(now)
        for key in expired:
            del self._entries[key]
        return len(expired)
