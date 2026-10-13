"""Search: ripgrep for the Grep/Glob tools, system grep for Bash grep."""

from __future__ import annotations

from ..corpus import NEEDLE, REGEX
from ..registry import bench
from ..util import IS_WINDOWS, now, run

_RG_ENV_FREE = ["--no-config"]


def _search_ms(argv, cwd, repeat: int = 3) -> float:
    """Run a search `repeat` times (output captured like an agent does) and return ms per run."""
    t0 = now()
    for _ in range(repeat):
        proc = run(argv, cwd=cwd, timeout=120)
        if proc.returncode not in (0, 1):  # 1 = no match
            raise RuntimeError("search failed: " + proc.stderr.decode("utf-8", "replace")[:200])
    return (now() - t0) * 1000.0 / repeat


@bench("search.rg_literal", title="rg literal search across the corpus", unit="ms",
       lower_is_better=True, models="Grep tool (Claude Code and Codex bundle ripgrep)",
       requires=("rg",), reps=7)
def rg_literal(ctx):
    (rg,) = ctx.need("rg")
    argv = [rg] + _RG_ENV_FREE + ["-l", NEEDLE, "."]
    return lambda: _search_ms(argv, ctx.corpus.tree)


@bench("search.rg_regex", title="rg regex search with 3,500 matching lines", unit="ms",
       lower_is_better=True, models="Grep tool with a pattern and line output", requires=("rg",),
       reps=7)
def rg_regex(ctx):
    (rg,) = ctx.need("rg")
    argv = [rg] + _RG_ENV_FREE + ["-n", REGEX, "."]
    return lambda: _search_ms(argv, ctx.corpus.tree)


@bench("search.rg_files", title="rg --files (list 6,000 files)", unit="ms", lower_is_better=True,
       models="Glob tool and file pickers", requires=("rg",), reps=7)
def rg_files(ctx):
    (rg,) = ctx.need("rg")
    argv = [rg] + _RG_ENV_FREE + ["--files", "."]
    return lambda: _search_ms(argv, ctx.corpus.tree)


@bench("search.grep", title="grep -rn literal across the corpus", unit="ms", lower_is_better=True,
       models="grep in Bash (12% of shell commands; BSD grep on macOS, GNU elsewhere)",
       requires=("grep",), reps=5)
def grep_recursive(ctx):
    (grep,) = ctx.need("grep")
    argv = [grep, "-rn", NEEDLE, "."]
    if IS_WINDOWS:
        ctx.note("search.grep", impl="Git for Windows grep")
    return lambda: _search_ms(argv, ctx.corpus.tree, repeat=2)
