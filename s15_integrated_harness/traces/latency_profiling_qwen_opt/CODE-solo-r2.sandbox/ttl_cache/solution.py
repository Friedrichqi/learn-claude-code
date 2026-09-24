import time
from collections import OrderedDict


class TTLCache:
    def __init__(self, capacity, clock=time.monotonic):
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self.capacity = capacity
        self.clock = clock
        self._entries = OrderedDict()  # key -> (value, deadline) where deadline is None if immortal

    def _expired(self, deadline):
        return deadline is not None and self.clock() >= deadline

    def _purge_expired(self):
        now = self.clock()
        dead = [
            k
            for k, (_, d) in self._entries.items()
            if d is not None and now >= d
        ]
        for k in dead:
            del self._entries[k]
        return len(dead)

    def put(self, key, value, ttl=None):
        if ttl is not None and ttl <= 0:
            raise ValueError("ttl must be positive")
        if key in self._entries and not self._expired(self._entries[key][1]):
            # replace existing live entry
            del self._entries[key]
        elif key in self._entries:
            del self._entries[key]
        else:
            if len(self._entries) >= self.capacity:
                self._purge_expired()
                if len(self._entries) >= self.capacity:
                    self._entries.popitem(last=False)
        deadline = None if ttl is None else self.clock() + ttl
        self._entries[key] = (value, deadline)

    def get(self, key, default=None):
        if key not in self._entries:
            return default
        value, deadline = self._entries[key]
        if self._expired(deadline):
            del self._entries[key]
            return default
        self._entries.move_to_end(key)
        return value

    def __contains__(self, key):
        if key not in self._entries:
            return False
        return not self._expired(self._entries[key][1])

    def __len__(self):
        self._purge_expired()
        return len(self._entries)

    def keys(self):
        self._purge_expired()
        return list(self._entries.keys())

    def evict_expired(self):
        return self._purge_expired()
