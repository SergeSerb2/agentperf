"""Small-file work: what Read, Write, Edit, Glob, cat, and sed do all day."""

from __future__ import annotations

import os

from ..registry import bench
from ..util import now, rmtree

CREATE_COUNT = 2000
READ_COUNT = 2000
EDIT_COUNT = 300


def _payloads(ctx):
    """2,000 real corpus files held in memory, so writes do not read from disk."""
    def load():
        out = []
        for rel in ctx.corpus.subset(CREATE_COUNT):
            with open(ctx.corpus.path(rel), "rb") as fh:
                out.append(fh.read())
        return out
    return ctx.cached("payloads", load)


def _write_tree(root: str, payloads) -> None:
    dirs = [os.path.join(root, "d{:02d}".format(i)) for i in range(40)]
    for d in dirs:
        os.mkdir(d)
    for i, data in enumerate(payloads):
        with open(os.path.join(dirs[i % 40], "f{:05d}.txt".format(i)), "wb") as fh:
            fh.write(data)


@bench("files.create", title="Create 2,000 small files in 40 folders", unit="ms",
       lower_is_better=True, models="Write tool, code generation, package unpacking",
       ops=CREATE_COUNT, reps=5)
def create(ctx):
    payloads = _payloads(ctx)
    base = ctx.scratch("files-create")
    counter = [0]

    def sample():
        counter[0] += 1
        root = os.path.join(base, "run{}".format(counter[0]))
        os.mkdir(root)
        t0 = now()
        _write_tree(root, payloads)
        elapsed = (now() - t0) * 1000.0
        rmtree(root)
        return elapsed

    return sample, lambda: rmtree(base)


@bench("files.stat_walk", title="Walk and stat 6,000 files", unit="ms", lower_is_better=True,
       models="Glob, ls -R, find, file watchers", reps=7)
def stat_walk(ctx):
    top = ctx.corpus.tree

    def walk(path):
        count = 0
        with os.scandir(path) as it:
            for entry in it:
                if entry.is_dir(follow_symlinks=False):
                    count += walk(entry.path)
                else:
                    entry.stat(follow_symlinks=False)
                    count += 1
        return count

    def sample():
        t0 = now()
        walk(top)
        return (now() - t0) * 1000.0

    return sample


@bench("files.read", title="Read 2,000 files (warm cache)", unit="ms", lower_is_better=True,
       models="Read tool, cat, sed -n (the most common agent action)", ops=READ_COUNT, reps=7)
def read(ctx):
    paths = [ctx.corpus.path(rel) for rel in ctx.corpus.subset(READ_COUNT)]

    def sample():
        t0 = now()
        for p in paths:
            with open(p, "rb") as fh:
                fh.read()
        return (now() - t0) * 1000.0

    return sample


@bench("files.edit", title="300 atomic edits (read, change, write temp, rename)", unit="ms",
       lower_is_better=True, models="Edit tool writes", ops=EDIT_COUNT, reps=5)
def edit(ctx):
    base = ctx.scratch("files-edit")
    payloads = _payloads(ctx)[:EDIT_COUNT]
    paths = []
    for i, data in enumerate(payloads):
        p = os.path.join(base, "f{:04d}.py".format(i))
        with open(p, "wb") as fh:
            fh.write(data)
        paths.append(p)
    flip = [0]

    def sample():
        flip[0] ^= 1
        old, new = (b"return", b"RETURN") if flip[0] else (b"RETURN", b"return")
        t0 = now()
        for p in paths:
            with open(p, "rb") as fh:
                text = fh.read()
            tmp = p + ".tmp"
            with open(tmp, "wb") as fh:
                fh.write(text.replace(old, new, 1))
            os.replace(tmp, p)
        return (now() - t0) * 1000.0

    return sample, lambda: rmtree(base)


@bench("files.delete", title="Delete a 2,000-file tree", unit="ms", lower_is_better=True,
       models="rm -rf node_modules, build dirs, worktree cleanup", reps=5)
def delete(ctx):
    payloads = _payloads(ctx)
    base = ctx.scratch("files-delete")
    counter = [0]

    def sample():
        counter[0] += 1
        root = os.path.join(base, "run{}".format(counter[0]))
        os.mkdir(root)
        _write_tree(root, payloads)
        t0 = now()
        rmtree(root)
        return (now() - t0) * 1000.0

    return sample, lambda: rmtree(base)


@bench("diag.fsync_small", title="Small durable write (4 KB, flushed to the device)", unit="ms",
       lower_is_better=True, models="SQLite commits, lock files, git refs; shows HDD vs SSD latency",
       ops=1, reps=5)
def fsync_small(ctx):
    from .transfer import flush_to_device
    base = ctx.scratch("fsync-small")
    data = os.urandom(4096)
    path = os.path.join(base, "f.bin")

    def sample():
        t0 = now()
        for _ in range(20):
            with open(path, "wb") as fh:
                fh.write(data)
                fh.flush()
                flush_to_device(fh.fileno())
        return (now() - t0) * 1000.0 / 20

    return sample, lambda: rmtree(base)
