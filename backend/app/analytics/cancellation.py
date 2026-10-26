"""Cooperative cancellation checkpoints; plain calculations remain DB-independent."""
from contextlib import contextmanager
from contextvars import ContextVar

_probe = ContextVar('analysis_cancel_probe', default=None)


class AnalysisCancelled(Exception):
    pass


def checkpoint():
    probe = _probe.get()
    if probe is not None:
        probe()


@contextmanager
def checking(probe):
    token = _probe.set(probe)
    try:
        checkpoint()
        yield
        checkpoint()
    finally:
        _probe.reset(token)
