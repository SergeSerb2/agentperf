import os
import shutil
import tempfile
import unittest

from agentperf.live import fixture as fixture_mod
from agentperf.live import tasks as tasks_mod
from agentperf.live.parsers import ClaudeParser, CodexParser

_HERE = os.path.dirname(os.path.abspath(__file__))
_SAMPLES = os.path.join(_HERE, "fixtures", "live")


def _replace_in(path, old, new):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    assert old in text, path
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text.replace(old, new))


class FixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="agentperf-test-")
        cls.clean = fixture_mod.build_fixture(os.path.join(cls.tmp, "clean"))
        cls.clean2 = fixture_mod.build_fixture(os.path.join(cls.tmp, "clean2"))
        cls.buggy = fixture_mod.build_fixture(
            os.path.join(cls.tmp, "buggy"), seed_bug=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _copy(self, base, name):
        return fixture_mod.fresh_copy(base, os.path.join(self.tmp, name))

    def test_deterministic(self):
        self.assertEqual(fixture_mod.tree_hash(self.clean),
                         fixture_mod.tree_hash(self.clean2))

    def test_fix_verifier(self):
        repo = self._copy(self.buggy, "fix-untouched")
        ok, _ = tasks_mod.verify("fix", repo)
        self.assertFalse(ok)
        repo = self._copy(self.buggy, "fix-solved")
        _replace_in(os.path.join(repo, "calcbox", "sequences.py"),
                    "values[start:end]", "values[start:end + 1]")
        ok, detail = tasks_mod.verify("fix", repo)
        self.assertTrue(ok, detail)

    def test_rename_verifier(self):
        repo = self._copy(self.clean, "rename-untouched")
        ok, _ = tasks_mod.verify("rename", repo)
        self.assertFalse(ok)
        repo = self._copy(self.clean, "rename-solved")
        for dirpath, dirnames, filenames in os.walk(repo):
            if ".git" in dirnames:
                dirnames.remove(".git")
            for name in filenames:
                if name.endswith(".py"):
                    path = os.path.join(dirpath, name)
                    with open(path, encoding="utf-8") as fh:
                        text = fh.read()
                    if "format_label" in text:
                        with open(path, "w", encoding="utf-8", newline="\n") as fh:
                            fh.write(text.replace("format_label", "build_label"))
        ok, detail = tasks_mod.verify("rename", repo)
        self.assertTrue(ok, detail)

    def test_feature_verifier(self):
        repo = self._copy(self.clean, "feature-untouched")
        ok, _ = tasks_mod.verify("feature", repo)
        self.assertFalse(ok)
        # hidden test file must not linger in the repo
        self.assertFalse(os.path.exists(
            os.path.join(repo, "tests", "test_hidden_clamp.py")))
        repo = self._copy(self.clean, "feature-solved")
        with open(os.path.join(repo, "calcbox", "numbers.py"), "a",
                  encoding="utf-8", newline="\n") as fh:
            fh.write("\n\ndef clamp(value, low, high):\n"
                     "    return max(low, min(high, value))\n")
        ok, detail = tasks_mod.verify("feature", repo)
        self.assertTrue(ok, detail)


def _feed(parser, path, times):
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    assert len(times) == len(lines), (len(times), len(lines))
    for t, line in zip(times, lines):
        parser.feed(t, line)


class ParserTests(unittest.TestCase):
    def test_claude_sample(self):
        # lines: init, tool_use, rate_limit, tool_result, assistant, result
        parser = ClaudeParser(100.0)
        _feed(parser, os.path.join(_SAMPLES, "claude_sample.jsonl"),
              [101.0, 102.0, 102.1, 107.0, 108.0, 109.0])
        r = parser.result(10.0)
        self.assertEqual(r["startup_s"], 1.0)
        self.assertEqual(r["tool_s"], 5.0)
        self.assertEqual(r["model_s"], 4.0)
        self.assertEqual(r["tool_calls"], 1)
        self.assertEqual(r["tool_breakdown"], {"Bash": {"count": 1, "s": 5.0}})
        self.assertEqual(r["turns"], 2)
        self.assertEqual(r["api_s"], 2.192)
        self.assertEqual(r["tokens"]["input"], 4)
        self.assertEqual(r["tokens"]["cache_read"], 27798)
        self.assertAlmostEqual(r["cost_usd"], 0.0273738)
        self.assertTrue(r["model"].startswith("claude-"))

    def test_codex_sample(self):
        # thread, turn, msg, item.started, item.completed, msg, turn.completed
        parser = CodexParser(100.0)
        _feed(parser, os.path.join(_SAMPLES, "codex_sample.jsonl"),
              [100.5, 100.6, 101.0, 102.0, 105.0, 106.0, 106.5])
        r = parser.result(8.0)
        self.assertEqual(r["startup_s"], 0.5)
        self.assertEqual(r["tool_s"], 3.0)
        self.assertEqual(r["model_s"], 4.5)
        self.assertEqual(r["tool_calls"], 1)
        self.assertEqual(r["tool_breakdown"], {"Bash": {"count": 1, "s": 3.0}})
        self.assertEqual(r["turns"], 1)
        self.assertIsNone(r["api_s"])
        self.assertIsNone(r["cost_usd"])
        self.assertEqual(r["tokens"]["input"], 30488)
        self.assertEqual(r["tokens"]["output"], 46)
        self.assertEqual(r["tokens"]["cache_read"], 27264)


if __name__ == "__main__":
    unittest.main()
