"""A tiny in-memory record store."""

from pkg import canonical_key


class Store:
    def __init__(self):
        self._items = {}

    def put(self, key, value):
        self._items[canonical_key(key)] = value

    def get(self, key):
        return self._items.get(canonical_key(key))

    def keys(self):
        return sorted(self._items)
