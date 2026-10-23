#!/usr/bin/env python3
"""Run a command that may touch only one folder (stdlib only).

    confine.py --root DIR --home DIR [--require] -- bash -c CMD

Landlock (Linux 5.13+) limits this process and everything it starts: read and
write beneath --root (its home and its temporary files are kept there too),
read and run the system (/usr, /etc, /opt, /proc, ...), nothing else - no
other chat's or project's folder, not the shared /tmp, /dev/shm or terminals.
On newer kernels it may also connect only to web ports (ABI 4) and not signal
processes outside (ABI 6). No privileges are needed, and the limits cannot be
lifted by the command. Landlock also keeps it from reading other processes'
memory and environment (/proc/<pid>/environ); their command lines stay visible.

Without Landlock (an old kernel, or a seccomp profile that blocks it) the
command runs as it is - or, with --require, not at all: exit code 126 and a
line on stderr saying why.
"""
from __future__ import annotations

import ctypes
import os
import sys

# Landlock system calls (the same numbers on x86_64 and arm64) and access rights.
SYS_CREATE_RULESET, SYS_ADD_RULE, SYS_RESTRICT_SELF = 444, 445, 446
CREATE_RULESET_VERSION = 1
RULE_PATH_BENEATH, RULE_NET_PORT = 1, 2
PR_SET_NO_NEW_PRIVS = 38
NET_BIND_TCP, NET_CONNECT_TCP = 1 << 0, 1 << 1
SCOPE_ABSTRACT_UNIX_SOCKET, SCOPE_SIGNAL = 1 << 0, 1 << 1
WEB_PORTS = (53, 80, 443)                # DNS over TCP, http, https

EXECUTE, WRITE_FILE, READ_FILE, READ_DIR = 1 << 0, 1 << 1, 1 << 2, 1 << 3
REMOVE_DIR, REMOVE_FILE, MAKE_CHAR, MAKE_DIR = 1 << 4, 1 << 5, 1 << 6, 1 << 7
MAKE_REG, MAKE_SOCK, MAKE_FIFO, MAKE_BLOCK, MAKE_SYM = 1 << 8, 1 << 9, 1 << 10, 1 << 11, 1 << 12
REFER, TRUNCATE, IOCTL_DEV = 1 << 13, 1 << 14, 1 << 15

READ = EXECUTE | READ_FILE | READ_DIR
SYSTEM = ("/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64", "/libx32", "/etc", "/opt", "/sys", "/run",
          "/var/lib", "/var/cache", "/proc", "/dev/random", "/dev/urandom")
DEVICES = ("/dev/null", "/dev/zero", "/dev/full", "/dev/tty")   # read and written; not /dev/shm (shared by all)


class RulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64), ("handled_access_net", ctypes.c_uint64),
                ("scoped", ctypes.c_uint64)]


class PathBeneath(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]


class NetPort(ctypes.Structure):
    _fields_ = [("allowed_access", ctypes.c_uint64), ("port", ctypes.c_uint64)]


def _libc():
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    return libc


def abi_version() -> int:
    """The kernel's Landlock ABI (0: not available here)."""
    try:
        libc = _libc()
        v = libc.syscall(SYS_CREATE_RULESET, None, ctypes.c_size_t(0), ctypes.c_uint32(CREATE_RULESET_VERSION))
    except OSError:
        return 0
    return int(v) if v > 0 else 0


def handled(abi: int) -> int:
    rights = (EXECUTE | WRITE_FILE | READ_FILE | READ_DIR | REMOVE_DIR | REMOVE_FILE | MAKE_CHAR | MAKE_DIR |
              MAKE_REG | MAKE_SOCK | MAKE_FIFO | MAKE_BLOCK | MAKE_SYM)
    if abi >= 2:
        rights |= REFER
    if abi >= 3:
        rights |= TRUNCATE
    if abi >= 5:
        rights |= IOCTL_DEV
    return rights


