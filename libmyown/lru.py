from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Callable, Generic, Hashable, TypeVar

V = TypeVar("V")

_MISSING = object()


class LRUCache(Generic[V]):
    """Small thread-safe LRU. Values for immutable keys (commit shas) never need invalidation."""

    def __init__(self, maxsize: int) -> None:
        self._maxsize = maxsize
        self._data: OrderedDict[Hashable, V] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: Hashable, default=None):
        with self._lock:
            value = self._data.get(key, _MISSING)
            if value is _MISSING:
                return default
            self._data.move_to_end(key)
            return value

    def put(self, key: Hashable, value: V) -> None:
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self._maxsize:
                self._data.popitem(last=False)

    def get_or_compute(self, key: Hashable, compute: Callable[[], V]) -> V:
        value = self.get(key, _MISSING)
        if value is not _MISSING:
            return value
        value = compute()
        self.put(key, value)
        return value

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)
