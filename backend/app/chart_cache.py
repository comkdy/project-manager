"""Bounded, immutable chart responses with one computation per concurrent key."""
from collections import OrderedDict
from concurrent.futures import Future
from dataclasses import dataclass
import json
import threading
import time


@dataclass(frozen=True)
class ChartPayload:
    fresh: bytes
    cached: bytes

    @classmethod
    def from_result(cls, result):
        # Encode rows and optional Plotly figures once, off the HTTP event loop. Only the
        # small metadata differs between a fresh response and a cache hit.
        def encode(value):
            return json.dumps(value, ensure_ascii=False, allow_nan=False,
                              separators=(",", ":")).encode("utf-8")
        body = {key: value for key, value in result.items() if key != "meta"}
        prefix = encode(body)[:-1] + b',"meta":'
        return cls(prefix + encode({**result["meta"], "cached": False}) + b'}',
                   prefix + encode({**result["meta"], "cached": True}) + b'}')

    @property
    def size(self):
        return len(self.fresh) + len(self.cached)


class ChartCache:
    def __init__(self, ttl, max_bytes=16 * 1024 * 1024, max_entries=128, clock=time.monotonic):
        self.ttl = ttl
        self.max_bytes = max_bytes
        self.max_entries = max_entries
        self.clock = clock
        self._lock = threading.Lock()
        self._entries = OrderedDict()
        self._pending = {}
        self._generation = 0
        self._bytes = 0

    def clear(self):
        with self._lock:
            self._generation += 1
            self._entries.clear()
            self._bytes = 0
            # Old requests may finish using their captured DB/definition, but
            # cannot repopulate this generation or serve requests after clear.

    def get_or_compute(self, key, compute, *, refresh=False):
        with self._lock:
            now = self.clock()
            ttl = self.ttl()
            for expired in [k for k, (created, _) in self._entries.items() if now - created >= ttl]:
                self._bytes -= self._entries.pop(expired)[1].size
            existing = self._entries.get(key)
            if existing is not None and not refresh:
                return existing[1], True
            generation = self._generation
            pending_key = (generation, key)
            future = self._pending.get(pending_key)
            owner = future is None
            if owner:
                future = self._pending[pending_key] = Future()
        if not owner:
            return future.result(), True
        try:
            payload = compute()
            with self._lock:
                if generation == self._generation:
                    previous = self._entries.pop(key, None)
                    if previous:
                        self._bytes -= previous[1].size
                    if payload.size <= self.max_bytes:
                        while self._entries and (len(self._entries) >= self.max_entries or
                                                 self._bytes + payload.size > self.max_bytes):
                            self._bytes -= self._entries.popitem(last=False)[1][1].size
                        self._entries[key] = (self.clock(), payload)
                        self._bytes += payload.size
                self._pending.pop(pending_key, None)
            future.set_result(payload)
            return payload, False
        except BaseException as error:
            with self._lock:
                self._pending.pop(pending_key, None)
            future.set_exception(error)
            raise
