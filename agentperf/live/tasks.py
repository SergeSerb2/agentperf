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

# The hidden check never runs trusted assertions in the interpreter
# that executes agent code: imported agent code can (and did) patch
# the real unittest classes and record fake successes. Instead a
# subprocess probe only *calls* the agent's function and emits raw
# outputs; the expected values are computed and compared here, in the
# trusted parent, which never imports the repo.
_PROBE = '''import json
import sys

repo, cases_path, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
sys.argv = [sys.argv[0]]
sys.path = [p for p in sys.path if p not in ("", ".")]
if repo not in sys.path:
    sys.path.append(repo)
with open(cases_path, encoding="utf-8") as fh:
    cases = json.load(fh)
from calcbox.numbers import clamp
out = []
for value, low, high in cases:
    try:
        result = clamp(value, low, high)
    except Exception as exc:
        out.append({"error": type(exc).__name__})
    else:
        if isinstance(result, bool) or not isinstance(result, (int, float)):
            out.append({"error": "returned non-number %r" % (result,)})
        else:
            out.append(result)
with open(out_path, "w", encoding="utf-8") as fh:
    json.dump(out, fh)
'''


# The fixture ships 8 tests (3 sequences + 2 labels + 2 numbers + 1 consumer).
# Verifiers require at least that many to actually execute, so an empty
# suite (for example via a load_tests hook) can never read as success.
FIXTURE_TEST_COUNT = 8

# Repo-suite verification runs the agent's code, so nothing it prints
# can be trusted: a test that prints "Ran 99 tests" must not set the
# count. This wrapper lives outside the repo, imports the real
# unittest before the repo joins sys.path (appended, and only after
# the stdlib import), and snapshots the unittest entry points before
# any repo module loads, restoring them before the run: agent code
# that patches TestCase.run at import time cannot keep the patch. It
# writes counts to a JSON file the verifier reads, and the verifier
# requires a zero process exit status on top. It is launched with
# `python -I`, so an inherited PYTHONPATH (and any shadow `unittest`
# module on it) is ignored entirely.
_RUNNER = '''import json
import sys
import unittest

mode, repo, result_path = sys.argv[1], sys.argv[2], sys.argv[3]
sys.argv = [sys.argv[0]]
_saved = {
    "TestCase.run": unittest.TestCase.run,
    "TestResult.addSuccess": unittest.TestResult.addSuccess,
    "TestResult.addFailure": unittest.TestResult.addFailure,
    "TestResult.addError": unittest.TestResult.addError,
    "TextTestRunner.run": unittest.TextTestRunner.run,
}
sys.path = [p for p in sys.path if p not in ("", ".")]
if repo not in sys.path:
    sys.path.append(repo)
loader = unittest.TestLoader()
suite = loader.discover(start_dir=repo + "/tests", top_level_dir=repo)
unittest.TestCase.run = _saved["TestCase.run"]
unittest.TestResult.addSuccess = _saved["TestResult.addSuccess"]
unittest.TestResult.addFailure = _saved["TestResult.addFailure"]
unittest.TestResult.addError = _saved["TestResult.addError"]
unittest.TextTestRunner.run = _saved["TextTestRunner.run"]
import io
stream = io.StringIO()
result = unittest.TextTestRunner(stream=stream, verbosity=1).run(suite)
with open(result_path, "w", encoding="utf-8") as fh:
    json.dump({"ran": result.testsRun, "ok": result.wasSuccessful(),
               "failures": len(result.failures), "errors": len(result.errors),
               "tail": stream.getvalue()[-400:]}, fh)
'''


def _isolated_env():
    return {k: v for k, v in os.environ.items()
            if k not in ("PYTHONPATH", "PYTHONSTARTUP", "PYTHONHOME")}


def _run_guarded(argv, cwd, timeout):
    """subprocess.run equivalent that owns the child's process group.

    Verifier subprocesses execute agent code too: anything they spawn
    is cleaned up with the group, on completion and on timeout, the
    same discipline the harness gets. Returns (returncode, stdout);
    raises subprocess.TimeoutExpired after cleanup on timeout."""
    from .harness import _cleanup_group
    proc = subprocess.Popen(
        argv, cwd=cwd, env=_isolated_env(), stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, encoding="utf-8", errors="replace",
        start_new_session=(os.name != "nt"))
    try:
        out, _none = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _cleanup_group(proc)
        raise
    _cleanup_group(proc)
    return proc.returncode, out or ""


