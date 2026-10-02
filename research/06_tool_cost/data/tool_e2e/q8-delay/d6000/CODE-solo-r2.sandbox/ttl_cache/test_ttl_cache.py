import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import solution  # noqa: E402


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class TTLCacheTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()

    def make(self, capacity):
        return solution.TTLCache(capacity, clock=self.clock)

    def test_capacity_validation(self):
        with self.assertRaises(ValueError):
            self.make(0)

    def test_ttl_validation(self):
        cache = self.make(2)
        with self.assertRaises(ValueError):
            cache.put("a", 1, ttl=0)

    def test_put_get_and_default(self):
        cache = self.make(2)
        cache.put("a", 1)
        self.assertEqual(cache.get("a"), 1)
        self.assertIsNone(cache.get("missing"))
        self.assertEqual(cache.get("missing", "dflt"), "dflt")

    def test_stored_none_is_distinguished_from_missing(self):
        cache = self.make(2)
        cache.put("k", None)
        self.assertIsNone(cache.get("k", "dflt"))
        self.assertIn("k", cache)

    def test_lru_eviction_order(self):
        cache = self.make(2)
        cache.put("a", 1)
        cache.put("b", 2)
        self.assertEqual(cache.get("a"), 1)  # a becomes most recent
        cache.put("c", 3)  # evicts b
        self.assertEqual(cache.keys(), ["a", "c"])
        self.assertIsNone(cache.get("b"))

    def test_expiry_boundary(self):
        cache = self.make(2)
        cache.put("a", 1, ttl=5)
        self.clock.advance(4.9)
        self.assertEqual(cache.get("a"), 1)
        self.clock.advance(0.1)
        self.assertEqual(cache.get("a", "gone"), "gone")
        self.assertEqual(len(cache), 0)

    def test_expired_entries_evicted_before_lru(self):
        cache = self.make(2)
        cache.put("a", 1, ttl=1)
        cache.put("b", 2)
        self.clock.advance(2)
        cache.put("c", 3)
        self.assertEqual(cache.keys(), ["b", "c"])

    def test_update_refreshes_recency_and_ttl(self):
        cache = self.make(2)
        cache.put("a", 1, ttl=2)
        cache.put("b", 2)
        self.clock.advance(1.5)
        cache.put("a", 10, ttl=2)  # restarts lifetime, most recent
        cache.put("c", 3)  # evicts b, the least recent
        self.assertEqual(cache.keys(), ["a", "c"])
        self.clock.advance(1.0)  # 2.5 s after the first put, 1.0 s after the second
        self.assertEqual(cache.get("a"), 10)

    def test_contains_does_not_refresh_recency(self):
        cache = self.make(2)
        cache.put("a", 1)
        cache.put("b", 2)
        self.assertTrue("a" in cache)
        cache.put("c", 3)  # a is still least recent
        self.assertEqual(cache.keys(), ["b", "c"])

    def test_evict_expired_count_and_len(self):
        cache = self.make(5)
        cache.put("a", 1, ttl=1)
        cache.put("b", 2, ttl=3)
        cache.put("c", 3)
        self.clock.advance(2)
        self.assertEqual(len(cache), 2)
        self.assertEqual(cache.evict_expired(), 0)  # get/len already purged a
        self.clock.advance(2)
        self.assertEqual(cache.evict_expired(), 1)
        self.assertEqual(cache.keys(), ["c"])

    def test_no_ttl_never_expires(self):
        cache = self.make(1)
        cache.put("a", 1)
        self.clock.advance(10 ** 9)
        self.assertEqual(cache.get("a"), 1)

    def test_keys_reflect_get_order(self):
        cache = self.make(3)
        for key in "abc":
            cache.put(key, key.upper())
        cache.get("a")
        cache.get("b")
        self.assertEqual(cache.keys(), ["c", "a", "b"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
