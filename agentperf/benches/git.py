"""Git: status, diff, commit, log, and worktrees on a 6,000-file repo.

The user's global git config is replaced by an empty file so dotfiles do not
skew results. System config stays, because platform defaults (for example
core.fscache on Git for Windows) are part of what agents really get.
"""

from __future__ import annotations

import os
import shutil

from ..registry import bench
from ..util import now, rmtree, run

DIRTY_COUNT = 100
COMMIT_COUNT = 50
HISTORY = 30
_FLAGS = ["-c", "user.name=agentperf", "-c", "user.email=agentperf@example.invalid",
          "-c", "commit.gpgsign=false", "-c", "gc.auto=0", "-c", "maintenance.auto=false",
          "-c", "init.defaultBranch=main"]


class Repo:
    def __init__(self, ctx, path: str):
        (self.git,) = ctx.need("git")
        self.path = path
        empty = os.path.join(ctx.workdir, "empty.gitconfig")
        if not os.path.exists(empty):
            open(empty, "w").close()
        hooks = os.path.join(ctx.workdir, "no-hooks")
        os.makedirs(hooks, exist_ok=True)
        self.flags = _FLAGS + ["-c", "core.hooksPath=" + hooks]
        self.env = dict(os.environ)
        self.env.update({"GIT_CONFIG_GLOBAL": empty, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C",
                         "GIT_PAGER": "cat", "PAGER": "cat"})
        self.env.pop("GIT_DIR", None)
        self.env.pop("GIT_WORK_TREE", None)

    def __call__(self, *args, cwd=None, extra=()):
        proc = run([self.git] + self.flags + list(extra) + list(args), cwd=cwd or self.path,
                   env=self.env, timeout=300)
        if proc.returncode != 0:
            raise RuntimeError("git {} failed: {}".format(
                args[0], proc.stderr.decode("utf-8", "replace")[:300]))
        return proc.stdout

    def timed(self, *args, **kw) -> float:
        t0 = now()
        self(*args, **kw)
        return (now() - t0) * 1000.0


def _touch(ctx, root: str, rels, tag: str) -> None:
    for rel in rels:
        with open(os.path.join(root, *rel.split("/")), "ab") as fh:
            fh.write("# {}\n".format(tag).encode("ascii"))


def _base_repo(ctx) -> Repo:
    """One shared repo with the corpus committed plus a short history."""
    def make():
        path = os.path.join(ctx.workdir, "repo")
        rmtree(path)
        shutil.copytree(ctx.corpus.tree, path)
        repo = Repo(ctx, path)
        repo("init", "-q")
        repo("add", "-A")
        repo("commit", "-q", "-m", "initial import")
        files = ctx.corpus.files
        for i in range(HISTORY):
            _touch(ctx, path, files[i * 7:i * 7 + 3], "history {}".format(i))
            repo("commit", "-q", "-a", "-m", "change {}".format(i))
        return repo
    return ctx.cached("git-repo", make)


@bench("git.init_commit", title="git init + add + commit of 6,000 files", unit="ms",
       lower_is_better=True, models="First commit of a scaffolded project; hashing and object writes",
       requires=("git",), reps=3, warmup=1, budget_s=40)
def init_commit(ctx):
    base = ctx.scratch("git-init")
    template = Repo(ctx, base)
    counter = [0]

    def sample():
        counter[0] += 1
        path = os.path.join(base, "r{}".format(counter[0]))
        shutil.copytree(ctx.corpus.tree, path)
        t0 = now()
        template("init", "-q", cwd=path)
        template("add", "-A", cwd=path)
        template("commit", "-q", "-m", "init", cwd=path)
        elapsed = (now() - t0) * 1000.0
        rmtree(path)
        return elapsed

    return sample, lambda: rmtree(base)


@bench("git.status", title="git status on a clean 6,000-file repo", unit="ms",
       lower_is_better=True, models="The status check agents run before and after edits",
       requires=("git",), reps=9)
def status(ctx):
    repo = _base_repo(ctx)

    def sample():
        return sum(repo.timed("status", "--porcelain") for _ in range(3)) / 3

    return sample


@bench("git.status_dirty", title="git status with 100 modified files", unit="ms",
       lower_is_better=True, models="Status after an agent edited many files", requires=("git",),
       reps=7)
def status_dirty(ctx):
    repo = _base_repo(ctx)
    rels = ctx.corpus.subset(DIRTY_COUNT)
    _touch(ctx, repo.path, rels, "dirty")

    def sample():
        return sum(repo.timed("status", "--porcelain") for _ in range(3)) / 3

    return sample, lambda: repo("checkout", "-q", "--", ".")


@bench("git.diff", title="git diff of 100 modified files", unit="ms", lower_is_better=True,
       models="Reviewing changes before commit", requires=("git",), reps=7)
def diff(ctx):
    repo = _base_repo(ctx)
    rels = ctx.corpus.subset(DIRTY_COUNT)
    _touch(ctx, repo.path, rels, "diff")

    def sample():
        return sum(repo.timed("diff") for _ in range(3)) / 3

    return sample, lambda: repo("checkout", "-q", "--", ".")


@bench("git.commit", title="git add + commit of 50 changed files", unit="ms",
       lower_is_better=True, models="Agent commits", requires=("git",), reps=5)
def commit(ctx):
    repo = _base_repo(ctx)
    rels = ctx.corpus.subset(COMMIT_COUNT)
    counter = [0]

    def sample():
        counter[0] += 1
        _touch(ctx, repo.path, rels, "commit {}".format(counter[0]))
        t0 = now()
        repo("add", "-A")
        repo("commit", "-q", "-m", "bench {}".format(counter[0]))
        return (now() - t0) * 1000.0

    return sample


@bench("git.log", title="git log (100 one-line + 3 patches)", unit="ms", lower_is_better=True,
       models="History questions", requires=("git",), reps=7)
def log(ctx):
    repo = _base_repo(ctx)

    def sample():
        t0 = now()
        repo("log", "--oneline", "-n", "100")
        repo("log", "-p", "-n", "3")
        return (now() - t0) * 1000.0

    return sample


@bench("git.worktree", title="git worktree add + remove (6,000 files)", unit="ms",
       lower_is_better=True, models="Isolated worktrees for subagents and parallel threads",
       requires=("git",), reps=5, budget_s=40)
def worktree(ctx):
    repo = _base_repo(ctx)
    base = ctx.scratch("git-worktree")
    counter = [0]

    def sample():
        counter[0] += 1
        path = os.path.join(base, "wt{}".format(counter[0]))
        t0 = now()
        repo("worktree", "add", "-q", "--detach", path, "HEAD")
        repo("worktree", "remove", "--force", path)
        return (now() - t0) * 1000.0

    def cleanup():
        repo("worktree", "prune")
        rmtree(base)

    return sample, cleanup


@bench("diag.git_fsmonitor", title="git status with fsmonitor + untracked cache", unit="ms",
       lower_is_better=True, models="What git status costs after enabling the built-in fsmonitor",
       requires=("git",), reps=7, platforms=("macos", "windows"))
def status_fsmonitor(ctx):
    repo = _base_repo(ctx)
    extra = ["-c", "core.fsmonitor=true", "-c", "core.untrackedCache=true"]
    for _ in range(3):  # start the daemon and fill the caches
        repo("status", "--porcelain", extra=extra)

    def sample():
        return sum(repo.timed("status", "--porcelain", extra=extra) for _ in range(3)) / 3

    def cleanup():
        try:
            repo("fsmonitor--daemon", "stop")
        except RuntimeError:
            pass

    return sample, cleanup

