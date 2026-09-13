import time
from collections import OrderedDict

class TTLCache:
    def __init__(self, capacity, clock=time.monotonic):
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self.capacity = capacity
        self.clock = clock
        self._data = OrderedDict()  # key -> (value, expires_at or None)

    def _expired(self, key, now=None):
        now = self.clock() if now is None else now
        exp = self._data[key][1]
        return exp is not None and now >= exp

    def evict_expired(self):
        now = self.clock()
        dead = [k for k in self._data if self._expired(k, now)]
        for k in dead:
            del self._data[k]
        return len(dead)

    def put(self, key, value, ttl=None):
        if ttl is not None and ttl <= 0:
            raise ValueError("ttl must be positive")
        exp = None if ttl is None else self.clock() + ttl
        if key in self._data:
            del self._data[key]
        else:
            if len(self._data) >= self.capacity:
                self.evict_expired()
            while len(self._data) >= self.capacity:
                self._data.popitem(last=False)
        self._data[key] = (value, exp)

    def get(self, key, default=None):
        if key not in self._data:
            return default
        if self._expired(key):
            del self._data[key]
            return default
        self._data.move_to_end(key)
        return self._data[key][0]

    def __contains__(self, key):
        if key not in self._data:
            return False
        if self._expired(key):
            del self._data[key]
            return False
        return True

    def __len__(self):
        self.evict_expired()
        return len(self._data)

    def keys(self):
        self.evict_expired()
        return list(self._data.keys())
