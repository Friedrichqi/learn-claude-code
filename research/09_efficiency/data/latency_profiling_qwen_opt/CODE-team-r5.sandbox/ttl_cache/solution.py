"""LRU cache with per-entry time-to-live."""

from collections import OrderedDict


class TTLCache:
    def __init__(self, capacity, clock=None):
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        if clock is None:
            import time
            clock = time.monotonic
        self._capacity = capacity
        self._clock = clock
        self._data = OrderedDict()  # key -> (value, expires_at or None)

    def _is_expired(self, expires_at):
        return expires_at is not None and self._clock() >= expires_at

    def _purge_expired(self):
        removed = 0
        for key in list(self._data):
            if self._is_expired(self._data[key][1]):
                del self._data[key]
                removed += 1
        return removed

    def put(self, key, value, ttl=None):
        if ttl is not None and ttl <= 0:
            raise ValueError("ttl must be > 0 or None")
        if key in self._data:
            del self._data[key]
        elif len(self._data) >= self._capacity:
            self._purge_expired()
            if len(self._data) >= self._capacity:
                self._data.popitem(last=False)
        now = self._clock()
        expires_at = None if ttl is None else now + ttl
        self._data[key] = (value, expires_at)

    def get(self, key, default=None):
        entry = self._data.get(key)
        if entry is None or self._is_expired(entry[1]):
            if key in self._data:
                del self._data[key]
            return default
        self._data.move_to_end(key)
        return entry[0]

    def __contains__(self, key):
        entry = self._data.get(key)
        if entry is None:
            return False
        if self._is_expired(entry[1]):
            del self._data[key]
            return False
        return True

    def __len__(self):
        self._purge_expired()
        return len(self._data)

    def keys(self):
        self._purge_expired()
        return list(self._data)

    def evict_expired(self):
        return self._purge_expired()
