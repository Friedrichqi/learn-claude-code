import time
from collections import OrderedDict


class TTLCache:
    def __init__(self, capacity, clock=time.monotonic):
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self._capacity = capacity
        self._clock = clock
        # key -> (value, expiry_time or None)
        self._entries = OrderedDict()

    def _is_expired(self, expiry):
        return expiry is not None and self._clock() >= expiry

    def _purge_expired(self):
        """Remove all expired entries; return the number removed."""
        now = self._clock()
        expired = [
            k for k, (_, expiry) in self._entries.items()
            if expiry is not None and now >= expiry
        ]
        for k in expired:
            del self._entries[k]
        return len(expired)

    def put(self, key, value, ttl=None):
        if ttl is not None and ttl <= 0:
            raise ValueError("ttl must be positive")
        now = self._clock()
        if key in self._entries:
            del self._entries[key]
        else:
            self._purge_expired()
            while len(self._entries) >= self._capacity:
                self._entries.popitem(last=False)
        expiry = None if ttl is None else now + ttl
        self._entries[key] = (value, expiry)

    def get(self, key, default=None):
        entry = self._entries.get(key)
        if entry is None:
            return default
        value, expiry = entry
        if self._is_expired(expiry):
            del self._entries[key]
            return default
        self._entries.move_to_end(key)
        return value

    def __contains__(self, key):
        entry = self._entries.get(key)
        if entry is None:
            return False
        return not self._is_expired(entry[1])

    def __len__(self):
        self._purge_expired()
        return len(self._entries)

    def keys(self):
        self._purge_expired()
        return list(self._entries.keys())

    def evict_expired(self):
        return self._purge_expired()
