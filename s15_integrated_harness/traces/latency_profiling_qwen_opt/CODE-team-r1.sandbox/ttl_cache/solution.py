"""LRU cache with per-entry time-to-live."""

from collections import OrderedDict


class TTLCache:
    def __init__(self, capacity, clock=None):
        if not isinstance(capacity, int) or capacity < 1:
            raise ValueError("capacity must be >= 1")
        if clock is None:
            import time
            clock = time.monotonic
        self._capacity = capacity
        self._clock = clock
        self._entries = OrderedDict()  # key -> (value, expiry); expiry is None if no ttl

    def _is_expired(self, expiry):
        return expiry is not None and self._clock() >= expiry

    def _purge_expired(self):
        now = self._clock()
        expired = [k for k, (_, expiry) in self._entries.items()
                   if expiry is not None and now >= expiry]
        for k in expired:
            del self._entries[k]
        return len(expired)

    def put(self, key, value, ttl=None):
        if ttl is not None and ttl <= 0:
            raise ValueError("ttl must be > 0")
        if key in self._entries:
            del self._entries[key]
        elif len(self._entries) >= self._capacity:
            removed = self._purge_expired()
            if len(self._entries) >= self._capacity:
                self._entries.popitem(last=False)
        expiry = None if ttl is None else self._clock() + ttl
        self._entries[key] = (value, expiry)

    def get(self, key, default=None):
        if key not in self._entries:
            return default
        value, expiry = self._entries[key]
        if self._is_expired(expiry):
            del self._entries[key]
            return default
        self._entries.move_to_end(key)
        return value

    def __contains__(self, key):
        if key not in self._entries:
            return False
        return not self._is_expired(self._entries[key][1])

    def __len__(self):
        self._purge_expired()
        return len(self._entries)

    def keys(self):
        self._purge_expired()
        return list(self._entries.keys())

    def evict_expired(self):
        return self._purge_expired()
