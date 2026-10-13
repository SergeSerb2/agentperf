"""Compute: test runs, builds, and scripts. C-backed work dominates so the Python
version matters little; the one pure-Python test carries a lower weight."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import zlib
from concurrent.futures import ThreadPoolExecutor

from ..registry import bench
from ..util import now, run


def _corpus_text(ctx, limit: int) -> bytes:
    def load():
        chunks, size = [], 0
        for rel in ctx.corpus.files:
            with open(ctx.corpus.path(rel), "rb") as fh:
                data = fh.read()
            chunks.append(data)
            size += len(data)
            if size >= 32 * 1024 * 1024:
                break
        return b"".join(chunks)
    return ctx.cached("corpus-text", load)[:limit]


@bench("compute.sha256", title="SHA-256 of 128 MB (one core)", unit="MB/s", lower_is_better=False,
       models="Content hashing in git, package managers, build caches", reps=5)
def sha256(ctx):
    buf = os.urandom(128 * 1024 * 1024)

    def sample():
        t0 = now()
        hashlib.sha256(buf).digest()
        return 128 / (now() - t0)

    return sample


@bench("compute.zlib", title="zlib compress + decompress 16 MB of code (one core)", unit="MB/s",
       lower_is_better=False, models="Archives, git objects, HTTP bodies", reps=5)
def zlib_roundtrip(ctx):
    text = _corpus_text(ctx, 16 * 1024 * 1024)
    mb = len(text) / (1024.0 * 1024.0)

    def sample():
        t0 = now()
        packed = zlib.compress(text, 6)
        zlib.decompress(packed)
        return mb / (now() - t0)

    return sample


def _json_doc():
    return [{"id": i, "name": "item_{}".format(i * 7919 % 10007), "tags": [i % 7, i % 11, i % 13],
             "score": (i * 31 % 997) / 7.0, "path": "src/module_{}/file_{}.py".format(i % 97, i),
             "ok": i % 3 == 0} for i in range(40000)]


@bench("compute.json", title="JSON dump + parse of a 5 MB document", unit="ms", lower_is_better=True,
       models="Tool results, package manifests, harness event streams", reps=7)
def json_roundtrip(ctx):
    doc = _json_doc()

    def sample():
        t0 = now()
        json.loads(json.dumps(doc))
        return (now() - t0) * 1000.0

    return sample


@bench("compute.python", title="Pure-Python text processing (interpreter speed)", unit="ms",
       lower_is_better=True, models="Local python3 scripts; depends on the installed Python version",
       reps=5, weight=0.5)
def python_work(ctx):
    lines = _corpus_text(ctx, 4 * 1024 * 1024).decode("ascii").splitlines()
    ctx.note("compute.python", python=sys.version.split()[0])

    def sample():
        t0 = now()
        counts = {}
        for line in lines:
            for word in line.replace("(", " ").replace(")", " ").replace(",", " ").split():
                if word.isidentifier():
                    counts[word] = counts.get(word, 0) + 1
        top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:50]
        "".join("{}={};".format(k, v) for k, v in top)
        return (now() - t0) * 1000.0

    return sample


@bench("compute.parallel", title="96 zlib+SHA-256 jobs on all cores", unit="ms", lower_is_better=True,
       models="Parallel test runners, builds, and linters", reps=5, weight=1.5)
def parallel(ctx):
    text = _corpus_text(ctx, 32 * 1024 * 1024)
    chunk = 1024 * 1024
    jobs = [text[(i * chunk) % (len(text) - chunk):][:chunk] for i in range(96)]
    workers = os.cpu_count() or 1
    ctx.note("compute.parallel", workers=workers)
    pool = ThreadPoolExecutor(max_workers=workers)  # zlib and hashlib release the GIL

    def job(data):
        return len(zlib.compress(data, 6)) + len(hashlib.sha256(data).digest())

    def sample():
        t0 = now()
        list(pool.map(job, jobs))
        return (now() - t0) * 1000.0

    return sample, lambda: pool.shutdown()


_NODE_SCRIPT = r"""
const t0 = performance.now();
let acc = 0;
for (let r = 0; r < 12; r++) {
  const objs = [];
  for (let i = 0; i < 20000; i++) {
    objs.push({id: i, name: 'item_' + (i * 7919 % 10007), tags: [i % 7, i % 11, i % 13],
               score: (i * 31 % 997) / 7, path: 'src/module_' + (i % 97) + '/file_' + i + '.ts'});
  }
  const text = JSON.stringify(objs);
  const back = JSON.parse(text);
  back.sort((a, b) => a.score - b.score || a.id - b.id);
  const m = new Map();
  for (const o of back) {
    const k = o.path.replace(/file_(\d+)\.ts$/, (_, d) => 'f' + (Number(d) % 50));
    m.set(k, (m.get(k) || 0) + o.tags.length);
  }
  acc += m.size + text.length % 7;
}
console.log(JSON.stringify({ms: performance.now() - t0, acc}));
"""


@bench("compute.node", title="Node.js JSON/regex/sort workload", unit="ms", lower_is_better=True,
       models="tsc, eslint, jest, bundlers, and Node-based harness work", requires=("node",), reps=5)
def node_work(ctx):
    (node,) = ctx.need("node")

    def sample():
        proc = run([node, "-e", _NODE_SCRIPT], timeout=120)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.decode("utf-8", "replace")[:200])
        return float(json.loads(proc.stdout.decode().strip().splitlines()[-1])["ms"])

    return sample
