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
        
        current_time = self.clock()
        
        if key in self.cache:
            self.cache[key] = (value, current_time, ttl)
            self.keys.remove(key)
            self.keys.append(key)
            return
        
        # Remove expired entries
        expired = []
        for k in self.cache:
            if self.clock() >= self.cache[k][1] + self.cache[k][2]:
                expired.append(k)
        for k in expired:
            del self.cache[k]
            self.keys.remove(k)
        
        # Check capacity
        if len(self.cache) >= self.capacity:
            # Evict least recently used
            lru_key = self.keys[0]
            del self.cache[lru_key]
            self.keys.remove(lru_key)
        
        self.cache[key] = (value, current_time, ttl)
        self.keys.append(key)

    def get(self, key, default=None):
        current_time = self.clock()
        if key in self.cache:
            value, stored_time, ttl = self.cache[key]
            if current_time >= stored_time + ttl:
                del self.cache[key]
                self.keys.remove(key)
                return default
            self.keys.remove(key)
            self.keys.append(key)
            return value
        return default

    def __contains__(self, key):
        current_time = self.clock()
        return key in self.cache and current_time < self.cache[key][1] + self.cache[key][2]

    def __len__(self):
        current_time = self.clock()
        return sum(1 for k in self.cache if current_time < self.cache[k][1] + self.cache[k][2])

    def keys(self):
        current_time = self.clock()
        return [k for k in self.keys if current_time < self.cache[k][1] + self.cache[k][2]]

    def evict_expired(self):
        current_time = self.clock()
        expired = []
        for k in self.cache:
            if current_time >= self.cache[k][1] + self.cache[k][2]:
                expired.append(k)
        for k in expired:
            del self.cache[k]
            self.keys.remove(k)
        return len(expired)