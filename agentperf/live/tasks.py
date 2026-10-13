"""Task definitions, prompts, and verifiers for ``agentperf live``."""
from __future__ import annotations

import os
import re
import subprocess
import sys

_RULES = (" Work only inside this repository. Do not install anything. "
          "Do not ask questions; finish the task, then stop.")

PROMPTS = {
    "fix": (
        "The tests in tests/test_sequences.py fail. Find the bug in the "
        "calcbox package and fix the source code so that `python -m unittest` "
        "passes. Do not edit any test file." + _RULES
    ),
    "rename": (
        "Rename the function `format_label` in calcbox/labels.py to "
        "`build_label`. Update every file that imports or calls it, including "
        "tests. After your change, `python -m unittest` must pass and no "
        "occurrence of the old name `format_label` may remain." + _RULES
    ),
    "feature": (
        "Add a function `clamp(value, low, high)` to calcbox/numbers.py. It "
        "returns value limited to the range low..high: low if value < low, "
        "high if value > high, else value. Also add one test for it in "
        "tests/test_numbers.py. `python -m unittest` must pass." + _RULES
    ),
}

TASK_NAMES = ["fix", "rename", "feature"]

_HIDDEN_TEST = '''import unittest

from calcbox.numbers import clamp


class ClampHiddenTests(unittest.TestCase):
    def test_below(self):
        self.assertEqual(clamp(-5, 0, 10), 0)

    def test_above(self):
        self.assertEqual(clamp(99, 0, 10), 10)

    def test_inside(self):
        self.assertEqual(clamp(4, 0, 10), 4)

    def test_bounds(self):
        self.assertEqual(clamp(0, 0, 10), 0)
        self.assertEqual(clamp(10, 0, 10), 10)

    def test_floats(self):
        self.assertEqual(clamp(1.5, 2.0, 3.0), 2.0)


if __name__ == "__main__":
    unittest.main()
'''


def _run_tests(repo, timeout=120):
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
        cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        encoding="utf-8", errors="replace", timeout=timeout)
    tail = (proc.stdout or "").strip().splitlines()
    return proc.returncode == 0, (tail[-1] if tail else "")


def _git_changed(repo, pathspec=None):
    cmd = ["git", "-C", repo, "status", "--porcelain"]
    if pathspec:
        cmd += ["--", pathspec]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          encoding="utf-8", errors="replace")
    paths = [line[3:] for line in proc.stdout.splitlines() if line.strip()]
    # Running the tests writes bytecode caches; they are not edits.
    return [p for p in paths if "__pycache__" not in p and not p.endswith(".pyc")]


def _verify_fix(repo):
    ok, detail = _run_tests(repo)
    if not ok:
        return False, "repo tests fail: " + detail
    test_edits = _git_changed(repo, "tests")
    if test_edits:
        return False, "test files were edited: %s" % ", ".join(test_edits)
    if not _git_changed(repo):
        return False, "no source change made"
    return True, "tests pass, no test edits"


def _verify_rename(repo):
    ok, detail = _run_tests(repo)
    if not ok:
        return False, "repo tests fail: " + detail
    hits = []
    for dirpath, dirnames, filenames in os.walk(repo):
        if ".git" in dirnames:
            dirnames.remove(".git")
        for name in filenames:
            if name.endswith(".py"):
                path = os.path.join(dirpath, name)
                with open(path, encoding="utf-8", errors="replace") as fh:
                    if re.search(r"\bformat_label\b", fh.read()):
                        hits.append(os.path.relpath(path, repo))
    if hits:
        return False, "old name remains in: %s" % ", ".join(sorted(hits))
    return True, "tests pass, old name gone"


def _verify_feature(repo):
    hidden = os.path.join(repo, "tests", "test_hidden_clamp.py")
    with open(hidden, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(_HIDDEN_TEST)
    try:
        ok, detail = _run_tests(repo)
    finally:
        try:
            os.remove(hidden)
        except OSError:
            pass
    if not ok:
        return False, "tests (incl. hidden) fail: " + detail
    return True, "repo and hidden tests pass"


VERIFIERS = {"fix": _verify_fix, "rename": _verify_rename, "feature": _verify_feature}


def verify(task, repo):
    """Return (success, detail) for *task* in *repo*."""
    try:
        return VERIFIERS[task](repo)
    except Exception as exc:  # verifier must never crash a run
        return False, "verifier error: %s: %s" % (type(exc).__name__, exc)
