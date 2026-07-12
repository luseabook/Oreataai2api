import os
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import server
import banti_token_generator
from gateway.runtime import (
    SingleWorkerLock,
    validate_single_worker_configuration,
    worker_lock_path,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class BantiHelperRuntimeTests(unittest.TestCase):
    def test_helper_retries_with_system_ca_when_default_node_tls_fails(self):
        helper_output = json.dumps(
            {
                "jt": "31$live-token",
                "cookies": {"__bid_n": "live-bid"},
                "version": "1.14.3.1",
            }
        )
        failure = subprocess.CalledProcessError(1, ["node", "banti_jt_helper.js"])

        with patch.object(
            banti_token_generator.subprocess,
            "check_output",
            side_effect=[failure, helper_output],
        ) as check_output:
            result = banti_token_generator.generate_banti_artifacts_from_helper()

        first_command = check_output.call_args_list[0].args[0]
        retry_command = check_output.call_args_list[1].args[0]
        self.assertEqual(first_command[0], "node")
        self.assertNotIn("--use-system-ca", first_command)
        self.assertEqual(retry_command[:2], ["node", "--use-system-ca"])
        self.assertEqual(result["cookies"]["__bid_n"], "live-bid")


class SingleWorkerLockTests(unittest.TestCase):
    def test_lock_is_exclusive_across_processes_and_reusable_after_release(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            lock_path = temporary_path / "gateway.lock"
            ready_path = temporary_path / "ready"
            holder_script = "\n".join(
                (
                    "import sys",
                    "from pathlib import Path",
                    "from gateway.runtime import SingleWorkerLock",
                    "lock = SingleWorkerLock(Path(sys.argv[1]))",
                    "lock.acquire()",
                    "Path(sys.argv[2]).touch()",
                    "sys.stdin.readline()",
                    "lock.release()",
                )
            )
            holder = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    holder_script,
                    os.fspath(lock_path),
                    os.fspath(ready_path),
                ],
                cwd=REPOSITORY_ROOT,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            try:
                deadline = time.monotonic() + 10
                while not ready_path.exists() and holder.poll() is None:
                    if time.monotonic() >= deadline:
                        self.fail("timed out waiting for the lock holder")
                    time.sleep(0.01)

                if not ready_path.exists():
                    _, stderr = holder.communicate(timeout=5)
                    self.fail(
                        "lock holder exited before acquiring the lock: "
                        f"returncode={holder.returncode}, stderr={stderr!r}"
                    )

                competing_lock = SingleWorkerLock(lock_path)
                self.assertFalse(competing_lock.is_held)
                self.assertFalse(competing_lock.held)
                with self.assertRaisesRegex(
                    RuntimeError, "single application worker|already held"
                ):
                    competing_lock.acquire()
                self.assertFalse(competing_lock.is_held)

                assert holder.stdin is not None
                holder.stdin.write("release\n")
                holder.stdin.flush()
                holder.wait(timeout=10)
                self.assertEqual(holder.returncode, 0)

                with SingleWorkerLock(lock_path):
                    self.assertTrue(lock_path.exists())
            finally:
                if holder.poll() is None:
                    holder.kill()
                    holder.wait(timeout=5)
                for stream in (holder.stdin, holder.stdout, holder.stderr):
                    if stream is not None and not stream.closed:
                        stream.close()

    def test_repeated_acquire_and_release_are_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            lock = SingleWorkerLock(Path(temporary_directory) / "gateway.lock")

            self.assertFalse(lock.is_held)
            self.assertFalse(lock.held)
            lock.acquire()
            lock.acquire()
            self.assertTrue(lock.is_held)
            self.assertTrue(lock.held)
            lock.release()
            lock.release()
            self.assertFalse(lock.is_held)
            self.assertFalse(lock.held)


class WorkerConfigurationTests(unittest.TestCase):
    def test_worker_lock_path_honors_explicit_override(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            database_path = temporary_path / "accounts.db"
            configured_path = temporary_path / "runtime" / "worker.lock"

            resolved_path = worker_lock_path(
                database_path,
                {"OREATE_WORKER_LOCK_PATH": os.fspath(configured_path)}
            )

            self.assertEqual(resolved_path, configured_path.resolve())

    def test_worker_lock_path_is_scoped_to_the_database_by_default(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "accounts.db"

            resolved_path = worker_lock_path(database_path, {})

            self.assertEqual(
                resolved_path,
                Path(f"{database_path}.worker.lock").resolve(),
            )

    def test_accepts_single_worker_declarations(self):
        declarations = (
            {},
            {"OREATE_APP_WORKERS": "1"},
            {"OREATE_WORKER_COUNT": "1"},
            {"WEB_CONCURRENCY": "1"},
            {"UVICORN_WORKERS": "1"},
            {"GUNICORN_CMD_ARGS": "--bind 127.0.0.1:8000 --workers 1"},
            {"GUNICORN_CMD_ARGS": "-w 1"},
            {"GUNICORN_CMD_ARGS": "--workers=1"},
        )

        for environment in declarations:
            with self.subTest(environment=environment):
                self.assertEqual(
                    validate_single_worker_configuration(environment), 1
                )

    def test_rejects_worker_counts_greater_than_one(self):
        declarations = (
            {"OREATE_APP_WORKERS": "2"},
            {"OREATE_WORKER_COUNT": "2"},
            {"WEB_CONCURRENCY": "4"},
            {"UVICORN_WORKERS": "8"},
            {"GUNICORN_CMD_ARGS": "--workers 2"},
            {"GUNICORN_CMD_ARGS": "-w 3"},
            {"GUNICORN_CMD_ARGS": "--workers=4"},
        )

        for environment in declarations:
            with self.subTest(environment=environment):
                with self.assertRaisesRegex(
                    RuntimeError, "exactly one application worker"
                ):
                    validate_single_worker_configuration(environment)

    def test_rejects_invalid_or_conflicting_worker_declarations(self):
        declarations = (
            {"OREATE_WORKER_COUNT": ""},
            {"WEB_CONCURRENCY": "many"},
            {"UVICORN_WORKERS": "0"},
            {"GUNICORN_CMD_ARGS": "--workers"},
            {"GUNICORN_CMD_ARGS": "--workers nope"},
            {
                "OREATE_WORKER_COUNT": "1",
                "GUNICORN_CMD_ARGS": "--workers 2",
            },
        )

        for environment in declarations:
            with self.subTest(environment=environment):
                with self.assertRaisesRegex(
                    RuntimeError, "invalid|conflicting|exactly one application worker"
                ):
                    validate_single_worker_configuration(environment)


class _StubbornThread:
    def __init__(self) -> None:
        self.join_timeouts = []

    def is_alive(self) -> bool:
        return True

    def join(self, timeout=None) -> None:
        self.join_timeouts.append(timeout)


class ServerLifecycleTests(unittest.TestCase):
    def test_startup_rejects_multiple_workers_before_database_initialization(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            with (
                patch.object(server, "DB_PATH", temporary_path / "accounts.db"),
                patch.object(server, "CONFIG_PATH", temporary_path / "config.json"),
                patch.object(server, "APPLICATION_WORKER_LOCK", None, create=True),
                patch.object(server, "APP_LIFECYCLE_STARTED", False, create=True),
                patch.object(server, "init_db") as init_db,
                patch.object(server, "worker_lock_path", create=True) as lock_path,
                patch.dict(os.environ, {"OREATE_APP_WORKERS": "2"}, clear=True),
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "exactly one application worker"
                ):
                    server.on_startup()

                init_db.assert_not_called()
                lock_path.assert_not_called()
                self.assertFalse(server.APP_LIFECYCLE_STARTED)
                self.assertIsNone(server.APPLICATION_WORKER_LOCK)

    def test_startup_releases_worker_lock_when_database_initialization_fails(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            database_path = temporary_path / "accounts.db"
            failure = RuntimeError("database initialization failed")
            cfg = server.deep_merge(
                server.CFG,
                {
                    "server": {"host": "127.0.0.1"},
                    "deployment": {
                        "allow_public_bind": False,
                        "trust_reverse_proxy": False,
                        "tls_terminated_by_proxy": False,
                    },
                },
            )

            def fail_after_observing_lock() -> None:
                self.assertIsNotNone(server.APPLICATION_WORKER_LOCK)
                self.assertTrue(server.APPLICATION_WORKER_LOCK.is_held)
                raise failure

            with (
                patch.object(server, "CFG", cfg),
                patch.object(server, "DB_PATH", database_path),
                patch.object(server, "CONFIG_PATH", temporary_path / "config.json"),
                patch.object(server, "APPLICATION_WORKER_LOCK", None, create=True),
                patch.object(server, "APP_LIFECYCLE_STARTED", False, create=True),
                patch.object(server, "init_db", side_effect=fail_after_observing_lock),
                patch.object(server, "save_config") as save_config,
                patch.object(server, "recover_stale_running_tasks") as recover_tasks,
                patch.object(server, "recover_interrupted_registration_jobs") as recover_registration_jobs,
                patch.object(server, "ensure_task_worker_started") as start_worker,
                patch.dict(os.environ, {}, clear=True),
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "database initialization failed"
                ):
                    server.on_startup()

                self.assertFalse(server.APP_LIFECYCLE_STARTED)
                self.assertIsNone(server.APPLICATION_WORKER_LOCK)
                save_config.assert_not_called()
                recover_tasks.assert_not_called()
                recover_registration_jobs.assert_not_called()
                start_worker.assert_not_called()

                with SingleWorkerLock(worker_lock_path(database_path, {})):
                    pass

    def test_startup_rejects_public_bind_without_explicit_proxy_tls_acknowledgement(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            database_path = temporary_path / "accounts.db"
            original_cfg = server.CFG
            cfg = server.deep_merge(
                original_cfg,
                {
                    "server": {"host": "0.0.0.0"},
                    "deployment": {
                        "allow_public_bind": False,
                        "trust_reverse_proxy": False,
                        "tls_terminated_by_proxy": False,
                    },
                },
            )
            with (
                patch.object(server, "CFG", cfg),
                patch.object(server, "DB_PATH", database_path),
                patch.object(server, "CONFIG_PATH", temporary_path / "config.json"),
                patch.object(server, "APPLICATION_WORKER_LOCK", None, create=True),
                patch.object(server, "APP_LIFECYCLE_STARTED", False, create=True),
                patch.object(server, "init_db") as init_db,
                patch.object(server, "save_config") as save_config,
                patch.object(server, "recover_stale_running_tasks") as recover_tasks,
                patch.object(server, "recover_interrupted_registration_jobs") as recover_registration_jobs,
                patch.object(server, "ensure_task_worker_started") as start_worker,
                patch.dict(os.environ, {}, clear=True),
            ):
                with self.assertRaisesRegex(RuntimeError, "public bind"):
                    server.on_startup()

                init_db.assert_not_called()
                save_config.assert_not_called()
                recover_tasks.assert_not_called()
                recover_registration_jobs.assert_not_called()
                start_worker.assert_not_called()
                self.assertFalse(server.APP_LIFECYCLE_STARTED)
                self.assertIsNone(server.APPLICATION_WORKER_LOCK)

                with SingleWorkerLock(worker_lock_path(database_path, {})):
                    pass

    def test_repeated_successful_startup_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            database_path = temporary_path / "accounts.db"
            cfg = server.deep_merge(
                server.CFG,
                {
                    "server": {"host": "127.0.0.1"},
                    "deployment": {
                        "allow_public_bind": False,
                        "trust_reverse_proxy": False,
                        "tls_terminated_by_proxy": False,
                    },
                },
            )
            with (
                patch.object(server, "CFG", cfg),
                patch.object(server, "DB_PATH", database_path),
                patch.object(server, "CONFIG_PATH", temporary_path / "config.json"),
                patch.object(server, "APPLICATION_WORKER_LOCK", None, create=True),
                patch.object(server, "APP_LIFECYCLE_STARTED", False, create=True),
                patch.object(server, "init_db") as init_db,
                patch.object(server, "save_config") as save_config,
                patch.object(server, "recover_stale_running_tasks") as recover_tasks,
                patch.object(server, "recover_interrupted_registration_jobs") as recover_registration_jobs,
                patch.object(server, "ensure_task_worker_started") as start_worker,
                patch.dict(os.environ, {}, clear=True),
            ):
                try:
                    server.on_startup()
                    first_lock = server.APPLICATION_WORKER_LOCK
                    server.on_startup()

                    self.assertTrue(server.APP_LIFECYCLE_STARTED)
                    self.assertIs(server.APPLICATION_WORKER_LOCK, first_lock)
                    self.assertTrue(first_lock.is_held)
                    init_db.assert_called_once_with()
                    save_config.assert_called_once_with(server.CFG)
                    recover_tasks.assert_called_once_with(stale_after_seconds=0.0)
                    recover_registration_jobs.assert_called_once_with()
                    start_worker.assert_called_once_with()
                finally:
                    lock = server.APPLICATION_WORKER_LOCK
                    if lock is not None:
                        lock.release()

    def test_shutdown_stops_real_worker_and_releases_worker_lock(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            stop_event = threading.Event()
            wake_event = threading.Event()
            worker = threading.Thread(
                target=stop_event.wait,
                name="runtime-safety-test-worker",
                daemon=True,
            )
            lock = SingleWorkerLock(Path(temporary_directory) / "gateway.lock")
            lock.acquire()
            worker.start()
            try:
                with (
                    patch.object(server, "TASK_WORKER_STOP", stop_event),
                    patch.object(server, "TASK_WORKER_WAKE", wake_event),
                    patch.object(server, "TASK_WORKER_THREAD", worker),
                    patch.object(server, "APPLICATION_WORKER_LOCK", lock, create=True),
                    patch.object(server, "APP_LIFECYCLE_STARTED", True, create=True),
                    patch.object(
                        server,
                        "gateway_cfg",
                        return_value={"worker_shutdown_timeout_seconds": 1},
                    ),
                ):
                    server.on_shutdown()

                    self.assertTrue(stop_event.is_set())
                    self.assertTrue(wake_event.is_set())
                    self.assertFalse(worker.is_alive())
                    self.assertIsNone(server.TASK_WORKER_THREAD)
                    self.assertIsNone(server.APPLICATION_WORKER_LOCK)
                    self.assertFalse(server.APP_LIFECYCLE_STARTED)
                    self.assertFalse(lock.is_held)
            finally:
                stop_event.set()
                worker.join(timeout=1)
                lock.release()

    def test_shutdown_retains_lock_when_worker_does_not_stop(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            stop_event = threading.Event()
            wake_event = threading.Event()
            worker = _StubbornThread()
            lock = SingleWorkerLock(Path(temporary_directory) / "gateway.lock")
            lock.acquire()
            try:
                with (
                    patch.object(server, "TASK_WORKER_STOP", stop_event),
                    patch.object(server, "TASK_WORKER_WAKE", wake_event),
                    patch.object(server, "TASK_WORKER_THREAD", worker),
                    patch.object(server, "APPLICATION_WORKER_LOCK", lock, create=True),
                    patch.object(server, "APP_LIFECYCLE_STARTED", True, create=True),
                    patch.object(
                        server,
                        "gateway_cfg",
                        return_value={"worker_shutdown_timeout_seconds": 0.25},
                    ),
                ):
                    server.on_shutdown()

                    self.assertTrue(stop_event.is_set())
                    self.assertTrue(wake_event.is_set())
                    self.assertEqual(worker.join_timeouts, [0.25])
                    self.assertIs(server.TASK_WORKER_THREAD, worker)
                    self.assertIs(server.APPLICATION_WORKER_LOCK, lock)
                    self.assertFalse(server.APP_LIFECYCLE_STARTED)
                    self.assertTrue(lock.is_held)
            finally:
                lock.release()

    def test_stop_task_worker_without_thread_is_successful(self):
        stop_event = threading.Event()
        wake_event = threading.Event()
        with (
            patch.object(server, "TASK_WORKER_STOP", stop_event),
            patch.object(server, "TASK_WORKER_WAKE", wake_event),
            patch.object(server, "TASK_WORKER_THREAD", None),
        ):
            self.assertTrue(server.stop_task_worker(0.01))
            self.assertIsNone(server.TASK_WORKER_THREAD)
            self.assertTrue(stop_event.is_set())
            self.assertTrue(wake_event.is_set())

    def test_readyz_rejects_started_lifecycle_without_held_worker_lock(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            missing_locks = (
                None,
                SingleWorkerLock(Path(temporary_directory) / "not-held.lock"),
            )
            for application_lock in missing_locks:
                with self.subTest(application_lock=application_lock):
                    with (
                        patch.object(
                            server, "APPLICATION_WORKER_LOCK", application_lock, create=True
                        ),
                        patch.object(server, "APP_LIFECYCLE_STARTED", True, create=True),
                        patch.object(server, "db_conn") as db_conn,
                    ):
                        with self.assertRaises(server.HTTPException) as raised:
                            server.readyz()

                        self.assertEqual(raised.exception.status_code, 503)
                        self.assertEqual(
                            raised.exception.detail,
                            "single application worker lock is not held",
                        )
                        db_conn.assert_not_called()

    def test_readyz_rejects_lock_held_after_lifecycle_left_started_state(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            lock = SingleWorkerLock(Path(temporary_directory) / "stale.lock")
            lock.acquire()
            try:
                with (
                    patch.object(server, "APPLICATION_WORKER_LOCK", lock, create=True),
                    patch.object(server, "APP_LIFECYCLE_STARTED", False, create=True),
                    patch.object(server, "db_conn") as db_conn,
                ):
                    with self.assertRaises(server.HTTPException) as raised:
                        server.readyz()

                    self.assertEqual(raised.exception.status_code, 503)
                    self.assertEqual(
                        raised.exception.detail,
                        "application worker lifecycle is not in a ready state",
                    )
                    db_conn.assert_not_called()
            finally:
                lock.release()

if __name__ == "__main__":
    unittest.main()