def _run_suite(repo, mode, timeout):
    """Run a suite via the outside-repo wrapper; return (ran, ok, tail).

    Counts come from the wrapper's JSON result file, never from text
    the tested code printed, and only when the wrapper itself exited
    zero: a success-shaped JSON next to a nonzero exit is a failure.
    """
    tmpdir = tempfile.mkdtemp(prefix="agentperf-verify-")
    wrapper = os.path.join(tmpdir, "run_suite.py")
    result_path = os.path.join(tmpdir, "result.json")
    try:
        with open(wrapper, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(_RUNNER)
        argv = [sys.executable, "-I", wrapper, mode, os.path.abspath(repo),
                result_path]
        returncode, stdout = _run_guarded(argv, tmpdir, timeout)
        tail_lines = stdout.strip().splitlines()
        tail = tail_lines[-1] if tail_lines else ""
        if returncode != 0:
            return 0, False, "verifier exited with code {}: {}".format(
                returncode, tail)
        try:
            import json as _json
            with open(result_path, encoding="utf-8") as fh:
                data = _json.load(fh)
            ran, failures, errors = (int(data["ran"]), int(data["failures"]),
                                     int(data["errors"]))
            ok = bool(data["ok"]) and failures == 0 and errors == 0
            return ran, ok, str(data.get("tail") or "")
        except (OSError, ValueError, KeyError, TypeError):
            return 0, False, (tail or "verifier produced no result")
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
    """Check clamp() outputs in this trusted process.

    The probe subprocess (which imports the agent's code) only emits
    return values for inputs generated here; expected values are
    computed and compared here, where no agent code has run. The case
    list mixes the fixed edge cases with fresh random inputs, so a
    lookup table for the known cases cannot pass. The probe must also
    exit zero."""
    import json as _json
    import random
    rng = random.SystemRandom()
    cases = [[-5, 0, 10], [99, 0, 10], [4, 0, 10], [0, 0, 10],
             [10, 0, 10], [1.5, 2.0, 3.0]]
    for _i in range(24):
        low = rng.randint(-50, 50)
        cases.append([rng.randint(-80, 120), low, low + rng.randint(0, 50)])
        low_f = round(rng.uniform(-10, 10), 3)
        cases.append([round(rng.uniform(-15, 25), 3), low_f,
                      low_f + round(rng.uniform(0, 10), 3)])
    expected = [low if value < low else high if value > high else value
                for value, low, high in cases]
    tmpdir = tempfile.mkdtemp(prefix="agentperf-hidden-")
    probe = os.path.join(tmpdir, "probe.py")
    cases_path = os.path.join(tmpdir, "cases.json")
    out_path = os.path.join(tmpdir, "out.json")
    try:
        with open(probe, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(_PROBE)
        with open(cases_path, "w", encoding="utf-8") as fh:
            _json.dump(cases, fh)
        returncode, stdout = _run_guarded(
            [sys.executable, "-I", probe, os.path.abspath(repo),
             cases_path, out_path], tmpdir, timeout)
        tail_lines = stdout.strip().splitlines()
        tail = tail_lines[-1] if tail_lines else ""
        if returncode != 0:
            return False, "hidden probe exited with code {}: {}".format(
                returncode, tail)
        try:
            with open(out_path, encoding="utf-8") as fh:
                outputs = _json.load(fh)
        except (OSError, ValueError):
            return False, "hidden probe produced no outputs: " + tail
        if not isinstance(outputs, list) or len(outputs) != len(cases):
            return False, "hidden probe returned malformed outputs"
        for (value, low, high), want, got in zip(cases, expected, outputs):
            if isinstance(got, dict) or isinstance(got, bool) \
                    or got != want:
                return False, "clamp({!r}, {!r}, {!r}) gave {!r}, " \
                              "expected {!r}".format(value, low, high, got, want)
        return True, "clamp matched {} parent-checked cases".format(len(cases))
    finally:
        for name in ("probe.py", "cases.json", "out.json"):
            try:
                os.remove(os.path.join(tmpdir, name))
            except OSError:
                pass
        try:
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
