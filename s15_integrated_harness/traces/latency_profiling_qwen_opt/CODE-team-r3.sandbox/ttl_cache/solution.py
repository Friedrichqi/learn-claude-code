import time
from collections import OrderedDict


class TTLCache:
    def __init__(self, capacity, clock=None):
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self.capacity = capacity
        self.clock = time.monotonic if clock is None else clock
        # key -> (value, expires_at or None); LRU order, least recent first
        self._entries = OrderedDict()

    def _is_expired(self, expires_at):
        return expires_at is not None and self.clock() >= expires_at

    def _purge_expired(self):
        for key in list(self._entries):
            if self._is_expired(self._entries[key][1]):
                del self._entries[key]

    def put(self, key, value, ttl=None):
        if ttl is not None and ttl <= 0:
            raise ValueError("ttl must be > 0")
        if key in self._entries:
            del self._entries[key]
        elif len(self._entries) >= self.capacity:
            self._purge_expired()
            if len(self._entries) >= self.capacity:
                self._entries.popitem(last=False)
        expires_at = self.clock() + ttl if ttl is not None else None
        self._entries[key] = (value, expires_at)

    def get(self, key, default=None):
        if key not in self._entries:
            return default
        value, expires_at = self._entries[key]
        if self._is_expired(expires_at):
            del self._entries[key]
            return default
        self._entries.move_to_end(key)
        return value

    def __contains__(self, key):
        if key not in self._entries:
            return False
        value, expires_at = self._entries[key]
        if self._is_expired(expires_at):
            del self._entries[key]
            return False
        return True

    def __len__(self):
        self._purge_expired()
        return len(self._entries)

    def keys(self):
        self._purge_expired()
        return list(self._entries.keys())

    def evict_expired(self):
        before = len(self._entries)
        self._purge_expired()
        return before - len(self._entries)
