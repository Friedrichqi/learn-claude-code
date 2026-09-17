import os
import sys

sys.dont_write_bytecode = True  # never leave a stale .pyc shadowing an edited module

import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pkg import canonical_key
from report import header, summarize
from store import Store


class TestCanonicalKey(unittest.TestCase):
    def test_lowercases_and_underscores(self):
        self.assertEqual(canonical_key("  Total Count "), "total_count")

    def test_rejects_none(self):
        with self.assertRaises(ValueError):
            canonical_key(None)


class TestStore(unittest.TestCase):
    def test_round_trip_is_key_insensitive(self):
        store = Store()
        store.put("Total Count", 7)
        self.assertEqual(store.get("total_count"), 7)
        self.assertEqual(store.keys(), ["total_count"])


class TestReport(unittest.TestCase):
    def test_header(self):
        self.assertEqual(header("Run Summary"), "# run_summary")

    def test_summarize(self):
        store = Store()
        store.put("A B", 1)
        self.assertEqual(summarize(store, ["A B"]), "a_b=1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
