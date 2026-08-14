"""Cross-platform exclusive file locks for ADWF trusted local state.

POSIX uses flock. Windows uses msvcrt byte-range locking. Callers lock a
separate *.lock file and then perform atomic fsync+replace on the protected
state. No third-party dependency is required.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, BinaryIO
import os
import time


@contextmanager
def exclusive_file_lock(path: str | Path, *, timeout_seconds: float = 15.0) -> Iterator[BinaryIO]:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = target.open("a+b")
    deadline = time.monotonic() + max(0.1, float(timeout_seconds))
    locked = False
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            while True:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    locked = True
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("FILE_LOCK_TIMEOUT")
                    time.sleep(0.05)
        else:
            import fcntl
            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("FILE_LOCK_TIMEOUT")
                    time.sleep(0.05)
        yield handle
    finally:
        if locked:
            try:
                if os.name == "nt":
                    import msvcrt
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        handle.close()
