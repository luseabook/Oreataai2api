"""Runtime safeguards for deployments that require one application worker.

The gateway currently keeps process-local scheduling and rate-limit state.  A
multi-process application server would therefore provide inconsistent limits.
This module makes that deployment boundary explicit and supplies a process
lock that can be held for the server lifetime.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path
import shlex
from typing import BinaryIO, Mapping

if os.name == "nt":
    import msvcrt
else:
    import fcntl


_WORKER_COUNT_ENVIRONMENT_VARIABLES = (
    "OREATE_APP_WORKERS",
    "OREATE_WORKER_COUNT",
    "WEB_CONCURRENCY",
    "UVICORN_WORKERS",
)
_LOCK_PATH_ENVIRONMENT_VARIABLE = "OREATE_WORKER_LOCK_PATH"


class RuntimeConfigurationError(RuntimeError):
    """Raised when the declared process model is unsafe or malformed."""


def worker_lock_path(
    database_path: str | os.PathLike[str],
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Return the absolute path used for the application-worker lock.

    ``OREATE_WORKER_LOCK_PATH`` is intended for service managers that provide a
    private runtime directory.  Otherwise the lock is colocated with the
    configured database name, so independent database instances do not block
    one another.
    """

    environment = os.environ if environ is None else environ
    configured_path = environment.get(_LOCK_PATH_ENVIRONMENT_VARIABLE)
    if configured_path is not None:
        if not isinstance(configured_path, str) or not configured_path.strip():
            raise RuntimeConfigurationError(
                f"invalid {_LOCK_PATH_ENVIRONMENT_VARIABLE}: expected a non-empty path"
            )
        path = Path(configured_path.strip()).expanduser()
    else:
        expanded_database_path = Path(database_path).expanduser()
        path = Path(f"{expanded_database_path}.worker.lock")

    return path.resolve(strict=False)


def _parse_positive_worker_count(raw_value: object, source: str) -> int:
    if not isinstance(raw_value, str):
        raise RuntimeConfigurationError(
            f"invalid {source}: expected a positive integer worker count"
        )

    value = raw_value.strip()
    if not value or not value.isascii() or not value.isdecimal():
        raise RuntimeConfigurationError(
            f"invalid {source}: expected a positive integer worker count"
        )

    worker_count = int(value, 10)
    if worker_count < 1:
        raise RuntimeConfigurationError(
            f"invalid {source}: worker count must be at least one"
        )
    return worker_count


def _gunicorn_worker_declarations(raw_arguments: object) -> list[tuple[str, int]]:
    if not isinstance(raw_arguments, str):
        raise RuntimeConfigurationError(
            "invalid GUNICORN_CMD_ARGS: expected a command-line string"
        )

    try:
        arguments = shlex.split(raw_arguments)
    except ValueError as exc:
        raise RuntimeConfigurationError(
            f"invalid GUNICORN_CMD_ARGS: {exc}"
        ) from exc

    declarations: list[tuple[str, int]] = []
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        raw_worker_count: str | None = None

        if argument in ("--workers", "-w"):
            index += 1
            if index >= len(arguments):
                raise RuntimeConfigurationError(
                    f"invalid GUNICORN_CMD_ARGS: {argument} requires a worker count"
                )
            raw_worker_count = arguments[index]
        elif argument.startswith("--workers="):
            raw_worker_count = argument.partition("=")[2]
        elif argument.startswith("-w="):
            raw_worker_count = argument.partition("=")[2]
        elif argument.startswith("-w") and len(argument) > 2:
            raw_worker_count = argument[2:]

        if raw_worker_count is not None:
            declarations.append(
                (
                    "GUNICORN_CMD_ARGS",
                    _parse_positive_worker_count(
                        raw_worker_count, "GUNICORN_CMD_ARGS worker count"
                    ),
                )
            )
        index += 1

    return declarations


def declared_worker_count(
    environ: Mapping[str, str] | None = None,
) -> int:
    """Resolve all supported worker declarations into one worker count.

    A missing declaration means one worker.  Multiple declarations are allowed
    only when they agree, preventing a service manager and Gunicorn arguments
    from silently describing different process models.
    """

    environment = os.environ if environ is None else environ
    declarations: list[tuple[str, int]] = []

    for variable_name in _WORKER_COUNT_ENVIRONMENT_VARIABLES:
        if variable_name in environment:
            declarations.append(
                (
                    variable_name,
                    _parse_positive_worker_count(
                        environment[variable_name], variable_name
                    ),
                )
            )

    if "GUNICORN_CMD_ARGS" in environment:
        declarations.extend(
            _gunicorn_worker_declarations(environment["GUNICORN_CMD_ARGS"])
        )

    if not declarations:
        return 1

    distinct_counts = {worker_count for _, worker_count in declarations}
    if len(distinct_counts) != 1:
        rendered_declarations = ", ".join(
            f"{source}={worker_count}" for source, worker_count in declarations
        )
        raise RuntimeConfigurationError(
            f"conflicting worker declarations: {rendered_declarations}"
        )

    return declarations[0][1]


def validate_single_worker_configuration(
    environ: Mapping[str, str] | None = None,
) -> int:
    """Reject deployment declarations that would start multiple workers."""

    worker_count = declared_worker_count(environ)
    if worker_count != 1:
        raise RuntimeConfigurationError(
            "runtime requires exactly one application worker; "
            f"declared worker count is {worker_count}"
        )
    return worker_count


class SingleWorkerLock:
    """A non-blocking, process-wide file lock for the server lifetime."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self._file: BinaryIO | None = None

    @property
    def is_held(self) -> bool:
        """Whether this instance currently owns the operating-system lock."""

        return self._file is not None

    @property
    def held(self) -> bool:
        """Backward-compatible short alias for :attr:`is_held`."""

        return self.is_held

    def acquire(self) -> None:
        """Acquire the lock or fail immediately when another process holds it."""

        if self._file is not None:
            return

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            lock_file = self.path.open("a+b")
        except OSError as exc:
            raise RuntimeConfigurationError(
                f"unable to open single application worker lock {self.path}: {exc}"
            ) from exc

        try:
            self._prepare_lock_byte(lock_file)
            self._lock_file(lock_file)
        except OSError as exc:
            lock_file.close()
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                raise RuntimeConfigurationError(
                    "single application worker lock is already held; "
                    "run exactly one application worker"
                ) from exc
            raise RuntimeConfigurationError(
                f"unable to acquire single application worker lock {self.path}: {exc}"
            ) from exc

        self._file = lock_file

    @staticmethod
    def _prepare_lock_byte(lock_file: BinaryIO) -> None:
        lock_file.seek(0, os.SEEK_END)
        if lock_file.tell() == 0:
            lock_file.write(b"\0")
            lock_file.flush()
        lock_file.seek(0)

    @staticmethod
    def _lock_file(lock_file: BinaryIO) -> None:
        if os.name == "nt":
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def release(self) -> None:
        """Release the lock; calling this on an unlocked instance is harmless."""

        lock_file = self._file
        if lock_file is None:
            return

        self._file = None
        try:
            lock_file.seek(0)
            if os.name == "nt":
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            lock_file.close()

    def __enter__(self) -> SingleWorkerLock:
        self.acquire()
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.release()


__all__ = [
    "RuntimeConfigurationError",
    "SingleWorkerLock",
    "declared_worker_count",
    "validate_single_worker_configuration",
    "worker_lock_path",
]
