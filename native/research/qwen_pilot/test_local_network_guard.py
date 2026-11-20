"""Test irreversible worker isolation in disposable Linux processes."""

import errno
from pathlib import Path
import socket
import subprocess
import sys
import textwrap
import unittest
from unittest.mock import Mock, patch

import local_network_guard as guard


def run_worker(source, **kwargs):
    return subprocess.run(
        [sys.executable, "-B", "-c", textwrap.dedent(source)],
        cwd=Path(__file__).resolve().parent,
        capture_output=True,
        text=True,
        timeout=15,
        close_fds=True,
        **kwargs,
    )


class GuardFailureTests(unittest.TestCase):
    def test_unsupported_platform_fails_before_loading_library(self):
        with patch.object(guard.sys, "platform", "win32"), patch.object(guard.ctypes, "CDLL") as load:
            with self.assertRaisesRegex(RuntimeError, "Linux"):
                guard.enforce_offline()
        load.assert_not_called()

    def test_missing_library_fails_closed(self):
        with (
            patch.object(guard.sys, "platform", "linux"),
            patch.object(guard.platform, "machine", return_value="x86_64"),
            patch.object(guard, "_check_open_descriptors"),
            patch.object(guard.ctypes, "CDLL", side_effect=OSError("missing")),
        ):
            with self.assertRaisesRegex(RuntimeError, "libseccomp"):
                guard.enforce_offline()

    def test_failed_filter_load_is_not_reported_as_success(self):
        for result in (-errno.EACCES, 42):
            with self.subTest(result=result):
                library = Mock()
                library.seccomp_init.return_value = 1
                library.seccomp_attr_set.return_value = 0
                library.seccomp_syscall_resolve_name.return_value = 1
                library.seccomp_rule_add_exact.return_value = 0
                library.seccomp_load.return_value = result
                with (
                    patch.object(guard.sys, "platform", "linux"),
                    patch.object(guard.platform, "machine", return_value="x86_64"),
                    patch.object(guard, "_check_open_descriptors"),
                    patch.object(guard.ctypes, "CDLL", return_value=library),
                ):
                    with self.assertRaisesRegex(RuntimeError, "seccomp_load"):
                        guard.enforce_offline()
                library.seccomp_release.assert_called_once_with(1)


@unittest.skipUnless(sys.platform == "linux", "Linux kernel integration")
class LinuxWorkerTests(unittest.TestCase):
    def test_worker_blocks_sockets_and_retains_threads_and_local_file_io(self):
        result = run_worker("""
            import ctypes
            import errno
            from pathlib import Path
            import socket
            import subprocess
            import sys
            import tempfile
            from concurrent.futures import ThreadPoolExecutor

            import local_network_guard

            # Establish that inherited policy lets the worker create IPv4
            # sockets before installing its own filter. No connections are made.
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM):
                pass

            local_network_guard.enforce_offline()
            status = Path("/proc/self/status").read_text()
            assert status.split("NoNewPrivs:", 1)[1].splitlines()[0].strip() == "1"

            def require_eperm(operation):
                try:
                    operation()
                except OSError as exc:
                    assert exc.errno == errno.EPERM, repr(exc)
                else:
                    raise AssertionError("Denied operation succeeded")

            def check_sockets():
                require_eperm(lambda: socket.socket(socket.AF_INET, socket.SOCK_STREAM))
                require_eperm(lambda: socket.socket(socket.AF_INET6, socket.SOCK_DGRAM))
                require_eperm(lambda: socket.socket(socket.AF_UNIX, socket.SOCK_STREAM))
                require_eperm(socket.socketpair)

            check_sockets()
            with ThreadPoolExecutor(max_workers=1) as threads:
                threads.submit(check_sockets).result(timeout=3)

            libc = ctypes.CDLL(None, use_errno=True)
            assert libc.connect(-1, None, 0) == -1
            assert ctypes.get_errno() == errno.EPERM

            library = ctypes.CDLL("libseccomp.so.2")
            for name in (b"io_uring_setup", b"io_uring_enter", b"io_uring_register", b"pidfd_getfd"):
                number = library.seccomp_syscall_resolve_name(name)
                assert number >= 0, name
                assert libc.syscall(number, -1, 0, 0, 0, 0, 0) == -1, name
                assert ctypes.get_errno() == errno.EPERM, name

            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "sample"
                path.write_bytes(b"local")
                assert path.read_bytes() == b"local"

            code = (
                "import errno, socket, sys\\n"
                "try: socket.socket()\\n"
                "except OSError as exc: sys.exit(0 if exc.errno == errno.EPERM else 1)\\n"
                "sys.exit(1)\\n"
            )
            subprocess.run([sys.executable, "-B", "-c", code], check=True, timeout=3)
        """)
        self.assertEqual(result.returncode, 0, result.stderr)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM):
            pass

    def test_worker_rejects_an_inherited_socket_even_on_stdin(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as existing:
            result = run_worker("""
                from local_network_guard import enforce_offline

                try:
                    enforce_offline()
                except RuntimeError as exc:
                    assert "socket" in str(exc), str(exc)
                else:
                    raise AssertionError("Inherited socket was accepted")
            """, stdin=existing)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
