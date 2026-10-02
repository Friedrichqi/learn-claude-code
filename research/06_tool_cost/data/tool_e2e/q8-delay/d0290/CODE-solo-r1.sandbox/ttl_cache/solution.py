import time

class TTLCache:
    def __init__(self, capacity, clock=time.monotonic):
        if capacity < 1:
            raise ValueError("Capacity must be at least 1")
        self.capacity = capacity
        self.clock = clock
        self.cache = {}  # key -> (value, ttl, last_used)
        self.keys = []  # list of keys in order of use

    def put(self, key, value, ttl=None):
        if ttl is not None and ttl <= 0:
            raise ValueError("TTL must be positive")
        # Remove expired entries
        self._evict_expired()
        # Check if the cache is full
        if len(self.cache) >= self.capacity:
            # Evict the least recently used entry
            if self.keys:
                lru_key = self.keys[0]
                del self.cache[lru_key]
                self.keys.pop(0)
        # Add the new entry
        current_time = self.clock()
        if ttl is None:
            expiration = float('inf')
        else:
            expiration = current_time + ttl
        self.cache[key] = (value, expiration, current_time)
        # Update the use order
        if key in self.keys:
            self.keys.remove(key)
        self.keys.append(key)

    def get(self, key, default=None):
        current_time = self.clock()
        # Remove expired entries
        self._evict_expired()
        if key in self.cache:
            value, expiration, last_used = self.cache[key]
            # Update the last used time
            self.cache[key] = (value, expiration, current_time)
            self.keys.remove(key)
            self.keys.append(key)
            return value
        return default

    def __contains__(self, key):
        current_time = self.clock()
        self._evict_expired()
        return key in self.cache

    def __len__(self):
        current_time = self.clock()
        self._evict_expired()
        return len(self.cache)

    def keys(self):
        current_time = self.clock()
        self._evict_expired()
        return self.keys

    def evict_expired(self):
        current_time = self.clock()
        removed = 0
        # Remove expired entries
        to_remove = [key for key in self.cache if self.cache[key][1] <= current_time]
        for key in to_remove:
            del self.cache[key]
            self.keys.remove(key)
            removed += 1
        return removed

    def _evict_expired(self):
        current_time = self.clock()
        to_remove = [key for key in self.cache if self.cache[key][1] <= current_time]
        for key in to_remove:
            del self.cache[key]
            self.keys.remove(key)