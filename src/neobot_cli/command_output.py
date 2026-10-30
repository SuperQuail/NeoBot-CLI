"""Stream child output without allowing inherited pipes to hold the UI open."""

from __future__ import annotations

import codecs
import io
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time

from .progress import Progress, ProgressSink


def _terminate(child: subprocess.Popen) -> None:
    if os.name == "nt":
        if child.poll() is None:
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(child.pid), "/T", "/F"],
                    capture_output=True,
                    timeout=5,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
            if child.poll() is None:
                child.kill()
    else:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    child.wait(timeout=5)


def run_streamed(command: list[str], *, cwd, env: dict, timeout: float, sink: ProgressSink):
    options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    deadline = time.monotonic() + timeout
    # Independent read handles do not share the child's file offsets. Unlike
    # pipes, regular files never wait for a descendant to close its inherited fd.
    # On Windows an orphan may briefly keep a file open after its parent exits;
    # that must not turn a completed operation into another blocked cleanup.
    with tempfile.TemporaryDirectory(prefix="neo-output-", ignore_cleanup_errors=True) as directory:
        paths = [Path(directory) / name for name in ("stdout", "stderr")]
        with paths[0].open("wb") as stdout, paths[1].open("wb") as stderr:
            child = subprocess.Popen(
                command, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, **options
            )
            try:
                outputs: list[list[str]] = [[], []]
                decoders = [
                    io.IncrementalNewlineDecoder(codecs.getincrementaldecoder("utf-8")("replace"), translate=True)
                    for _ in paths
                ]
                with paths[0].open("rb") as first, paths[1].open("rb") as second:
                    while True:
                        finished = child.poll() is not None
                        received = False
                        for stream, decoder, output in zip((first, second), decoders, outputs, strict=True):
                            chunk = stream.read(4096)
                            received |= bool(chunk)
                            if text := decoder.decode(chunk):
                                output.append(text)
                                sink(Progress(text, output=True))
                        if finished and not received:
                            break
                        if time.monotonic() >= deadline:
                            raise subprocess.TimeoutExpired(command, timeout)
                        if not finished:
                            time.sleep(min(0.05, max(0, deadline - time.monotonic())))
                for decoder, output in zip(decoders, outputs, strict=True):
                    if tail := decoder.decode(b"", final=True):
                        output.append(tail)
                        sink(Progress(tail, output=True))
                return subprocess.CompletedProcess(command, child.returncode, *("".join(output) for output in outputs))
            finally:
                _terminate(child)
