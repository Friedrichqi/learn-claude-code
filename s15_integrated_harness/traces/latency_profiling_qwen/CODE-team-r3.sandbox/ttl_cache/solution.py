"""LRU cache with per-entry time-to-live."""

import time
from collections import OrderedDict


class TTLCache:
    """An LRU cache in which each entry may carry its own time-to-live.

    Expiry rule: an entry stored at time ``t`` with lifetime ``ttl`` is
    expired when ``clock() >= t + ttl``; entries with ``ttl=None`` never
    expire.
    """

    def __init__(self, capacity, clock=time.monotonic):
        if capacity is None or capacity < 1:
            raise ValueError("capacity must be >= 1")
        if not callable(clock):
            raise TypeError("clock must be a zero-argument callable")
        self._capacity = capacity
        self._clock = clock
        # key -> (value, deadline); deadline is None for never-expiring entries.
        # OrderedDict iteration order goes from least to most recently used.
        self._data = OrderedDict()

    # ------------------------------------------------------------------ #
    # internal helpers
    # ------------------------------------------------------------------ #

    def _deadline(self, ttl, now):
        return None if ttl is None else now + ttl

    def _is_expired(self, deadline, now):
        return deadline is not None and now >= deadline

    def _purge_expired(self):
        """Remove every expired entry; return how many were removed."""
        now = self._clock()
        expired = [
            key
            for key, (_, deadline) in self._data.items()
            if self._is_expired(deadline, now)
        ]
        for key in expired:
            del self._data[key]
        return len(expired)

    # ------------------------------------------------------------------ #
    # public API
    # ------------------------------------------------------------------ #

    def put(self, key, value, ttl=None):
        if ttl is not None and ttl <= 0:
            raise ValueError("ttl must be > 0")
        now = self._clock()
        deadline = self._deadline(ttl, now)

        if key in self._data:
            # Replace value, restart lifetime, mark most recently used.
            self._data[key] = (value, deadline)
            self._data.move_to_end(key)
            return

        if len(self._data) >= self._capacity:
            # A new key would exceed capacity: drop expired entries first,
            # then fall back to evicting the least recently used one.
            self._purge_expired()
            if len(self._data) >= self._capacity:
                self._data.popitem(last=False)
        self._data[key] = (value, deadline)

    def get(self, key, default=None):
        entry = self._data.get(key)
        if entry is None:
            return default
        value, deadline = entry
        if self._is_expired(deadline, self._clock()):
            del self._data[key]
            return default
        self._data.move_to_end(key)
        return value

    def keys(self):
        self._purge_expired()
        return list(self._data.keys())

    def evict_expired(self):
        return self._purge_expired()

    def __contains__(self, key):
        entry = self._data.get(key)
        if entry is None:
            return False
        _, deadline = entry
        # Must not change recency.
        return not self._is_expired(deadline, self._clock())

    def __len__(self):
        self._purge_expired()
        return len(self._data)
