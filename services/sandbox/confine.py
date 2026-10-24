#!/usr/bin/env python3
"""Run a command that may touch only one folder, or without the internet (stdlib only).

    confine.py --root DIR --home DIR [--require] [--offline] -- bash -c CMD
    confine.py --offline [--require] -- bash -c CMD

Landlock (Linux 5.13+) limits this process and everything it starts: read and
write beneath --root (its home and its temporary files are kept there too),
read and run the system (/usr, /etc, /opt, /proc, ...), nothing else - no
other chat's or project's folder, not the shared /tmp, /dev/shm or terminals.
On newer kernels it may also connect only to web ports (ABI 4) and not signal
processes outside (ABI 6). No privileges are needed, and the limits cannot be
lifted by the command. Landlock also keeps it from reading other processes'
memory and environment (/proc/<pid>/environ); their command lines stay visible.

--offline takes away the network altogether - with --root as well as the
folder limits, alone with no folder limits (the owner's command while the
assistant has to ask before it goes online): Landlock (ABI 4) refuses every
TCP connection, and a seccomp filter the rest of the ways out.

Landlock's TCP rule sees only connect() on a plain TCP socket. So wherever it
limits connections, a seccomp filter (socket_filter) also refuses what goes
around it: sockets of other families and protocols (MPTCP - which talks to
any TCP server - SCTP, vsock, ...), TCP Fast Open (a connection opened by
sendto/sendmsg, without connect), io_uring (which makes sockets and sends
without those system calls) and the other system call tables (x32, 32-bit).
Offline, UDP sockets go too, so no DNS lookup or QUIC either; a person's
online command keeps UDP (DNS needs it; the VM's firewall still keeps it off
the LAN). What stays: Unix sockets and netlink. Being in a Landlock domain,
the command also cannot ptrace or read the memory of a process outside it.

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


# seccomp: the ways to a connection that Landlock's TCP rule does not see (socket_filter).
AUDIT_ARCH = {"x86_64": 0xC000003E, "aarch64": 0xC00000B7}
SYSCALL_NR = {"x86_64": {"socket": 41, "sendto": 44, "sendmsg": 46, "sendmmsg": 307},
              "aarch64": {"socket": 198, "sendto": 206, "sendmsg": 211, "sendmmsg": 269}}
IO_URING = (425, 426, 427)                      # io_uring_setup, _enter, _register (the same on both)
X32_BIT = 0x40000000
AF_UNIX, AF_INET, AF_INET6, AF_NETLINK = 1, 2, 10, 16
SOCK_STREAM, SOCK_DGRAM, SOCK_TYPE_MASK = 1, 2, 0xF
IPPROTO_TCP, IPPROTO_UDP = 6, 17
MSG_FASTOPEN = 0x20000000
PR_SET_SECCOMP, SECCOMP_MODE_FILTER = 22, 2
RET_ALLOW, RET_ERRNO = 0x7FFF0000, 0x00050000
EPERM, EACCES, ENOSYS = 1, 13, 38
LD_ABS, JEQ, JGE, JSET, AND, RET = 0x20, 0x15, 0x35, 0x45, 0x54, 0x06   # BPF_LD|W|ABS, BPF_JMP|..|K, ALU, RET


class SockFilter(ctypes.Structure):
    _fields_ = [("code", ctypes.c_uint16), ("jt", ctypes.c_uint8), ("jf", ctypes.c_uint8), ("k", ctypes.c_uint32)]


class SockFprog(ctypes.Structure):
    _fields_ = [("len", ctypes.c_uint16), ("filter", ctypes.POINTER(SockFilter))]


def _arg(i: int) -> int:
    """Offset of the low 32 bits of system call argument i in seccomp_data (little-endian)."""
    return 16 + 8 * i


def socket_program(machine: str, offline: bool) -> list[tuple[int, int, int, int]]:
    """The BPF program socket_filter installs, as (code, jt, jf, k). Offline: sockets only for Unix, netlink and
    TCP (whose connections Landlock refuses); online (a person's command, ports limited): UDP as well."""
    if machine not in AUDIT_ARCH:
        raise OSError(f"no seccomp filter for this machine ({machine})")
    nr = SYSCALL_NR[machine]
    prog: list = [("ld", 4), ("jeq", AUDIT_ARCH[machine], None, "nosys"), ("ld", 0)]
    if machine == "x86_64":
        prog.append(("jge", X32_BIT, "nosys", None))
    prog += [("jeq", n, "eperm", None) for n in IO_URING]
    prog += [("jeq", nr["socket"], "socket", None), ("jeq", nr["sendto"], "flags3", None),
             ("jeq", nr["sendmsg"], "flags2", None), ("jeq", nr["sendmmsg"], "flags3", None), ("ret", RET_ALLOW),
             "flags3", ("ld", _arg(3)), ("jset", MSG_FASTOPEN, "eacces", "allow"),
             "flags2", ("ld", _arg(2)), ("jset", MSG_FASTOPEN, "eacces", "allow"),
             "socket", ("ld", _arg(0)), ("jeq", AF_UNIX, "allow", None), ("jeq", AF_NETLINK, "allow", None),
             ("jeq", AF_INET, "inet", None), ("jeq", AF_INET6, "inet", "eacces"),
             "inet", ("ld", _arg(1)), ("and", SOCK_TYPE_MASK), ("jeq", SOCK_STREAM, "stream", None)]
    prog += [("ret", RET_ERRNO | EACCES)] if offline else [("jeq", SOCK_DGRAM, "dgram", "eacces")]
    prog += ["stream", ("ld", _arg(2)), ("jeq", 0, "allow", None), ("jeq", IPPROTO_TCP, "allow", "eacces")]
    if not offline:
        prog += ["dgram", ("ld", _arg(2)), ("jeq", 0, "allow", None), ("jeq", IPPROTO_UDP, "allow", "eacces")]
    prog += ["allow", ("ret", RET_ALLOW), "eacces", ("ret", RET_ERRNO | EACCES), "eperm", ("ret", RET_ERRNO | EPERM),
             "nosys", ("ret", RET_ERRNO | ENOSYS)]
    at, n = {}, 0                                     # labels -> instruction index
    for ins in prog:
        if isinstance(ins, str):
            at[ins] = n
        else:
            n += 1
    out, i = [], 0
    for ins in prog:
        if isinstance(ins, str):
            continue
        op, k = ins[0], ins[1]
        if op in ("jeq", "jge", "jset"):
            jt = at[ins[2]] - i - 1 if ins[2] else 0
            jf = at[ins[3]] - i - 1 if ins[3] else 0
            if not (0 <= jt <= 255 and 0 <= jf <= 255):
                raise OSError("seccomp program: a jump out of range")
            out.append(({"jeq": JEQ, "jge": JGE, "jset": JSET}[op], jt, jf, k))
        else:
            out.append(({"ld": LD_ABS, "and": AND, "ret": RET}[op], 0, 0, k))
        i += 1
    return out


def socket_filter(offline: bool) -> None:
    """Install the seccomp filter (see the module's text) on this process and everything it starts. Raises
    OSError."""
    prog = socket_program(os.uname().machine, offline)
    arr = (SockFilter * len(prog))(*[SockFilter(*ins) for ins in prog])
    fprog = SockFprog(len(prog), ctypes.cast(arr, ctypes.POINTER(SockFilter)))
    libc = _libc()
    if libc.prctl(ctypes.c_int(PR_SET_NO_NEW_PRIVS), ctypes.c_ulong(1), ctypes.c_ulong(0), ctypes.c_ulong(0),
                  ctypes.c_ulong(0)) != 0:
        raise OSError(ctypes.get_errno(), "prctl(NO_NEW_PRIVS): " + os.strerror(ctypes.get_errno()))
    if libc.prctl(ctypes.c_int(PR_SET_SECCOMP), ctypes.c_ulong(SECCOMP_MODE_FILTER), ctypes.byref(fprog),
                  ctypes.c_ulong(0), ctypes.c_ulong(0)) != 0:
        raise OSError(ctypes.get_errno(), "prctl(SECCOMP): " + os.strerror(ctypes.get_errno()))


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


def confine(write: list[str], read: list[str], ports: tuple[int, ...] | None = WEB_PORTS, files: bool = True,
            need_net: bool = False) -> int:
    """Restrict this process: full access beneath `write`, read and run beneath `read` (files=False: no file
    limits at all); with ABI 4+ TCP connections only to `ports` (an empty tuple: none), with ABI 6+ no signals or
    abstract sockets outside. need_net: refuse (OSError) where the TCP rule cannot be enforced. Returns the ABI.
    Raises OSError."""
    abi = abi_version()
    if abi < 1:
        raise OSError("Landlock is not available on this system")
    if need_net and abi < 4:
        raise OSError(f"this kernel's Landlock (ABI {abi}) cannot limit network connections (ABI 4 can)")
    libc = _libc()
    all_rights = handled(abi) if files else 0
    net = NET_CONNECT_TCP if abi >= 4 and ports is not None else 0
    scoped = SCOPE_ABSTRACT_UNIX_SOCKET | SCOPE_SIGNAL if abi >= 6 and files else 0
    attr = RulesetAttr(all_rights, net, scoped)
    size = ctypes.sizeof(attr) if abi >= 6 else (16 if abi >= 4 else 8)     # older kernels know fewer fields
    fd = libc.syscall(SYS_CREATE_RULESET, ctypes.byref(attr), ctypes.c_size_t(size), ctypes.c_uint32(0))
    if fd < 0:
        raise OSError(ctypes.get_errno(), "landlock_create_ruleset: " + os.strerror(ctypes.get_errno()))
    try:
        for paths, rights in ((write, all_rights), (read, READ)) if files else ():
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
    if net:                                          # and the ways around the TCP rule: required with it
        socket_filter(offline=not ports)
    return abi


def main(argv: list[str]) -> int:
    if "--" not in argv:
        print("usage: confine.py --root DIR --home DIR [--require] -- COMMAND...", file=sys.stderr)
        return 2
    opts, cmd = argv[:argv.index("--")], argv[argv.index("--") + 1:]
    root = home = ""
    require = "--require" in opts
    offline = "--offline" in opts
    for i, a in enumerate(opts):
        if a == "--root" and i + 1 < len(opts):
            root = opts[i + 1]
        elif a == "--home" and i + 1 < len(opts):
            home = opts[i + 1]
    if offline and not root and cmd:                 # no folder limits: only the network goes
        try:
            confine([], [], ports=(), files=False, need_net=True)
        except OSError as exc:
            if require:
                print(f"The sandbox cannot keep this command off the internet ({exc}), so it was not run.",
                      file=sys.stderr)
                return 126
        try:
            os.execvp(cmd[0], cmd)
        except OSError as exc:
            print(f"confine.py: {cmd[0]}: {exc}", file=sys.stderr)
            return 127
    if not root or not cmd:
        print("confine.py: --root and a command are required", file=sys.stderr)
        return 2
    home = home or os.path.join(root, ".hub", "home")
    tmp = os.path.join(root, ".hub", "tmp")
    os.makedirs(home, exist_ok=True)
    os.makedirs(tmp, exist_ok=True)
    try:
        confine([root, home, *DEVICES], list(SYSTEM), ports=() if offline else WEB_PORTS, need_net=offline)
    except OSError as exc:
        if require:
            print(f"The sandbox cannot keep this command inside its folder{' and off the internet' if offline else ''}"
                  f" ({exc}), so it was not run.", file=sys.stderr)
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
