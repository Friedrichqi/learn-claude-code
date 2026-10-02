import time

class TTLCache:
    def __init__(self, capacity, clock=time.monotonic):
        if capacity < 1:
            raise ValueError("Capacity must be at least 1")
        self.capacity = capacity
        self.clock = clock
        self.cache = {}
        self.order = []  # List of keys in LRU order

    def put(self, key, value, ttl=None):
        if ttl is None:
            ttl = float('inf')
        elif ttl <= 0:
            raise ValueError("TTL must be positive")
        # Remove expired entries
        self._evict_expired()
        # Check if the key already exists
        if key in self.cache:
            # Update the value and reset the TTL
            self.cache[key] = (value, self.clock(), ttl)
            # Move the key to the end of the order list
            self.order.remove(key)
            self.order.append(key)
            return
        # If the cache is full, evict the LRU entry
        if len(self.cache) >= self.capacity:
            lru_key = self.order[0]
            del self.cache[lru_key]
            self.order.pop(0)
        # Add the new entry
        self.cache[key] = (value, self.clock(), ttl)
        self.order.append(key)

    def get(self, key, default=None):
        # Remove expired entries
        self._evict_expired()
        if key in self.cache:
            value, _, _ = self.cache[key]
            # Move the key to the end of the order list
            self.order.remove(key)
            self.order.append(key)
            return value
        return default

    def __contains__(self, key):
        # Remove expired entries
        self._evict_expired()
        return key in self.cache

    def __len__(self):
        # Remove expired entries
        self._evict_expired()
        return len(self.cache)

    def keys(self):
        # Remove expired entries
        self._evict_expired()
        return list(self.order)

    def evict_expired(self):
        removed = 0
        # Remove expired entries from the cache
        to_remove = []
        for key in self.order:
            if key not in self.cache:
                continue
            value, timestamp, ttl = self.cache[key]
            if self.clock() >= timestamp + ttl:
                to_remove.append(key)
        for key in to_remove:
            del self.cache[key]
            self.order.remove(key)
            removed += 1
        return removed

    def _evict_expired(self):
        # Helper method to remove expired entries
        to_remove = []
        for key in self.order:
            if key not in self.cache:
                continue
            value, timestamp, ttl = self.cache[key]
            if self.clock() >= timestamp + ttl:
                to_remove.append(key)
        for key in to_remove:
            del self.cache[key]
            self.order.remove(key)
