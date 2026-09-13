"""LRU cache with per-entry time-to-live."""

from collections import OrderedDict
from time import monotonic


class TTLCache:
    def __init__(self, capacity, clock=monotonic):
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self._capacity = capacity
        self._clock = clock
        self._data = OrderedDict()  # key -> [value, expires_at or None]

    # -- internals -----------------------------------------------------
    def _expired(self, record):
        expires_at = record[1]
        return expires_at is not None and self._clock() >= expires_at

    def _purge_expired(self):
        now = self._clock()
        expired = [
            key
            for key, (_, expires_at) in self._data.items()
            if expires_at is not None and now >= expires_at
        ]
        for key in expired:
            del self._data[key]
        return len(expired)

    # -- public API ----------------------------------------------------
    def put(self, key, value, ttl=None):
        if ttl is not None and ttl <= 0:
            raise ValueError("ttl must be positive")
        expires_at = None if ttl is None else self._clock() + ttl
        record = self._data.get(key)
        if record is not None and self._expired(record):
            del self._data[key]
            record = None
        if record is None and len(self._data) >= self._capacity:
            self._purge_expired()
            while len(self._data) >= self._capacity:
                self._data.popitem(last=False)
        self._data[key] = [value, expires_at]
        self._data.move_to_end(key)

    def get(self, key, default=None):
        record = self._data.get(key)
        if record is None:
            return default
        if self._expired(record):
            del self._data[key]
            return default
        self._data.move_to_end(key)
        return record[0]

    def __contains__(self, key):
        record = self._data.get(key)
        return record is not None and not self._expired(record)

    def __len__(self):
        self._purge_expired()
        return len(self._data)

    def keys(self):
        self._purge_expired()
        return list(self._data.keys())

    def evict_expired(self):
        return self._purge_expired()
