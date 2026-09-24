"""LRU cache with per-entry time-to-live (see README.md)."""

import time
from collections import OrderedDict


class TTLCache:
    """A bounded LRU cache where each entry may have its own TTL.

    Entries are stored in an ``OrderedDict`` ordered least-recently-used
    first.  Each value is kept as a ``(value, expires_at)`` pair where
    ``expires_at`` is ``None`` for entries that never expire.  An entry
    stored at time ``t`` with lifetime ``ttl`` is considered expired once
    ``clock() >= t + ttl``.
    """

    def __init__(self, capacity, clock=time.monotonic):
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._capacity = capacity
        self._clock = clock
        self._data = OrderedDict()  # key -> (value, expires_at); LRU first

    # -- internal helpers -------------------------------------------------

    def _expired(self, entry, now=None):
        """Return True if the ``(value, expires_at)`` entry is expired."""
        expires_at = entry[1]
        if expires_at is None:
            return False
        if now is None:
            now = self._clock()
        return now >= expires_at

    # -- cache operations --------------------------------------------------

    def put(self, key, value, ttl=None):
        """Store ``value`` under ``key``.

        ``ttl`` is the entry lifetime in seconds; ``None`` means the entry
        never expires and a ``ttl <= 0`` raises ``ValueError``.  Re-storing
        an existing (live) key replaces its value, restarts its lifetime and
        makes it the most recently used entry.  Inserting a *new* key into a
        full cache first drops every expired entry; if the cache is still
        full afterwards, the least recently used entry is evicted.
        """
        if ttl is not None and ttl <= 0:
            raise ValueError("ttl must be a positive number of seconds or None")

        now = self._clock()
        entry = self._data.get(key)
        if entry is not None and self._expired(entry, now):
            # An expired entry counts as absent: drop it, then behave as if
            # the key were brand new.
            del self._data[key]
            entry = None

        if entry is None:
            if len(self._data) >= self._capacity:
                self.evict_expired()
                while len(self._data) >= self._capacity:
                    self._data.popitem(last=False)
        else:
            # Live key: re-insert to move it to the most-recently-used end.
            del self._data[key]

        self._data[key] = (value, None if ttl is None else now + ttl)

    def get(self, key, default=None):
        """Return the live value for ``key`` (which may itself be ``None``).

        A missing or expired key returns ``default``; an expired entry is
        removed.  A live entry becomes the most recently used one.
        """
        entry = self._data.get(key)
        if entry is None:
            return default
        if self._expired(entry):
            del self._data[key]
            return default
        self._data.move_to_end(key)
        return entry[0]

    def __contains__(self, key):
        """Return True for a live entry without changing recency."""
        entry = self._data.get(key)
        if entry is None:
            return False
        if self._expired(entry):
            del self._data[key]
            return False
        return True

    def __len__(self):
        """Return the number of live (non-expired) entries."""
        self.evict_expired()
        return len(self._data)

    def keys(self):
        """Return the live keys ordered least recently used first."""
        self.evict_expired()
        return list(self._data.keys())

    def evict_expired(self):
        """Remove all expired entries and return how many were removed."""
        now = self._clock()
        expired = [
            key
            for key, entry in self._data.items()
            if self._expired(entry, now)
        ]
        for key in expired:
            del self._data[key]
        return len(expired)