def confine(write: list[str], read: list[str], ports: tuple[int, ...] | None = WEB_PORTS) -> int:
    """Restrict this process: full access beneath `write`, read and run beneath `read`; with ABI 4+ TCP
    connections only to `ports`, with ABI 6+ no signals or abstract sockets outside. Returns the ABI.
    Raises OSError."""
    abi = abi_version()
    if abi < 1:
        raise OSError("Landlock is not available on this system")
    libc = _libc()
    all_rights = handled(abi)
    net = NET_CONNECT_TCP if abi >= 4 and ports is not None else 0
    scoped = SCOPE_ABSTRACT_UNIX_SOCKET | SCOPE_SIGNAL if abi >= 6 else 0
    attr = RulesetAttr(all_rights, net, scoped)
    size = ctypes.sizeof(attr) if abi >= 6 else (16 if abi >= 4 else 8)     # older kernels know fewer fields
    fd = libc.syscall(SYS_CREATE_RULESET, ctypes.byref(attr), ctypes.c_size_t(size), ctypes.c_uint32(0))
    if fd < 0:
        raise OSError(ctypes.get_errno(), "landlock_create_ruleset: " + os.strerror(ctypes.get_errno()))
    try:
        for paths, rights in ((write, all_rights), (read, READ)):
            for path in paths:
                try:
                    pfd = os.open(path, os.O_PATH | os.O_CLOEXEC)
                except OSError:
                    continue                       # not on this system
                try:
                    allowed = rights if os.path.isdir(path) else rights & (EXECUTE | WRITE_FILE | READ_FILE |
                                                                            TRUNCATE | IOCTL_DEV)
                    rule = PathBeneath(allowed & all_rights, pfd)
                    if libc.syscall(SYS_ADD_RULE, ctypes.c_int(fd), ctypes.c_int(RULE_PATH_BENEATH),
                                    ctypes.byref(rule), ctypes.c_uint32(0)) < 0:
                        raise OSError(ctypes.get_errno(), f"landlock_add_rule {path}: " +
                                      os.strerror(ctypes.get_errno()))
                finally:
                    os.close(pfd)
        for port in (ports or ()) if net else ():
            rule = NetPort(NET_CONNECT_TCP, port)
            if libc.syscall(SYS_ADD_RULE, ctypes.c_int(fd), ctypes.c_int(RULE_NET_PORT), ctypes.byref(rule),
                            ctypes.c_uint32(0)) < 0:
                raise OSError(ctypes.get_errno(), f"landlock_add_rule port {port}: " + os.strerror(ctypes.get_errno()))
        if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "prctl(NO_NEW_PRIVS): " + os.strerror(ctypes.get_errno()))
        if libc.syscall(SYS_RESTRICT_SELF, ctypes.c_int(fd), ctypes.c_uint32(0)) < 0:
            raise OSError(ctypes.get_errno(), "landlock_restrict_self: " + os.strerror(ctypes.get_errno()))
    finally:
        os.close(fd)
    return abi


def main(argv: list[str]) -> int:
    if "--" not in argv:
        print("usage: confine.py --root DIR --home DIR [--require] -- COMMAND...", file=sys.stderr)
        return 2
    opts, cmd = argv[:argv.index("--")], argv[argv.index("--") + 1:]
    root = home = ""
    require = "--require" in opts
    for i, a in enumerate(opts):
        if a == "--root" and i + 1 < len(opts):
            root = opts[i + 1]
        elif a == "--home" and i + 1 < len(opts):
            home = opts[i + 1]
    if not root or not cmd:
        print("confine.py: --root and a command are required", file=sys.stderr)
        return 2
    home = home or os.path.join(root, ".hub", "home")
    tmp = os.path.join(root, ".hub", "tmp")
    os.makedirs(home, exist_ok=True)
    os.makedirs(tmp, exist_ok=True)
    try:
        confine([root, home, *DEVICES], list(SYSTEM))
    except OSError as exc:
        if require:
            print(f"The sandbox cannot keep this command inside its folder ({exc}), so it was not run.",
                  file=sys.stderr)
            return 126
    env = dict(os.environ, HOME=home, TMPDIR=tmp, TMP=tmp, TEMP=tmp, XDG_CACHE_HOME=os.path.join(home, ".cache"))
    env.setdefault("PIP_TARGET", os.path.join(home, ".pylib"))       # pip installs where the command may write
    env["PYTHONPATH"] = os.pathsep.join(p for p in (os.path.join(home, ".pylib"), env.get("PYTHONPATH", "")) if p)
    try:
        os.execvpe(cmd[0], cmd, env)
    except OSError as exc:
        print(f"confine.py: {cmd[0]}: {exc}", file=sys.stderr)
        return 127


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
