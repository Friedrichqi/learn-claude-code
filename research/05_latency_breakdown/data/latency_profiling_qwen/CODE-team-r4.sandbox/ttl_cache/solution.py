"""LRU cache with per-entry time-to-live."""

import time
from collections import OrderedDict


class TTLCache:
    """LRU cache where each entry may carry a time-to-live.

    Entries are stored in insertion/usage order (least recently used first).
    An entry stored at time ``t`` with lifetime ``ttl`` is expired when
    ``clock() >= t + ttl``; entries with ``ttl=None`` never expire.
    """

    def __init__(self, capacity, clock=time.monotonic):
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        self._capacity = capacity
        self._clock = clock
        # key -> (value, expiry); expiry is None for entries that never expire
        self._data = OrderedDict()

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------
    def _is_expired(self, expiry, now):
        return expiry is not None and now >= expiry

    def _purge_expired(self):
        """Remove every expired entry; return the number removed."""
        now = self._clock()
        removed = 0
        for key in [k for k, (_, expiry) in self._data.items()
                    if self._is_expired(expiry, now)]:
            del self._data[key]
            removed += 1
        return removed

    def _expiry_for(self, ttl):
        if ttl is None:
            return None
        return self._clock() + ttl

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def put(self, key, value, ttl=None):
        if ttl is not None and ttl <= 0:
            raise ValueError("ttl must be positive")
        if key in self._data:
            # Replace value, restart lifetime, make most recently used.
            self._data[key] = (value, self._expiry_for(ttl))
            self._data.move_to_end(key)
            return
        if len(self._data) >= self._capacity:
            self._purge_expired()
            if len(self._data) >= self._capacity:
                # Still full: evict the least recently used entry.
                self._data.popitem(last=False)
        self._data[key] = (value, self._expiry_for(ttl))

    def get(self, key, default=None):
        entry = self._data.get(key)
        if entry is None:
            return default
        value, expiry = entry
        if self._is_expired(expiry, self._clock()):
            del self._data[key]
            return default
        self._data.move_to_end(key)
        return value

    def __contains__(self, key):
        entry = self._data.get(key)
        if entry is None:
            return False
        _, expiry = entry
        if self._is_expired(expiry, self._clock()):
            del self._data[key]
            return False
        return True

    def __len__(self):
        self._purge_expired()
        return len(self._data)

    def keys(self):
        self._purge_expired()
        return list(self._data.keys())

    def evict_expired(self):
        return self._purge_expired()
