# Problem: LRU cache with per-entry time-to-live

Implement `solution.py` in this directory with a class `TTLCache`.

```python
cache = TTLCache(capacity, clock=time.monotonic)
```

* `capacity` is the maximum number of live entries; anything below 1 raises `ValueError`.
* `clock` is a zero-argument callable returning the current time in seconds (tests inject a fake).

Methods:

* `put(key, value, ttl=None)`: store `value`. `ttl` is the lifetime in seconds; `None` means the
  entry never expires; a `ttl <= 0` raises `ValueError`. Storing an existing key replaces its value,
  restarts its lifetime and makes it the most recently used entry. When a **new** key would exceed
  the capacity, first drop every expired entry; if the cache is still full, evict the least recently
  used entry.
* `get(key, default=None)`: return the stored value (which may itself be `None`) and make the entry
  the most recently used one. A missing or expired key returns `default`; an expired entry is removed.
* `key in cache`: `True` for a live entry. Must **not** change recency. Expired entries count as absent.
* `len(cache)`: number of live (non-expired) entries.
* `keys()`: list of live keys from least recently used to most recently used.
* `evict_expired()`: remove all expired entries and return how many were removed.

An entry stored at time `t` with lifetime `ttl` is expired when `clock() >= t + ttl` (it is still live
at `t + ttl - 0.1`). Run the tests with `python3 test_ttl_cache.py`.
