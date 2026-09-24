"""LRU cache with per-entry time-to-live."""

from collections import OrderedDict


class TTLCache:
    def __init__(self, capacity, clock=None):
        if capacity is None or capacity < 1:
            raise ValueError("capacity must be >= 1")
        self._capacity = int(capacity)
        self._clock = clock if clock is not None else __import__("time").monotonic
        # key -> (value, expiry) ; expiry is None for entries that never expire
        self._data = OrderedDict()

    def _is_expired(self, expiry, now):
        return expiry is not None and now >= expiry

    def _purge_expired(self):
        now = self._clock()
        dead = [k for k, (_v, exp) in self._data.items() if self._is_expired(exp, now)]
        for k in dead:
            del self._data[k]
        return len(dead)

    def put(self, key, value, ttl=None):
        if ttl is not None and ttl <= 0:
            raise ValueError("ttl must be > 0")
        now = self._clock()
        if key in self._data:
            del self._data[key]
        else:
            if len(self._data) >= self._capacity:
                self._purge_expired()
                if len(self._data) >= self._capacity:
                    self._data.popitem(last=False)
        expiry = None if ttl is None else now + ttl
        self._data[key] = (value, expiry)

    def get(self, key, default=None):
        if key not in self._data:
            return default
        now = self._clock()
        value, expiry = self._data[key]
        if self._is_expired(expiry, now):
            del self._data[key]
            return default
        self._data.move_to_end(key)
        return value

    def __contains__(self, key):
        if key not in self._data:
            return False
        _value, expiry = self._data[key]
        if self._is_expired(expiry, self._clock()):
            del self._data[key]
            return False
        return True

    def __len__(self):
        self._purge_expired()
        return len(self._data)

    def keys(self):
        now = self._clock()
        dead = [k for k, (_v, exp) in self._data.items() if self._is_expired(exp, now)]
        for k in dead:
            del self._data[k]
        return list(self._data.keys())

    def evict_expired(self):
        return self._purge_expired()
