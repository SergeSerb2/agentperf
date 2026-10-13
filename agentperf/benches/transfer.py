"""File transfer: sequential disk throughput, big and many-file copies, archive extraction."""

from __future__ import annotations

import gzip
import io
import os
import shutil
import tarfile

from ..registry import Skip, bench
from ..util import IS_LINUX, IS_MAC, IS_WINDOWS, now, rmtree, run

BIG_MB = 256
CHUNK = 4 * 1024 * 1024
F_NOCACHE = 48     # macOS fcntl: bypass the unified buffer cache for this descriptor
F_FULLFSYNC = 51   # macOS fcntl: flush the drive's own write cache too


def _block():
    # Incompressible data so file systems with compression cannot cheat.
    return os.urandom(CHUNK)


def flush_to_device(fd: int, full: bool = True) -> None:
    """Make written data durable. On macOS, plain fsync() stops at the drive's cache;
    F_FULLFSYNC matches what fsync() does on Linux and FlushFileBuffers on Windows."""
    if full and IS_MAC:
        import fcntl
        fcntl.fcntl(fd, F_FULLFSYNC)
    else:
        os.fsync(fd)


def _write_big(path: str, block: bytes, nocache: bool = False, full_flush: bool = True) -> float:
    """Write BIG_MB and flush to the device. Returns seconds."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o644)
    try:
        if nocache and IS_MAC:
            import fcntl
            fcntl.fcntl(fd, F_NOCACHE, 1)
        t0 = now()
        for _ in range(BIG_MB * 1024 * 1024 // CHUNK):
            os.write(fd, block)
        flush_to_device(fd, full_flush)
        return now() - t0
    finally:
        os.close(fd)


@bench("transfer.write", title="Sequential write 256 MB, flushed to the device", unit="MB/s",
       lower_is_better=False, models="Writing build artifacts, model weights, large logs", reps=7,
       budget_s=25)
def seq_write(ctx):
    base = ctx.scratch("xfer-write")
    block = _block()
    path = os.path.join(base, "big.bin")

    def sample():
        secs = _write_big(path, block)
        os.remove(path)
        return BIG_MB / secs

    return sample, lambda: rmtree(base)


@bench("diag.write_fsync", title="Sequential write 256 MB + plain fsync (macOS)", unit="MB/s",
       lower_is_better=False, models="What most macOS programs get: fsync stops at the drive cache",
       reps=5, platforms=("macos",))
def seq_write_plain(ctx):
    base = ctx.scratch("xfer-fsync")
    block = _block()
    path = os.path.join(base, "big.bin")

    def sample():
        secs = _write_big(path, block, full_flush=False)
        os.remove(path)
        return BIG_MB / secs

    return sample, lambda: rmtree(base)


# ---------------------------------------------------------------- cold read

def _read_nocache_mac(path: str) -> float:
    import fcntl
    fd = os.open(path, os.O_RDONLY)
    try:
        fcntl.fcntl(fd, F_NOCACHE, 1)
        t0 = now()
        while os.read(fd, CHUNK):
            pass
        return now() - t0
    finally:
        os.close(fd)


def _read_fadvise_linux(path: str) -> float:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
        t0 = now()
        while os.read(fd, CHUNK):
            pass
        return now() - t0
    finally:
        os.close(fd)


def _read_unbuffered_windows(path: str) -> float:
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    k32.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                             ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    k32.VirtualAlloc.restype = ctypes.c_void_p
    k32.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD, wintypes.DWORD]
    k32.VirtualFree.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    GENERIC_READ, FILE_SHARE_READ, OPEN_EXISTING = 0x80000000, 1, 3
    FILE_FLAG_NO_BUFFERING, FILE_FLAG_SEQUENTIAL_SCAN = 0x20000000, 0x08000000
    handle = k32.CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, None, OPEN_EXISTING,
                             FILE_FLAG_NO_BUFFERING | FILE_FLAG_SEQUENTIAL_SCAN, None)
    if handle in (None, ctypes.c_void_p(-1).value):
        raise OSError(ctypes.get_last_error(), "CreateFileW failed")
    buf = k32.VirtualAlloc(None, CHUNK, 0x3000, 0x04)  # MEM_COMMIT|MEM_RESERVE, PAGE_READWRITE
    try:
        got = wintypes.DWORD(0)
        t0 = now()
        while True:
            if not k32.ReadFile(handle, buf, CHUNK, ctypes.byref(got), None):
                raise OSError(ctypes.get_last_error(), "ReadFile failed")
            if got.value == 0:
                break
        return now() - t0
    finally:
        k32.VirtualFree(buf, 0, 0x8000)  # MEM_RELEASE
        k32.CloseHandle(handle)


@bench("transfer.read", title="Sequential read 256 MB, cache bypassed", unit="MB/s",
       lower_is_better=False, models="First read of a fresh clone, model files, or a cold cache",
       reps=5, budget_s=25)
def seq_read(ctx):
    base = ctx.scratch("xfer-read")
    path = os.path.join(base, "big.bin")
    if IS_MAC:
        method, reader = "F_NOCACHE", _read_nocache_mac
        _write_big(path, _block(), nocache=True)
    elif IS_LINUX and hasattr(os, "posix_fadvise"):
        method, reader = "posix_fadvise(DONTNEED)", _read_fadvise_linux
        _write_big(path, _block())
    elif IS_WINDOWS:
        method, reader = "FILE_FLAG_NO_BUFFERING", _read_unbuffered_windows
        _write_big(path, _block())
    else:
        raise Skip("no cache-bypass method on this platform")
    ctx.note("transfer.read", method=method)
    return (lambda: BIG_MB / reader(path)), lambda: rmtree(base)


@bench("transfer.copy", title="Copy a 256 MB file, flushed to the device", unit="MB/s",
       lower_is_better=False, models="cp of binaries, datasets, caches", reps=7, budget_s=25)
def copy_big(ctx):
    base = ctx.scratch("xfer-copy")
    src = os.path.join(base, "src.bin")
    dst = os.path.join(base, "dst.bin")
    _write_big(src, _block())

    def sample():
        t0 = now()
        shutil.copyfile(src, dst)
        fd = os.open(dst, os.O_RDWR | getattr(os, "O_BINARY", 0))
        try:
            flush_to_device(fd)
        finally:
            os.close(fd)
        secs = now() - t0
        os.remove(dst)
        return BIG_MB / secs

    return sample, lambda: rmtree(base)


@bench("transfer.copy_tree", title="Copy the 6,000-file corpus (49 MB)", unit="MB/s",
       lower_is_better=False, models="Copying repos, caches, fixtures; worktree-style duplication",
       reps=5, budget_s=30)
def copy_tree(ctx):
    base = ctx.scratch("xfer-tree")
    mb = ctx.corpus.total_bytes / (1024.0 * 1024.0)
    counter = [0]

    def sample():
        counter[0] += 1
        dst = os.path.join(base, "copy{}".format(counter[0]))
        t0 = now()
        shutil.copytree(ctx.corpus.tree, dst)
        secs = now() - t0
        rmtree(dst)
        return mb / secs

    return sample, lambda: rmtree(base)


def _corpus_targz(ctx) -> str:
    """A reproducible .tar.gz of the corpus (fixed order, mtimes, and owners)."""
    path = os.path.join(ctx.workdir, "corpus.tar.gz")
    if os.path.exists(path):
        return path
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.USTAR_FORMAT) as tf:
        for rel in ctx.corpus.files:
            with open(ctx.corpus.path(rel), "rb") as fh:
                data = fh.read()
            info = tarfile.TarInfo("corpus/" + rel)
            info.size, info.mtime, info.mode = len(data), 1700000000, 0o644
            tf.addfile(info, io.BytesIO(data))
    with open(path + ".part", "wb") as out:
        with gzip.GzipFile(fileobj=out, mode="wb", compresslevel=6, mtime=0) as gz:
            gz.write(raw.getvalue())
    os.replace(path + ".part", path)
    return path


@bench("transfer.extract", title="Extract corpus .tar.gz with system tar", unit="MB/s",
       lower_is_better=False, models="npm/pip/cargo package unpacking", requires=("tar",), reps=5,
       budget_s=30)
def extract(ctx):
    (tar,) = ctx.need("tar")
    archive = _corpus_targz(ctx)
    base = ctx.scratch("xfer-extract")
    mb = ctx.corpus.total_bytes / (1024.0 * 1024.0)
    counter = [0]

    def sample():
        counter[0] += 1
        dst = os.path.join(base, "x{}".format(counter[0]))
        os.mkdir(dst)
        t0 = now()
        proc = run([tar, "-xzf", archive, "-C", dst], timeout=300)
        secs = now() - t0
        if proc.returncode != 0:
            raise RuntimeError("tar failed: " + proc.stderr.decode("utf-8", "replace")[:200])
        rmtree(dst)
        return mb / secs

    return sample, lambda: rmtree(base)


@bench("diag.clone_copy", title="Copy-on-write clone of 256 MB (cp -c / --reflink)", unit="ms",
       lower_is_better=True, models="APFS/btrfs/XFS clones make worktrees and copies nearly free",
       reps=3, platforms=("macos", "linux", "wsl"))
def clone_copy(ctx):
    base = ctx.scratch("xfer-clone")
    src = os.path.join(base, "src.bin")
    dst = os.path.join(base, "dst.bin")
    _write_big(src, _block())
    argv = ["cp", "-c", src, dst] if IS_MAC else ["cp", "--reflink=always", src, dst]
    probe = run(argv, timeout=60)
    if probe.returncode != 0:
        rmtree(base)
        raise Skip("file system has no copy-on-write clones")
    os.remove(dst)

    def sample():
        t0 = now()
        run(argv, timeout=60, check=True)
        elapsed = (now() - t0) * 1000.0
        os.remove(dst)
        return elapsed

    return sample, lambda: rmtree(base)
