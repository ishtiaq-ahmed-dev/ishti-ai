"""Reuse SQLite connections only for a live thread; never reuse a recycled thread ID."""
import sqlite3
import threading


class ThreadConnections:
    def __init__(self):
        self._items = {}
        self._lock = threading.RLock()

    def _close(self, connection):
        try:
            connection.close()
        except sqlite3.Error as exc:
            from ishti.core.logutil import swallowed
            swallowed("sqlite_cache.close", exc)

    def _reap(self):
        for key in list(self._items):
            if not key[0].is_alive():
                self._close(self._items.pop(key))

    def get(self, key):
        with self._lock:
            self._reap()
            return self._items.get(key)

    def __setitem__(self, key, value):
        with self._lock:
            self._reap()
            old = self._items.get(key)
            if old is not None and old is not value:
                self._close(old)
            self._items[key] = value

    def values(self):
        with self._lock:
            self._reap()
            return list(self._items.values())

    def clear(self):
        with self._lock:
            for value in self._items.values():
                self._close(value)
            self._items.clear()

    def __len__(self):
        with self._lock:
            self._reap()
            return len(self._items)
