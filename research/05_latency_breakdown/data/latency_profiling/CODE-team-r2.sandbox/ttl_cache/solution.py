"""LRU cache with per-entry time-to-live (TTL)."""

from collections import OrderedDict
import time


class TTLCache:
    """A least-recently-used cache whose entries may carry a lifetime.

    Entries stored without a TTL never expire. An entry stored at time ``t``
    with lifetime ``ttl`` is expired once ``clock() >= t + ttl``.
    """

    def __init__(self, capacity, clock=time.monotonic):
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        self._capacity = capacity
        self._clock = clock
        # Ordered least-recently-used -> most-recently-used.
        # Maps key -> (value, expires_at) where expires_at is None for no expiry.
        self._entries = OrderedDict()

    # -- internal helpers -------------------------------------------------

    def _expired(self, expires_at, now=None):
        return expires_at is not None and (self._clock() if now is None else now) >= expires_at

    def _purge_expired(self):
        now = self._clock()
        expired = [
            key
            for key, (_, expires_at) in self._entries.items()
            if self._expired(expires_at, now)
        ]
        for key in expired:
            del self._entries[key]
        return len(expired)

    # -- public API -------------------------------------------------------

    def put(self, key, value, ttl=None):
        if ttl is not None and ttl <= 0:
            raise ValueError("ttl must be positive")
        expires_at = None if ttl is None else self._clock() + ttl
        if key in self._entries:
            # Replacing an existing key: restart its lifetime and recency.
            del self._entries[key]
        elif len(self._entries) >= self._capacity:
            # New key over capacity: drop expired entries first, then LRU.
            self._purge_expired()
            while len(self._entries) >= self._capacity:
                self._entries.popitem(last=False)
        self._entries[key] = (value, expires_at)

    def get(self, key, default=None):
        entry = self._entries.get(key)
        if entry is None:
            return default
        value, expires_at = entry
        if self._expired(expires_at):
            del self._entries[key]
            return default
        self._entries.move_to_end(key)
        return value

    def __contains__(self, key):
        entry = self._entries.get(key)
        return entry is not None and not self._expired(entry[1])

    def __len__(self):
        self._purge_expired()
        return len(self._entries)

    def keys(self):
        self._purge_expired()
        return list(self._entries)

    def evict_expired(self):
        return self._purge_expired()
