"""Worker notifications must not add polling delays or lose racing submissions."""
import asyncio

from app.analytics import worker


def test_worker_drains_queue_and_preserves_wake_during_claim(monkeypatch):
    calls, waits = [], []

    class Signal:
        set_value = False

        def clear(self):
            self.set_value = False

        def set(self):
            self.set_value = True

        def wait(self, timeout):
            waits.append((timeout, self.set_value))
            # On the third claim a submission raced with an idle DB check.
            assert self.set_value
            raise asyncio.CancelledError()

    def tick():
        calls.append("claim")
        if len(calls) <= 2:
            return True
        worker.wake_worker()
        return False

    monkeypatch.setattr(worker, "_wakeup", Signal())
    monkeypatch.setattr(worker, "tick", tick)

    async def run():
        try:
            await worker.loop()
        except asyncio.CancelledError:
            pass

    asyncio.run(run())
    assert len(calls) == 3
    assert waits == [(2, True)]
