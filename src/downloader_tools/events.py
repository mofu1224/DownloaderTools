import asyncio
import json
from contextlib import asynccontextmanager


class Events:
    def __init__(self):
        self.clients: set[asyncio.Queue] = set()
        self.refresh_pending = False

    @asynccontextmanager
    async def subscribe(self):
        queue: asyncio.Queue = asyncio.Queue(256)
        self.clients.add(queue)
        try:
            yield queue
        finally:
            self.clients.discard(queue)

    def publish(self, event: dict):
        if event["type"] == "refresh":
            if not self.refresh_pending and self.clients:
                self.refresh_pending = True

                def flush():
                    self.refresh_pending = False
                    self.broadcast({"type": "refresh"})

                asyncio.get_running_loop().call_later(0.25, flush)
            return
        self.broadcast(event)

    def broadcast(self, event):
        for queue in list(self.clients):
            if queue.qsize() >= 256:
                self.clients.discard(queue)
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(None)
            else:
                # 単一Source通知は64KiB未満。API snapshotは別送する。
                if len(json.dumps(event)) > 4096:
                    event = {"type": "refresh"}
                queue.put_nowait(event)
