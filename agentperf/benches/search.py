"""Search: ripgrep for the Grep/Glob tools, system grep for Bash grep."""

from __future__ import annotations

from ..corpus import NEEDLE, REGEX
from ..registry import Skip, bench
from ..util import IS_WINDOWS, now, run

# --no-ignore: inherited .ignore/.rgignore/git rules (including from parent
# directories) must not change what is searched on different machines.
_RG_ENV_FREE = ["--no-config", "--no-ignore", "--no-require-git"]


def _line_count(data: bytes) -> int:
    if not data:
        return 0
    return data.count(b"\n") + (0 if data.endswith(b"\n") else 1)


def _expected_lines(argv, cwd, what: str) -> int:
    """One untimed run that proves the search finds what the corpus contains."""
    proc = run(argv, cwd=cwd, timeout=180)
    if proc.returncode != 0:
        raise Skip("{} validation failed (exit {}): {}".format(
            what, proc.returncode, proc.stderr.decode("utf-8", "replace")[:200]))
    count = _line_count(proc.stdout)
    if count == 0:
        raise Skip("{} validation found no matches in the corpus".format(what))
    return count


def _search_ms(argv, cwd, expected: int, repeat: int = 3) -> float:
    """Run a search `repeat` times (output captured like an agent does) and return ms per run.

    Exit 1 (no matches) is a failure: the corpus is known to match. The
    match count is checked after timing, from already-captured output.
    """
    t0 = now()
    last = None
    for _ in range(repeat):
        last = run(argv, cwd=cwd, timeout=120)
        if last.returncode != 0:
            raise RuntimeError("search failed (exit {}): {}".format(
                last.returncode, last.stderr.decode("utf-8", "replace")[:200]))
    elapsed = (now() - t0) * 1000.0 / repeat
    got = _line_count(last.stdout if last else b"")
    if got != expected:
        raise RuntimeError("search returned {} lines, expected {} "
                           "(inputs differ from the validated corpus)".format(got, expected))
    return elapsed


@bench("search.rg_literal", title="rg literal search across the corpus", unit="ms",
       lower_is_better=True, models="Grep tool (Claude Code and Codex bundle ripgrep)",
       requires=("rg",), reps=7)
def rg_literal(ctx):
    (rg,) = ctx.need("rg")
    argv = [rg] + _RG_ENV_FREE + ["-l", NEEDLE, "."]
    expected = _expected_lines(argv, ctx.corpus.tree, "rg literal")
    ctx.note("search.rg_literal", method="rg --no-config --no-ignore", matched_files=expected)
    return lambda: _search_ms(argv, ctx.corpus.tree, expected)


@bench("search.rg_regex", title="rg regex search with matching lines", unit="ms",
       lower_is_better=True, models="Grep tool with a pattern and line output", requires=("rg",),
       reps=7)
def rg_regex(ctx):
    (rg,) = ctx.need("rg")
    argv = [rg] + _RG_ENV_FREE + ["-n", REGEX, "."]
    expected = _expected_lines(argv, ctx.corpus.tree, "rg regex")
    ctx.note("search.rg_regex", method="rg --no-config --no-ignore", matched_lines=expected)
    return lambda: _search_ms(argv, ctx.corpus.tree, expected)


@bench("search.rg_files", title="rg --files (list 6,000 files)", unit="ms", lower_is_better=True,
       models="Glob tool and file pickers", requires=("rg",), reps=7)
def rg_files(ctx):
    (rg,) = ctx.need("rg")
    argv = [rg] + _RG_ENV_FREE + ["--files", "."]
    expected = _expected_lines(argv, ctx.corpus.tree, "rg --files")
    if expected != len(ctx.corpus.files):
        raise Skip("rg --files listed {} files, corpus has {}".format(
            expected, len(ctx.corpus.files)))
    ctx.note("search.rg_files", method="rg --no-config --no-ignore", matched_files=expected)
    return lambda: _search_ms(argv, ctx.corpus.tree, expected)


@bench("search.grep", title="grep -rn literal across the corpus", unit="ms", lower_is_better=True,
       models="grep in Bash (12% of shell commands; BSD grep on macOS, GNU elsewhere)",
       requires=("grep",), reps=5)
def grep_recursive(ctx):
    (grep,) = ctx.need("grep")
    argv = [grep, "-rn", NEEDLE, "."]
    expected = _expected_lines(argv, ctx.corpus.tree, "grep")
    if IS_WINDOWS:
        ctx.note("search.grep", impl="Git for Windows grep", matched_lines=expected)
    else:
        ctx.note("search.grep", matched_lines=expected)
    return lambda: _search_ms(argv, ctx.corpus.tree, expected, repeat=2)
