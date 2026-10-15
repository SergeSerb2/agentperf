"""Round-5 tests: skill installer, --json summaries, exit codes, multi-host remote."""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _quiet_main(argv):
    from agentperf.cli import main
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


def _tmp_home(test):
    tmp = tempfile.mkdtemp()
    test.addCleanup(shutil.rmtree, tmp, True)
    # expanduser("~") reads HOME on POSIX but USERPROFILE on Windows.
    patcher = mock.patch.dict(os.environ, {"HOME": tmp, "USERPROFILE": tmp})
    patcher.start()
    test.addCleanup(patcher.stop)
    return tmp


class SkillInstallTest(unittest.TestCase):
    def test_install_all_targets_in_temp_home(self):
        from agentperf import skill as skill_mod
        home = _tmp_home(self)
        code, _out, _err = _quiet_main(["skill", "install"])
        self.assertEqual(code, 0)
        for target in ("claude", "codex", "agents"):
            dest = skill_mod.target_path(target, home=home)
            self.assertTrue(os.path.isfile(os.path.join(dest, "SKILL.md")), dest)
            self.assertTrue(os.path.isfile(os.path.join(dest, skill_mod.MANIFEST_NAME)))
            for rel in skill_mod.SKILL_FILES:
                self.assertTrue(os.path.isfile(os.path.join(dest, *rel.split("/"))), rel)
            self.assertEqual(skill_mod.inspect(dest)[0], "clean")
        # Second install is a no-op that still succeeds.
        code, _out, _err = _quiet_main(["skill", "install"])
        self.assertEqual(code, 0)

    def test_project_install_defaults_claude_and_agents(self):
        from agentperf import skill as skill_mod
        proj = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, proj, True)
        code, _out, _err = _quiet_main(
            ["skill", "install", "--project", proj])
        self.assertEqual(code, 0)
        self.assertTrue(os.path.isfile(os.path.join(
            proj, ".claude", "skills", "agentperf", "SKILL.md")))
        self.assertTrue(os.path.isfile(os.path.join(
            proj, ".agents", "skills", "agentperf", "SKILL.md")))
        self.assertFalse(os.path.exists(os.path.join(proj, ".codex")))

    def test_modified_copy_is_refused_then_force_keeps_extras(self):
        from agentperf import skill as skill_mod
        home = _tmp_home(self)
        self.assertEqual(_quiet_main(["skill", "install", "--target", "claude"])[0], 0)
        dest = skill_mod.target_path("claude", home=home)
        with open(os.path.join(dest, "SKILL.md"), "a", encoding="utf-8") as fh:
            fh.write("\nuser edit\n")
        with open(os.path.join(dest, "my-notes.md"), "w", encoding="utf-8") as fh:
            fh.write("mine\n")
        self.assertEqual(skill_mod.inspect(dest)[0], "modified")
        code, _out, _err = _quiet_main(["skill", "install", "--target", "claude"])
        self.assertEqual(code, 1)  # refused, not overwritten
        with open(os.path.join(dest, "SKILL.md"), encoding="utf-8") as fh:
            self.assertIn("user edit", fh.read())
        code, _out, _err = _quiet_main(
            ["skill", "install", "--target", "claude", "--force"])
        self.assertEqual(code, 0)
        with open(os.path.join(dest, "SKILL.md"), encoding="utf-8") as fh:
            self.assertNotIn("user edit", fh.read())
        with open(os.path.join(dest, "my-notes.md"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "mine\n")  # extra file survived

    def test_foreign_copy_is_refused(self):
        from agentperf import skill as skill_mod
        home = _tmp_home(self)
        dest = skill_mod.target_path("agents", home=home)
        os.makedirs(dest)
        with open(os.path.join(dest, "SKILL.md"), "w", encoding="utf-8") as fh:
            fh.write("someone else's skill\n")
        self.assertEqual(skill_mod.inspect(dest)[0], "foreign")
        code, _out, _err = _quiet_main(["skill", "install", "--target", "agents"])
        self.assertEqual(code, 1)
        with open(os.path.join(dest, "SKILL.md"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "someone else's skill\n")

    def test_uninstall_clean_modified_and_absent(self):
        from agentperf import skill as skill_mod
        home = _tmp_home(self)
        self.assertEqual(_quiet_main(["skill", "uninstall", "--target", "codex"])[0], 0)
        self.assertEqual(_quiet_main(["skill", "install", "--target", "codex"])[0], 0)
        dest = skill_mod.target_path("codex", home=home)
        with open(os.path.join(dest, "SKILL.md"), "a", encoding="utf-8") as fh:
            fh.write("\nedit\n")
        self.assertEqual(_quiet_main(["skill", "uninstall", "--target", "codex"])[0], 1)
        self.assertTrue(os.path.isdir(dest))
        self.assertEqual(_quiet_main(
            ["skill", "uninstall", "--target", "codex", "--force"])[0], 0)
        self.assertFalse(os.path.exists(dest))

    def test_skill_path_json_and_states(self):
        home = _tmp_home(self)
        dest_json = os.path.join(home, "path.json")
        code, _out, _err = _quiet_main(
            ["skill", "path", "--target", "claude", "--json=" + dest_json])
        self.assertEqual(code, 0)
        with open(dest_json, encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual(doc["command"], "skill path")
        self.assertEqual(doc["targets"][0]["state"], "absent")
        self.assertEqual(_quiet_main(["skill", "install", "--target", "claude"])[0], 0)
        code, out, _err = _quiet_main(
            ["skill", "path", "--target", "claude", "--json=-"])
        self.assertEqual(code, 0)
        doc = json.loads(out)  # stdout is only JSON
        self.assertEqual(doc["targets"][0]["state"], "clean")

    def test_skill_without_subcommand_is_usage_error(self):
        code, _out, err = _quiet_main(["skill"])
        self.assertEqual(code, 2)
        self.assertIn("install", err)

    def test_bundled_skill_matches_repo_skill_and_file_list(self):
        from agentperf import skill as skill_mod
        packaged = os.path.join(ROOT, "agentperf", "skill")
        repo = os.path.join(ROOT, "skills", "agentperf")
        for rel in skill_mod.SKILL_FILES:
            parts = rel.split("/")
            with open(os.path.join(packaged, *parts), "rb") as fh:
                a = fh.read()
            with open(os.path.join(repo, *parts), "rb") as fh:
                b = fh.read()
            self.assertEqual(a, b, rel)
        on_disk = []
        for base, _dirs, names in os.walk(repo):
            for name in names:
                on_disk.append(os.path.relpath(
                    os.path.join(base, name), repo).replace(os.sep, "/"))
        self.assertEqual(sorted(on_disk), sorted(skill_mod.SKILL_FILES))

    def test_skill_frontmatter(self):
        with open(os.path.join(ROOT, "skills", "agentperf", "SKILL.md"),
                  encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.startswith("---\n"))
        head = text.split("\n---", 1)[0]
        self.assertIn("name: agentperf", head)
        self.assertIn("description:", head)
        lowered = head.lower()
        for trigger in ("benchmark", "where", "slow"):
            self.assertIn(trigger, lowered)

    def test_plugin_manifests_are_valid_json_and_aligned(self):
        with open(os.path.join(ROOT, ".claude-plugin", "marketplace.json"),
                  encoding="utf-8") as fh:
            market = json.load(fh)
        with open(os.path.join(ROOT, ".claude-plugin", "plugin.json"),
                  encoding="utf-8") as fh:
            plugin = json.load(fh)
        self.assertEqual(market["plugins"][0]["name"], plugin["name"])
        self.assertEqual(plugin["name"], "agentperf")
        self.assertEqual(market["plugins"][0]["source"], "./")
        import re
        with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as fh:
            pyproject = fh.read()
        version = re.search(r'^version = "([^"]+)"', pyproject, re.M).group(1)
        self.assertEqual(plugin["version"], version)
        with open(os.path.join(ROOT, "agentperf", "__init__.py"), encoding="utf-8") as fh:
            init = fh.read()
        self.assertIn('_PACKAGE_VERSION_FALLBACK = "{}"'.format(version), init)


class JsonSummaryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.result = os.path.join(self.tmp, "r.json")
        code, _out, err = _quiet_main(
            ["run", "--quick", "--no-network", "--clean",
             "--only", "process.shell,files.read,compute.sha256",
             "--label", "unit-smoke", "--out", self.result,
             "--workdir", os.path.join(self.tmp, "work")])
        self.assertEqual(code, 0, err)

    def test_report_json_to_file(self):
        summary = os.path.join(self.tmp, "summary.json")
        html = os.path.join(self.tmp, "r.html")
        code, _out, _err = _quiet_main(
            ["report", self.result, "--html", html, "--json=" + summary])
        self.assertEqual(code, 0)
        with open(summary, encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual(doc["command"], "report")
        self.assertEqual(doc["summary_version"], 1)
        self.assertEqual(doc["label"], "unit-smoke")
        self.assertIsNotNone(doc["agentmark"])
        self.assertTrue(0 < doc["coverage"] <= 1)
        self.assertEqual(os.path.abspath(html), doc["html_file"])
        self.assertIn("verdict", doc)

    def test_report_json_stdout_is_only_json(self):
        code, out, _err = _quiet_main(["report", self.result, "--json=-"])
        self.assertEqual(code, 0)
        doc = json.loads(out)
        self.assertEqual(doc["command"], "report")

    def test_compare_json(self):
        summary = os.path.join(self.tmp, "cmp.json")
        code, _out, _err = _quiet_main(
            ["compare", self.result, self.result, "--json=" + summary])
        self.assertEqual(code, 0)
        with open(summary, encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual(doc["command"], "compare")
        self.assertEqual(len(doc["machines"]), 2)
        self.assertIsNotNone(doc["machines"][0]["agentmark_common"])

    def test_doctor_json_ok_field(self):
        code, out, _err = _quiet_main(["doctor", "--json=-"])
        self.assertEqual(code, 0)  # doctor ran; health is in `ok`
        doc = json.loads(out)
        self.assertEqual(doc["command"], "doctor")
        self.assertEqual(doc["ok"], not doc["checks"])
        self.assertIn("missing_tools", doc)

    def test_run_json_stdout_is_only_json(self):
        out_path = os.path.join(self.tmp, "r2.json")
        code, out, _err = _quiet_main(
            ["run", "--quick", "--no-network", "--clean",
             "--only", "process.shell", "--label", "unit-smoke-2",
             "--out", out_path, "--workdir", os.path.join(self.tmp, "work2"),
             "--json=-"])
        self.assertEqual(code, 0)
        doc = json.loads(out)  # progress went to stderr, stdout parses whole
        self.assertEqual(doc["command"], "run")
        self.assertEqual(doc["result_file"], os.path.abspath(out_path))


class ExitCodeTest(unittest.TestCase):
    def test_missing_result_is_2(self):
        code, _out, _err = _quiet_main(["report", "/nonexistent/definitely.json"])
        self.assertEqual(code, 2)

    def test_malformed_result_is_2(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        bad = os.path.join(tmp, "bad.json")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write('{"nope": true}\n')
        code, _out, _err = _quiet_main(["report", bad])
        self.assertEqual(code, 2)

    def test_no_command_is_2(self):
        code, _out, _err = _quiet_main([])
        self.assertEqual(code, 2)

    def test_refused_workdir_is_1_without_traceback(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        keep = os.path.join(tmp, "keep.txt")
        with open(keep, "w", encoding="utf-8") as fh:
            fh.write("mine")
        code, _out, err = _quiet_main(
            ["run", "--quick", "--workdir", tmp,
             "--out", os.path.join(tempfile.gettempdir(), "never-written.json")])
        self.assertEqual(code, 1)
        self.assertIn("error:", err)
        self.assertNotIn("Traceback", err)
        with open(keep, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "mine")

    def test_bad_wsl_combo_is_2(self):
        code, _out, err = _quiet_main(
            ["remote", "my-mac", "linux-box", "--wsl", "Ubuntu"])
        self.assertEqual(code, 2)
        self.assertIn("HOST:DISTRO", err)


def _remote_args(**over):
    base = dict(hosts=[], host=None, wsl=[], label=None, python=None,
                profile="standard", quick=False, runs=1, workdir=None,
                fetch_tools=False, clean=False, no_network=False, only=None,
                out_dir="results", mode="run", json=None, extra=[])
    base.update(over)
    return types.SimpleNamespace(**base)


class RemoteTargetsTest(unittest.TestCase):
    def test_single_host_legacy_wsl(self):
        from agentperf.remote import parse_targets
        self.assertEqual(parse_targets(_remote_args(hosts=["my-pc"], wsl=["Ubuntu"])),
                         [("my-pc", "Ubuntu", "my-pc-wsl-ubuntu")])
        self.assertEqual(parse_targets(_remote_args(host="old-pc", wsl="Debian")),
                         [("old-pc", "Debian", "old-pc-wsl-debian")])

    def test_multi_host_order_and_wsl_extra(self):
        from agentperf.remote import parse_targets
        got = parse_targets(_remote_args(
            hosts=["my-mac", "my-pc"], wsl=["my-pc:Ubuntu"]))
        self.assertEqual(got, [("my-mac", None, "my-mac"),
                               ("my-pc", None, "my-pc"),
                               ("my-pc", "Ubuntu", "my-pc-wsl-ubuntu")])

    def test_duplicate_labels_and_bad_label(self):
        from agentperf.remote import parse_targets
        with self.assertRaises(ValueError):
            parse_targets(_remote_args(hosts=["a@dup", "b@dup"]))
        with self.assertRaises(ValueError):
            parse_targets(_remote_args(hosts=["my-mac"], label="bad label; rm"))
        with self.assertRaises(ValueError):
            parse_targets(_remote_args(hosts=["a", "b"], label="one"))

    def test_subcommand_forwarding(self):
        from agentperf.remote import _subcommand
        args = _remote_args(quick=True, runs=3, fetch_tools=True, clean=True,
                            no_network=True, only="git", workdir="/work/here")
        sub = _subcommand(args, "lab", ".agentperf/out.json")
        self.assertIn("--profile", sub)
        self.assertEqual(sub[sub.index("--profile") + 1], "quick")
        self.assertEqual(sub[sub.index("--runs") + 1], "3")
        self.assertEqual(sub[sub.index("--workdir") + 1], "/work/here")
        for flag in ("--fetch-tools", "--clean", "--no-network"):
            self.assertIn(flag, sub)


class RemoteOrchestrationTest(unittest.TestCase):
    def _run_with_fake_ssh(self, args, behavior, fetched_docs):
        """behavior: host -> remote exit code, or 'setup-fail' to raise early."""
        from agentperf import remote as remote_mod

        order = []

        def fake_is_windows(host):
            if behavior.get(host) == "setup-fail":
                raise SystemExit("cannot reach {!r} over SSH".format(host))
            return False

        def fake_ssh(host, command, stream=False, out=None):
            class Proc(object):
                pass
            proc = Proc()
            if stream:
                order.append(host)
                proc.returncode = behavior.get(host, 0)
            else:
                proc.returncode = 0
                proc.stdout = b""
                proc.stderr = b""
            return proc

        def fake_run(argv, **_kwargs):
            class Proc(object):
                pass
            proc = Proc()
            proc.returncode = 0
            if len(argv) >= 3 and ":" in str(argv[-2]):  # scp fetch: write the doc
                for label, doc in fetched_docs.items():
                    if label in str(argv[-2]) or str(argv[-1]).endswith(label + ".json"):
                        with open(argv[-1], "w", encoding="utf-8") as fh:
                            json.dump(doc, fh)
                        break
            return proc

        with mock.patch.object(remote_mod, "_is_windows", side_effect=fake_is_windows), \
                mock.patch.object(remote_mod, "_ssh", side_effect=fake_ssh), \
                mock.patch.object(remote_mod.subprocess, "run", side_effect=fake_run):
            code, summary = remote_mod.run_remote(args)
        return code, summary, order

    def _doc(self, label):
        # Round 7: an empty benchmarks object is not a successful fetch.
        # Round 8 (F7): a fetched run must pass full result validation,
        # so the fake fetched entry carries the whole entry shape.
        entry = {"category": "process", "title": "process", "unit": "ms",
                 "lower_is_better": True, "weight": 1.0, "scored": True,
                 "ops": 1, "median": 1.0, "samples": [1.0], "cv": 0.0,
                 "noisy": False, "meta": {}}
        return {"label": label, "profile": "standard",
                "benchmarks": {"process.shell": entry},
                "skipped": {}, "system": {},
                "schema": 1, "corpus": {"fixture_hash": "h"}}

    def test_hosts_run_in_order_and_all_fetch(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        args = _remote_args(hosts=["beta", "alpha"], out_dir=tmp)
        code, summary, order = self._run_with_fake_ssh(
            args, {}, {"beta": self._doc("beta"), "alpha": self._doc("alpha")})
        self.assertEqual(code, 0)
        self.assertEqual(order, ["beta", "alpha"])  # given order, sequential
        self.assertEqual([h["label"] for h in summary["hosts"]], ["beta", "alpha"])
        self.assertEqual(len(summary["results"]), 2)
        self.assertTrue(summary["sequential"])

    def test_one_failure_does_not_block_the_rest(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        args = _remote_args(hosts=["down", "up"], out_dir=tmp)
        code, summary, order = self._run_with_fake_ssh(
            args, {"down": "setup-fail"}, {"up": self._doc("up")})
        self.assertEqual(code, 1)
        self.assertEqual(order, ["up"])
        self.assertIsNotNone(summary["hosts"][0]["error"])
        self.assertIsNotNone(summary["hosts"][1]["result_file"])

    def test_remote_exit_code_wins_over_fetched_file(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        args = _remote_args(hosts=["solo"], out_dir=tmp)
        code, summary, _order = self._run_with_fake_ssh(
            args, {"solo": 3}, {"solo": self._doc("solo")})
        self.assertEqual(code, 3)
        self.assertEqual(summary["hosts"][0]["exit_code"], 3)

    def test_doctor_mode_fetches_the_summary_not_a_result(self):
        # Round 7: remote doctor carries each host's health summary.
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        args = _remote_args(hosts=["solo"], out_dir=tmp, mode="doctor")
        doctor = {"command": "doctor", "ok": True, "checks": [],
                  "missing_tools": []}
        code, summary, order = self._run_with_fake_ssh(
            args, {}, {"solo": doctor})
        self.assertEqual(code, 0)
        self.assertEqual(summary["results"], [])
        self.assertIsNone(summary["hosts"][0]["result_file"])
        self.assertIs(summary["hosts"][0]["doctor"]["ok"], True)


class ScoresUnchangedTest(unittest.TestCase):
    def test_bundled_profiles_still_score_the_same(self):
        from agentperf import SUITE_VERSION, scoring
        self.assertEqual(SUITE_VERSION, "0.3.0")
        ref_dir = os.path.join(ROOT, "agentperf", "references")
        with open(os.path.join(ref_dir, "index.json"), encoding="utf-8") as fh:
            index = json.load(fh)
        with open(os.path.join(ref_dir, "DEFAULT"), encoding="utf-8") as fh:
            default_name = fh.read().strip()
        default = scoring.load(os.path.join(ref_dir, default_name + ".json"))
        expected = {"wsl2-i7-12700k": 1000, "windows-i7-12700k": 103,
                    "macos-m1-pro": 398, "macos-m5-pro": 408}
        for profile in index["profiles"]:
            doc = scoring.load(os.path.join(ref_dir, profile["file"]))
            self.assertEqual(scoring.fingerprint(doc), doc["fingerprint"],
                             profile["name"])
            mark = scoring.score(doc, default)["agentmark"]
            self.assertEqual(round(mark), expected[profile["name"]],
                             profile["name"])


class PyzSkillTest(unittest.TestCase):
    def test_pyz_carries_the_skill(self):
        import zipfile
        from agentperf.remote import build_pyz
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        path = build_pyz(os.path.join(tmp, "a.pyz"))
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
        self.assertIn("agentperf/skill/SKILL.md", names)
        self.assertIn("agentperf/skill/references/multi-machine.md", names)
        self.assertIn("agentperf/summary.py", names)


if __name__ == "__main__":
    unittest.main()
