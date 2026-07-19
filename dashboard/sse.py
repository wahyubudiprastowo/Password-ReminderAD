import asyncio
from typing import Any, Dict

class Broadcaster:
    def __init__(self):
        self._subs = []
        self._history = []
        self._max_history = 500

    def subscribe(self):
        q = asyncio.Queue(maxsize=100)
        self._subs.append(q)
        return q

    def unsubscribe(self, q):
        if q in self._subs:
            self._subs.remove(q)

    async def publish(self, event_type, data):
        self._history.append({"type": event_type, "data": data})
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]
        dead = []
        for q in self._subs:
            try:
                q.put_nowait({"type": event_type, "data": data})
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self.unsubscribe(q)

    def history(self):
        return list(self._history)

    def clear_history(self):
        self._history = []

broadcaster = Broadcaster()
