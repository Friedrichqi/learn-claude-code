import time

class TTLCache:
    def __init__(self, capacity, clock=time.monotonic):
        if capacity < 1:
            raise ValueError("Capacity must be at least 1")
        self.capacity = capacity
        self.clock = clock
        self.cache = {}  # key: (value, expiration_time, last_used)
        self.keys = []  # list of keys in order of usage

    def put(self, key, value, ttl=None):
        if ttl is not None and ttl <= 0:
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
        # Add or update the entry
        expiration_time = self.clock() + (ttl if ttl is not None else float('inf'))
        self.cache[key] = (value, expiration_time, self.clock())
        # Update the usage order
        if key in self.keys:
            self.keys.remove(key)
        self.keys.append(key)

    def get(self, key, default=None):
        # Remove expired entries
        self.evict_expired()
        if key in self.cache:
            value, expiration_time, last_used = self.cache[key]
            # Update the last used time
            self.cache[key] = (value, expiration_time, self.clock())
            self.keys.remove(key)
            self.keys.append(key)
            return value
        return default

    def __contains__(self, key):
        # Do not remove expired entries for containment check
        # Return True if the key is in the cache and not expired
        if key in self.cache:
            value, expiration_time, last_used = self.cache[key]
            return self.clock() < expiration_time
        return False

    def __len__(self):
        # Remove expired entries
        self.evict_expired()
        return len(self.cache)

    def keys(self):
        # Remove expired entries
        self.evict_expired()
        return list(self.keys)

    def evict_expired(self):
        removed = 0
        # Create a list of keys to remove
        keys_to_remove = [key for key in self.cache if self.clock() >= self.cache[key][1]]
        for key in keys_to_remove:
            del self.cache[key]
            self.keys.remove(key)
            removed += 1
        return removed