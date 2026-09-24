import time
from collections import OrderedDict


class TTLCache:
    def __init__(self, capacity, clock=time.monotonic):
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self.capacity = capacity
        self.clock = clock
        self._store = OrderedDict()  # key -> (value, expiry or None)

    def _purge(self, now=None):
        if now is None:
            now = self.clock()
        dead = [k for k, (_, exp) in self._store.items() if exp is not None and now >= exp]
        for k in dead:
            del self._store[k]
        return len(dead)

    def put(self, key, value, ttl=None):
        if ttl is not None and ttl <= 0:
            raise ValueError("ttl must be positive")
        now = self.clock()
        if key in self._store:
            self._store.move_to_end(key)
            self._store[key] = (value, now + ttl if ttl is not None else None)
            return
        if len(self._store) >= self.capacity:
            self._purge(now)
            if len(self._store) >= self.capacity:
                self._store.popitem(last=False)
        self._store[key] = (value, now + ttl if ttl is not None else None)

    def get(self, key, default=None):
        if key not in self._store:
            return default
        value, exp = self._store[key]
        if exp is not None and self.clock() >= exp:
            del self._store[key]
            return default
        self._store.move_to_end(key)
        return value

    def __contains__(self, key):
        if key not in self._store:
            return False
        _, exp = self._store[key]
        if exp is not None and self.clock() >= exp:
            del self._store[key]
            return False
        return True

    def __len__(self):
        self._purge()
        return len(self._store)

    def keys(self):
        self._purge()
        return list(self._store.keys())

    def evict_expired(self):
        return self._purge()
