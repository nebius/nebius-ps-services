"""Bounded native JSONL framing; model output never supplies control authority."""
from __future__ import annotations

import json


class NativeStream:
    def __init__(self, agent, *, limit=1024 * 1024):
        if agent not in {"codex", "claude"}:
            raise ValueError("unsupported native stream")
        self.agent = agent
        self.limit = limit
        self.pending = bytearray()
        self.discarding = False
        self.final = None
        self.completed = False
        self.failed = False
        self.malformed = False
        self.frames = 0

    def feed(self, chunk):
        for index, part in enumerate(chunk.split(b"\n")):
            if index:
                if not self.discarding and self.pending:
                    self._frame(bytes(self.pending))
                self.pending.clear()
                self.discarding = False
            if not self.discarding:
                if len(self.pending) + len(part) > self.limit:
                    # An unclassified lost frame may be a failure or supersede
                    # an earlier result. Continue draining, but never accept it.
                    self.malformed = True
                    self.pending.clear()
                    self.discarding = True
                else:
                    self.pending.extend(part)

    def _frame(self, raw):
        self.frames += 1
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError, RecursionError):
            self.malformed = True
            return
        if not isinstance(value, dict):
            self.malformed = True
            return
        kind = value.get("type")
        if self.agent == "codex":
            if kind == "item.completed":
                item = value.get("item")
                if isinstance(item, dict) and item.get("type") == "agent_message":
                    if self.completed:
                        self.malformed = True
                    self.final = item.get("text")
            elif kind == "turn.completed":
                if self.completed:
                    self.malformed = True
                self.completed = True
            elif kind in {"turn.failed", "error"}:
                self.failed = True
        elif kind == "result":
            if self.completed:
                self.malformed = True
            self.final = json.dumps(value)
            self.completed = True
            self.failed = self.failed or value.get("is_error") is not False or value.get("subtype") != "success"

    def result(self):
        if (self.pending or self.discarding or self.malformed or self.failed
                or not self.completed or not isinstance(self.final, str)):
            raise ValueError("native terminal frame is incomplete or unsuccessful")
        return self.final
