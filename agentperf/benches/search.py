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


def _validate_lines(argv, cwd, expected: int, what: str) -> int:
    """One untimed run of the tool, checked against the corpus-derived count.

    *expected* comes from reading the corpus files (see
    ``Corpus.expected_search_counts``), never from the tool itself: a
    tool that consistently searches a subset must be rejected here, not
    allowed to define the right answer.
    """
    proc = run(argv, cwd=cwd, timeout=180)
    if proc.returncode != 0:
        raise Skip("{} validation failed (exit {}): {}".format(
            what, proc.returncode, proc.stderr.decode("utf-8", "replace")[:200]))
    count = _line_count(proc.stdout)
    if count != expected:
        raise Skip("{} validation returned {} lines; the corpus contains {} "
                   "(the tool is not searching the whole corpus)".format(
                       what, count, expected))
    return count


def _search_ms(argv, cwd, expected: int, repeat: int = 3) -> float:
    """Run a search `repeat` times (output captured like an agent does) and return ms per run.

    Exit 1 (no matches) is a failure: the corpus is known to match.
    Every repetition's output is captured during timing and its line
    count is checked only after the clock stops, so validation work
    never lands inside a measurement - and a wrong count in any one
    repetition fails the sample, not just the last one.
    """
    t0 = now()
    outputs = []
    for _ in range(repeat):
        proc = run(argv, cwd=cwd, timeout=120)
        if proc.returncode != 0:
            raise RuntimeError("search failed (exit {}): {}".format(
                proc.returncode, proc.stderr.decode("utf-8", "replace")[:200]))
        outputs.append(proc.stdout)
    elapsed = (now() - t0) * 1000.0 / repeat
    for index, out in enumerate(outputs, 1):
        got = _line_count(out)
        if got != expected:
            raise RuntimeError("search repetition {} returned {} lines, expected {} "
                               "(inputs differ from the validated corpus)".format(
                                   index, got, expected))
    return elapsed


@bench("search.rg_literal", title="rg literal search across the corpus", unit="ms",
       lower_is_better=True, models="Grep tool (Claude Code and Codex bundle ripgrep)",
       requires=("rg",), reps=7)
def rg_literal(ctx):
    (rg,) = ctx.need("rg")
    argv = [rg] + _RG_ENV_FREE + ["-l", NEEDLE, "."]
    expected = ctx.corpus.expected_search_counts()["needle_files"]
    _validate_lines(argv, ctx.corpus.tree, expected, "rg literal")
    ctx.note("search.rg_literal", method="rg --no-config --no-ignore", matched_files=expected)
    return lambda: _search_ms(argv, ctx.corpus.tree, expected)


@bench("search.rg_regex", title="rg regex search with matching lines", unit="ms",
       lower_is_better=True, models="Grep tool with a pattern and line output", requires=("rg",),
       reps=7)
def rg_regex(ctx):
    (rg,) = ctx.need("rg")
    argv = [rg] + _RG_ENV_FREE + ["-n", REGEX, "."]
    expected = ctx.corpus.expected_search_counts()["regex_lines"]
    _validate_lines(argv, ctx.corpus.tree, expected, "rg regex")
    ctx.note("search.rg_regex", method="rg --no-config --no-ignore", matched_lines=expected)
    return lambda: _search_ms(argv, ctx.corpus.tree, expected)


@bench("search.rg_files", title="rg --files (list 6,000 files)", unit="ms", lower_is_better=True,
       models="Glob tool and file pickers", requires=("rg",), reps=7)
def rg_files(ctx):
    (rg,) = ctx.need("rg")
    argv = [rg] + _RG_ENV_FREE + ["--files", "."]
    expected = len(ctx.corpus.files)
    _validate_lines(argv, ctx.corpus.tree, expected, "rg --files")
    ctx.note("search.rg_files", method="rg --no-config --no-ignore", matched_files=expected)
    return lambda: _search_ms(argv, ctx.corpus.tree, expected)


@bench("search.grep", title="grep -rn literal across the corpus", unit="ms", lower_is_better=True,
       models="grep in Bash (12% of shell commands; BSD grep on macOS, GNU elsewhere)",
       requires=("grep",), reps=5)
def grep_recursive(ctx):
    (grep,) = ctx.need("grep")
    argv = [grep, "-rn", NEEDLE, "."]
    expected = ctx.corpus.expected_search_counts()["needle_lines"]
    _validate_lines(argv, ctx.corpus.tree, expected, "grep")
    if IS_WINDOWS:
        ctx.note("search.grep", impl="Git for Windows grep", matched_lines=expected)
    else:
        ctx.note("search.grep", matched_lines=expected)
    return lambda: _search_ms(argv, ctx.corpus.tree, expected, repeat=2)
