import time

class TTLCache:
    def __init__(self, capacity, clock=time.monotonic):
        if capacity < 1:
            raise ValueError("Capacity must be at least 1")
        self.capacity = capacity
        self.clock = clock
        self.cache = {}
        self.keys = []

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
            expiration_time = float('inf')
        else:
            expiration_time = current_time + ttl
        self.cache[key] = (value, expiration_time)
        # Update the most recently used entry
        if key in self.keys:
            self.keys.remove(key)
        self.keys.append(key)

    def get(self, key, default=None):
        current_time = self.clock()
        # Check if the key is present and not expired
        if key in self.cache:
            value, expiration_time = self.cache[key]
            if current_time < expiration_time:
                # Make the entry the most recently used
                self.keys.remove(key)
                self.keys.append(key)
                return value
            else:
                # Remove the expired entry
                del self.cache[key]
                self.keys.remove(key)
        return default

    def __contains__(self, key):
        current_time = self.clock()
        return key in self.cache and current_time < self.cache[key][1]

    def __len__(self):
        current_time = self.clock()
        return sum(1 for value, expiration_time in self.cache.values() if current_time < expiration_time)

    def keys(self):
        current_time = self.clock()
        # Filter out expired entries
        live_keys = [key for key in self.keys if current_time < self.cache[key][1]]
        return live_keys

    def evict_expired(self):
        current_time = self.clock()
        removed = 0
        # Filter out expired entries
        new_cache = {}
        new_keys = []
        for key in self.keys:
            value, expiration_time = self.cache[key]
            if current_time < expiration_time:
                new_cache[key] = (value, expiration_time)
                new_keys.append(key)
        # Update the cache and keys
        self.cache = new_cache
        self.keys = new_keys
        return len(self.cache) - len(new_cache)