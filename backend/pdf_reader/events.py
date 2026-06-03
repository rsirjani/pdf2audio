"""Per-job in-memory pub/sub for Server-Sent Events.

The ingest worker runs in a background thread and emits phase events via `emit()`.
SSE clients subscribe via `subscribe()` which yields a (thread-safe) Queue of events
for that specific job_id. When the job emits a terminal event (done or error), the
queue receives a sentinel and the SSE generator closes the stream.

We keep the API thread-safe and synchronous so the worker can publish without
needing the asyncio loop. The SSE endpoint adapts the sync Queue into an async
generator via run_in_threadpool.
"""
from __future__ import annotations

import json
import queue
import threading
import time
from collections import defaultdict
from typing import Any

# job_id -> list of subscriber queues. New subscribers get the replay buffer first.
_subscribers: dict[str, list[queue.Queue]] = defaultdict(list)
_replay: dict[str, list[dict]] = defaultdict(list)
_lock = threading.Lock()

_SENTINEL = {"type": "_close"}
_REPLAY_CAP = 32  # so a late-joining browser sees recent history


def emit(job_id: str, type: str, data: Any | None = None) -> None:
    """Publish an event for a job. Non-blocking and safe to call from any thread."""
    event = {"type": type, "data": data, "ts": time.time()}
    with _lock:
        # Store in replay buffer so late subscribers see the history.
        buf = _replay[job_id]
        buf.append(event)
        if len(buf) > _REPLAY_CAP:
            del buf[: len(buf) - _REPLAY_CAP]
        # Fan out to live subscribers.
        for sub in _subscribers.get(job_id, []):
            try:
                sub.put_nowait(event)
            except queue.Full:
                pass
        # On terminal event, signal subscribers to close.
        if type in ("done", "error"):
            for sub in _subscribers.get(job_id, []):
                try:
                    sub.put_nowait(_SENTINEL)
                except queue.Full:
                    pass


def subscribe(job_id: str) -> queue.Queue:
    """Returns a Queue that receives all subsequent events for job_id plus replay history."""
    q: queue.Queue = queue.Queue(maxsize=256)
    with _lock:
        for prior in _replay.get(job_id, []):
            try:
                q.put_nowait(prior)
            except queue.Full:
                break
        _subscribers[job_id].append(q)
        # If the job already terminated, send the close marker immediately so the
        # SSE stream finishes after delivering the replay.
        if _replay.get(job_id) and _replay[job_id][-1]["type"] in ("done", "error"):
            try:
                q.put_nowait(_SENTINEL)
            except queue.Full:
                pass
    return q


def unsubscribe(job_id: str, q: queue.Queue) -> None:
    with _lock:
        try:
            _subscribers[job_id].remove(q)
        except ValueError:
            pass
        if not _subscribers[job_id]:
            del _subscribers[job_id]


def cleanup(job_id: str) -> None:
    """Drop the replay buffer for a finished job once no subscribers remain."""
    with _lock:
        if job_id in _subscribers and _subscribers[job_id]:
            return  # someone is still listening
        _replay.pop(job_id, None)
        _subscribers.pop(job_id, None)


def serialize_sse(event: dict) -> str:
    """Format an event as a single SSE record."""
    return f"data: {json.dumps(event)}\n\n"


SENTINEL = _SENTINEL
