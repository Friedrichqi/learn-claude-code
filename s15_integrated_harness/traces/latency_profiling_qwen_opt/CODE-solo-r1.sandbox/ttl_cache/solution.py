"""LRU cache with per-entry time-to-live."""


class TTLCache:
    def __init__(self, capacity, clock=None):
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        import time

        self._capacity = capacity
        self._clock = clock if clock is not None else time.monotonic
        self._entries = {}  # key -> [value, expires_at (None if never)]
        self._order = []    # keys in LRU -> MRU order
        self._index = {}    # key -> position in _order

    def _is_expired(self, expires_at):
        return expires_at is not None and self._clock() >= expires_at

    def _remove_key(self, key):
        pos = self._index.pop(key)
        del self._entries[key]
        del self._order[pos]
        self._index = {k: i for i, k in enumerate(self._order)}

    def _touch(self, key):
        pos = self._index[key]
        if pos != len(self._order) - 1:
            self._order.pop(pos)
            self._order.append(key)
            self._index = {k: i for i, k in enumerate(self._order)}

    def put(self, key, value, ttl=None):
        if ttl is not None and ttl <= 0:
            raise ValueError("ttl must be positive")
        expires_at = None if ttl is None else self._clock() + ttl
        if key in self._entries:
            self._entries[key] = [value, expires_at]
            self._touch(key)
            return
        if len(self._entries) >= self._capacity:
            self._purge_expired()
            if len(self._entries) >= self._capacity:
                self._remove_key(self._order[0])
        self._entries[key] = [value, expires_at]
        self._order.append(key)
        self._index[key] = len(self._order) - 1

    def _purge_expired(self):
        now = self._clock()
        expired = [
            k for k in self._order
            if self._entries[k][1] is not None and now >= self._entries[k][1]
        ]
        for k in expired:
            self._remove_key(k)
        return len(expired)

    def get(self, key, default=None):
        entry = self._entries.get(key)
        if entry is None or self._is_expired(entry[1]):
            if entry is not None and self._is_expired(entry[1]):
                self._remove_key(key)
            return default
        self._touch(key)
        return entry[0]

    def __contains__(self, key):
        entry = self._entries.get(key)
        return entry is not None and not self._is_expired(entry[1])

    def __len__(self):
        self._purge_expired()
        return len(self._entries)

    def keys(self):
        self._purge_expired()
        return list(self._order)

    def evict_expired(self):
        return self._purge_expired()
