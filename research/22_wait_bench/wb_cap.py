"""Topic 22: keep at most CAP bytes of an agent command's output; Harbor's exec keeps all of it in memory.

Since 2026-11-01 20:05 UTC (README Changes). In `n8_none` an agent ran `zip -FF` on a damaged archive; reading EOF for
its question, zip asks again without end (over 40 MB/s in a test). Harbor kept every byte, so within 9 minutes the
eval container passed its 100 GB and the kernel killed Harbor, and the run with it. wb_agent.WBAgent's RPC execs (the
agent's commands and the child's file helpers) now keep the first CAP bytes and read and drop the rest, so a command
still runs to its end or its timeout as before. The model sees the first 50,000 characters either way
(s15_child.run_bash), and the child's file reads are at most 20 MB, about 27 MB in base64; CAP is 64 MiB.
Harbor-free, so test_wb.py runs it on the host.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

CAP = 1 << 26


async def read_capped(stream: asyncio.StreamReader, cap: int, state: dict, emit=None) -> None:
    """Read the stream to its end into state["kept"] (its first `cap` bytes) and state["dropped"] (the count of the
    rest). emit(line) gets each kept line with its newline, and a last partial one, as Harbor's streamed path does."""
    kept, start = state.setdefault("kept", bytearray()), 0
    state.setdefault("dropped", 0)
    while chunk := await stream.read(1 << 20):
        room = max(cap - len(kept), 0)
        kept += chunk[:room]
        state["dropped"] += len(chunk) - min(len(chunk), room)
        while emit is not None and (i := kept.find(b"\n", start)) != -1:
            await emit(bytes(kept[start:i + 1]))
            start = i + 1
    if emit is not None and start < len(kept):
        await emit(bytes(kept[start:]))


async def collect(process: asyncio.subprocess.Process, *, timeout_sec: int | None, cap: int, log: Path | None,
                  terminate, result, on_output=None):
    """Harbor 0.24's DockerEnvironment._collect_buffered_output (on_output None) or _collect_streamed_output, with
    read_capped in place of communicate() or the unbounded line reader: the same result, timeout and cancel handling.
    Logs a line to `log` when it drops output."""
    state: dict = {}
    lines: list[str] = []

    async def emit(raw: bytes) -> None:
        lines.append(raw.decode(errors="replace"))
        await on_output(lines[-1], "stdout")

    async def read_and_wait() -> None:
        await read_capped(process.stdout, cap, state, emit if on_output is not None else None)
        await process.wait()

    def note(**extra) -> None:
        if state.get("dropped") and log is not None:
            with log.open("a") as f:
                f.write(json.dumps({"t": time.time(), "kept": len(state["kept"]), "dropped": state["dropped"],
                                    **extra}) + "\n")

    try:
        await (asyncio.wait_for(read_and_wait(), timeout=timeout_sec) if timeout_sec else read_and_wait())
    except asyncio.TimeoutError:
        note(timed_out=True)
        await terminate(process)
        raise RuntimeError(f"Command timed out after {timeout_sec} seconds")
    except BaseException:
        if on_output is not None and process.returncode is None:
            await terminate(process)
        raise
    note(rc=process.returncode)
    stdout = "".join(lines) if on_output is not None else bytes(state["kept"]).decode(errors="replace")
    return result(stdout=stdout or None, stderr=None, return_code=process.returncode or 0)
