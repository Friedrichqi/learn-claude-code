import time

class TTLCache:
    def __init__(self, capacity, clock=time.monotonic):
        if capacity < 1:
            raise ValueError("Capacity must be at least 1")
        self.capacity = capacity
        self.clock = clock
        self.cache = {}  # key -> (value, timestamp, ttl)
        self.order = []  # list of keys in order of use

    def put(self, key, value, ttl=None):
        if ttl is not None and ttl <= 0:
            raise ValueError("TTL must be positive")
        # Remove expired entries
        self.evict_expired()
        # Check if the cache is full
        if len(self.cache) >= self.capacity:
            # Evict the least recently used entry
            if self.order:
                lru_key = self.order[0]
                del self.cache[lru_key]
                self.order.pop(0)
        # Add the new entry
        timestamp = self.clock()
        if ttl is None:
            self.cache[key] = (value, timestamp, float('inf'))
        else:
            self.cache[key] = (value, timestamp, timestamp + ttl)
        # Update the order
        if key in self.order:
            self.order.remove(key)
        self.order.append(key)

    def get(self, key, default=None):
        # Remove expired entries
        self.evict_expired()
        if key in self.cache:
            value, timestamp, ttl = self.cache[key]
            # Make the entry the most recently used
            if key in self.order:
                self.order.remove(key)
            self.order.append(key)
            return value
        return default

    def __contains__(self, key):
        # Remove expired entries
        self.evict_expired()
        return key in self.cache

    def __len__(self):
        # Remove expired entries
        self.evict_expired()
        return len(self.cache)

    def keys(self):
        # Remove expired entries
        self.evict_expired()
        return list(self.order)

    def evict_expired(self):
        removed = 0
        # Remove expired entries from the cache
        to_remove = [key for key in self.cache if self.cache[key][2] <= self.clock()]
        for key in to_remove:
            del self.cache[key]
            removed += 1
            if key in self.order:
                self.order.remove(key)
        return removed