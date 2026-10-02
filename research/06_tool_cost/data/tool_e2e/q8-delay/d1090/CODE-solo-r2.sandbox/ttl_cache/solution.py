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
        if ttl is None:
            ttl = float('inf')
        elif ttl <= 0:
            raise ValueError("TTL must be positive")
        # Remove expired entries
        self.evict_expired()
        # Check if the cache is full
        if len(self.cache) >= self.capacity:
            # Evict the least recently used entry
            if self.keys:
                lru_key = self.keys[0]
                del self.cache[lru_key]
                self.keys.pop(0)
        # Add the new entry
        self.cache[key] = (value, self.clock(), ttl)
        self.keys.append(key)
        # Keep the keys list up to date
        self.keys = list(set(self.keys))
        self.keys.sort(key=lambda k: self.keys.index(k))
        # Ensure the key is in the list before sorting
        if key in self.keys:
            self.keys.remove(key)
            self.keys.append(key)
        # Remove duplicates
        self.keys = list(set(self.keys))

    def get(self, key, default=None):
        # Remove expired entries
        self.evict_expired()
        if key in self.cache:
            value, _, _ = self.cache[key]
            # Move the key to the end of the keys list
            self.keys.remove(key)
            self.keys.append(key)
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
        return list(self.cache.keys())

    def evict_expired(self):
        now = self.clock()
        expired = []
        for key, (value, timestamp, ttl) in self.cache.items():
            if now >= timestamp + ttl:
                expired.append(key)
        for key in expired:
            del self.cache[key]
            if key in self.keys:
                self.keys.remove(key)
        return len(expired)