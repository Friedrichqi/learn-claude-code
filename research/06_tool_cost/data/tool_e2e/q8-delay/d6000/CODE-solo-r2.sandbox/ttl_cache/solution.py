import time

class TTLCache:
    def __init__(self, capacity, clock=time.monotonic):
        if capacity < 1:
            raise ValueError("Capacity must be at least 1")
        self.capacity = capacity
        self.clock = clock
        self.cache = {}
        self.order = []

    def put(self, key, value, ttl=None):
        if ttl is None:
            ttl = float('inf')
        elif ttl <= 0:
            raise ValueError("TTL must be positive")
        # Remove expired entries
        self._evict_expired()
        # Check if the cache is full
        if len(self.cache) >= self.capacity:
            # Evict the least recently used entry
            if self.order:
                lru_key = self.order.pop(0)
                del self.cache[lru_key]
        # Add the new entry
        current_time = self.clock()
        self.cache[key] = (value, current_time, ttl)
        self.order.append(key)

    def get(self, key, default=None):
        current_time = self.clock()
        if key in self.cache:
            value, stored_time, ttl = self.cache[key]
            if current_time >= stored_time + ttl:
                # Entry is expired, remove it
                del self.cache[key]
                self.order.remove(key)
                return default
            # Make the entry the most recently used
            self.order.remove(key)
            self.order.append(key)
            return value
        return default

    def __contains__(self, key):
        current_time = self.clock()
        if key in self.cache:
            value, stored_time, ttl = self.cache[key]
            if current_time >= stored_time + ttl:
                # Entry is expired, remove it
                del self.cache[key]
                self.order.remove(key)
                return False
        return True

    def __len__(self):
        current_time = self.clock()
        count = 0
        for key in self.cache:
            value, stored_time, ttl = self.cache[key]
            if current_time < stored_time + ttl:
                count += 1
        return count

    def keys(self):
        current_time = self.clock()
        live_keys = []
        for key in self.cache:
            value, stored_time, ttl = self.cache[key]
            if current_time < stored_time + ttl:
                live_keys.append(key)
        return live_keys

    def evict_expired(self):
        current_time = self.clock()
        removed = 0
        for key in list(self.cache.keys()):
            value, stored_time, ttl = self.cache[key]
            if current_time >= stored_time + ttl:
                del self.cache[key]
                self.order.remove(key)
                removed += 1
        return removed