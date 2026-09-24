"""A tiny in-memory record store."""

from pkg import normalize_key


class Store:
    def __init__(self):
        self._items = {}

    def put(self, key, value):
        self._items[normalize_key(key)] = value

    def get(self, key):
        return self._items.get(normalize_key(key))

    def keys(self):
        return sorted(self._items)
