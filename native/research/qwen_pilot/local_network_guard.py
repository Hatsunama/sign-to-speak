"""Disable direct network access in a fresh Linux research worker.

Spawn the dedicated worker with close_fds=True, no pass_fds, and non-socket
standard streams. Call enforce_offline() before loading the SDK or reading
research media, and abort the worker on failure. The parent stages all inputs
first. The filter is inherited by worker threads and descendants and cannot be
removed. This is a socket barrier for the SDK; it assumes a trusted launcher
and local filesystem without network proxies or delegated network access.
"""

import ctypes
import errno
import os
import platform
import sys

_ACTION_ALLOW = 0x7FFF0000  # SCMP_ACT_ALLOW
_ACTION_ERRNO = 0x00050000  # SCMP_ACT_ERRNO base
_FILTER_NNP = 3  # SCMP_FLTATR_CTL_NNP sets PR_SET_NO_NEW_PRIVS
_FILTER_TSYNC = 4  # SCMP_FLTATR_CTL_TSYNC synchronizes the filter across threads
_DENIED_SYSCALLS = (
    b"socket",
    b"socketpair",
    b"connect",
    b"io_uring_setup",
    b"io_uring_enter",
    b"io_uring_register",
    b"pidfd_getfd",
)


def _check_open_descriptors():
    try:
        if len(os.listdir("/proc/self/task")) != 1:
            raise RuntimeError("Offline isolation requires a fresh, single-threaded worker")
        for descriptor in os.listdir("/proc/self/fd"):
            try:
                target = os.readlink(f"/proc/self/fd/{descriptor}")
            except FileNotFoundError:
                # The directory descriptor used by listdir is now closed.
                continue
            if target.startswith("socket:") or target == "anon_inode:[io_uring]":
                raise RuntimeError(f"Offline worker inherited a socket or io_uring descriptor: {descriptor}")
    except OSError as exc:
        raise RuntimeError("Could not verify offline worker descriptors through procfs") from exc


def _require_success(operation, result):
    if result != 0:
        raise RuntimeError(f"Offline isolation failed at {operation} ({result})")


def enforce_offline():
    """Install the worker's seccomp filter or raise RuntimeError."""
    if (
        sys.platform != "linux"
        or platform.machine() not in {"x86_64", "aarch64"}
        or ctypes.sizeof(ctypes.c_void_p) != 8
    ):
        raise RuntimeError("Offline isolation requires 64-bit Linux on x86_64 or aarch64")

    _check_open_descriptors()
    try:
        library = ctypes.CDLL("libseccomp.so.2")
        library.seccomp_init.argtypes = [ctypes.c_uint32]
        library.seccomp_init.restype = ctypes.c_void_p
        library.seccomp_release.argtypes = [ctypes.c_void_p]
        library.seccomp_release.restype = None
        library.seccomp_attr_set.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint32]
        library.seccomp_attr_set.restype = ctypes.c_int
        library.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
        library.seccomp_syscall_resolve_name.restype = ctypes.c_int
        library.seccomp_rule_add_exact.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint,
        ]
        library.seccomp_rule_add_exact.restype = ctypes.c_int
        library.seccomp_load.argtypes = [ctypes.c_void_p]
        library.seccomp_load.restype = ctypes.c_int
    except (OSError, AttributeError) as exc:
        raise RuntimeError("Offline isolation requires a compatible libseccomp.so.2") from exc

    context = library.seccomp_init(_ACTION_ALLOW)
    if not context:
        raise RuntimeError("Offline isolation failed at seccomp_init")
    try:
        _require_success("seccomp_attr_set(NNP)", library.seccomp_attr_set(context, _FILTER_NNP, 1))
        _require_success("seccomp_attr_set(TSYNC)", library.seccomp_attr_set(context, _FILTER_TSYNC, 1))
        for name in _DENIED_SYSCALLS:
            number = library.seccomp_syscall_resolve_name(name)
            if number < 0:
                raise RuntimeError(f"Offline isolation could not resolve syscall {name.decode('ascii')}")
            _require_success(
                f"seccomp_rule_add_exact({name.decode('ascii')})",
                library.seccomp_rule_add_exact(context, _ACTION_ERRNO | errno.EPERM, number, 0),
            )
        _require_success("seccomp_load", library.seccomp_load(context))
    finally:
        library.seccomp_release(context)
