"""Task definitions, prompts, and verifiers for ``agentperf live``."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile

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


# The fixture ships 8 tests (3 sequences + 2 labels + 2 numbers + 1 consumer).
# Verifiers require at least that many to actually execute, so an empty
# suite (for example via a load_tests hook) can never read as success.
FIXTURE_TEST_COUNT = 8
HIDDEN_TEST_COUNT = 5

# Verification runs the agent's code, so nothing it prints can be
# trusted: a test that prints "Ran 99 tests" must not set the count.
# This wrapper lives outside the repo, imports the real unittest before
# the repo joins sys.path (appended, and only after the stdlib import),
# runs the suite through the programmatic API, and writes the count to
# a JSON file the verifier reads. It is launched with `python -I`, so
# an inherited PYTHONPATH (and any shadow `unittest` module on it) is
# ignored entirely.
_RUNNER = '''import json
import sys
import unittest

mode, repo, result_path = sys.argv[1], sys.argv[2], sys.argv[3]
hidden = sys.argv[4] if len(sys.argv) > 4 else ""
sys.argv = [sys.argv[0]]
sys.path = [p for p in sys.path if p not in ("", ".")]
if repo not in sys.path:
    sys.path.append(repo)
loader = unittest.TestLoader()
if mode == "hidden":
    import importlib.util
    spec = importlib.util.spec_from_file_location("agentperf_hidden", hidden)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    suite = loader.loadTestsFromModule(module)
else:
    suite = loader.discover(start_dir=repo + "/tests", top_level_dir=repo)
import io
stream = io.StringIO()
result = unittest.TextTestRunner(stream=stream, verbosity=1).run(suite)
with open(result_path, "w", encoding="utf-8") as fh:
    json.dump({"ran": result.testsRun, "ok": result.wasSuccessful(),
               "tail": stream.getvalue()[-400:]}, fh)
'''


def _run_suite(repo, mode, timeout, hidden_path=""):
    """Run a suite via the outside-repo wrapper; return (ran, ok, tail).

    Counts come from the wrapper's JSON result file, never from text
    the tested code printed.
    """
    tmpdir = tempfile.mkdtemp(prefix="agentperf-verify-")
    wrapper = os.path.join(tmpdir, "run_suite.py")
    result_path = os.path.join(tmpdir, "result.json")
    try:
        with open(wrapper, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(_RUNNER)
        env = {k: v for k, v in os.environ.items()
               if k not in ("PYTHONPATH", "PYTHONSTARTUP", "PYTHONHOME")}
        argv = [sys.executable, "-I", wrapper, mode, os.path.abspath(repo),
                result_path]
        if hidden_path:
            argv.append(hidden_path)
        proc = subprocess.run(
            argv, cwd=tmpdir, env=env, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, encoding="utf-8", errors="replace",
            timeout=timeout)
        try:
            import json as _json
            with open(result_path, encoding="utf-8") as fh:
                data = _json.load(fh)
            return int(data["ran"]), bool(data["ok"]), str(data.get("tail") or "")
        except (OSError, ValueError, KeyError, TypeError):
            tail = (proc.stdout or "").strip().splitlines()
            return 0, False, (tail[-1] if tail else "verifier produced no result")
    finally:
        for name in ("run_suite.py", "result.json"):
            try:
                os.remove(os.path.join(tmpdir, name))
            except OSError:
                pass
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass


def _run_tests(repo, timeout=120, min_tests=FIXTURE_TEST_COUNT):
    ran, ok, tail = _run_suite(repo, "discover", timeout)
    detail = tail.strip().splitlines()[-1] if tail.strip() else ""
    if ok and ran < min_tests:
        return False, "only {} of at least {} expected tests ran".format(ran, min_tests)
    return ok, detail


def _run_hidden(repo, timeout=120):
    """Run the hidden tests from outside the repo's tests package.

    The agent controls the repo, including unittest discovery hooks and
    any module that could shadow the stdlib. The hidden file lives in a
    fresh temp directory and runs through the isolated wrapper above,
    importing only the package under test."""
    tmpdir = tempfile.mkdtemp(prefix="agentperf-hidden-")
    hidden = os.path.join(tmpdir, "test_hidden_clamp.py")
    try:
        with open(hidden, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(_HIDDEN_TEST)
        ran, ok, tail = _run_suite(repo, "hidden", timeout, hidden)
        detail = tail.strip().splitlines()[-1] if tail.strip() else ""
        if ok and ran != HIDDEN_TEST_COUNT:
            return False, "hidden suite ran {} tests, expected {}".format(ran, HIDDEN_TEST_COUNT)
        return ok, detail
    finally:
        try:
            os.remove(hidden)
            os.rmdir(tmpdir)
        except OSError:
            pass


def _git_changed(repo, pathspec=None):
    cmd = ["git", "-C", repo, "status", "--porcelain"]
    if pathspec:
        cmd += ["--", pathspec]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          encoding="utf-8", errors="replace", timeout=60)
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
    ok, detail = _run_tests(repo)
    if not ok:
        return False, "repo tests fail: " + detail
    ok, detail = _run_hidden(repo)
    if not ok:
        return False, "hidden tests fail: " + detail
    return True, "repo and hidden tests pass"


VERIFIERS = {"fix": _verify_fix, "rename": _verify_rename, "feature": _verify_feature}


def verify(task, repo):
    """Return (success, detail) for *task* in *repo*."""
    try:
        return VERIFIERS[task](repo)
    except Exception as exc:  # verifier must never crash a run
        return False, "verifier error: %s: %s" % (type(exc).__name__, exc)
