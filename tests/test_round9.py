"""Round-9 regression tests: blockers from the round-8 verification.

R1: share scrubs unknown children of the objects it keeps whole.
R2: combine and merge never relabel a shell benchmark's policy.
R3: results missing report fields are refused before any report runs.
Temp folders only: no network, no real home folders. Invented names only."""
from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REFS = os.path.join(ROOT, "agentperf", "references")
PROFILES = ("wsl2-i7-12700k", "windows-i7-12700k", "macos-m1-pro",
            "macos-m5-pro")
SECRET = "/Users/Alice/Private Repo/file"


def _quiet_main(argv):
    from agentperf.cli import main
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


def _load(name):
    with open(os.path.join(REFS, name + ".json"), encoding="utf-8") as fh:
        return json.load(fh)


def _entry(category, median):
    return {"category": category, "title": category, "unit": "ms",
            "lower_is_better": True, "weight": 1.0, "scored": True,
            "ops": 1, "median": median, "samples": [median], "cv": 0.0,
            "noisy": False, "meta": {}}


def _doc(benches, shell_launch):
    doc = {"label": "box", "profile": "standard", "benchmarks": benches,
           "skipped": {}, "schema": 1, "corpus": {"fixture_hash": "h1"},
           "system": {"cpu_model": "Test CPU", "os_family": "linux"}}
    if shell_launch is not None:
        doc["measurement_env"] = {"shell_launch": shell_launch}
    return doc


class ShareUnknownFieldsTest(unittest.TestCase):
    """R1: keeping corpus, measurement_env, and scoring_config must not
    keep text the tool never writes there."""

    def test_unknown_children_are_scrubbed_known_ones_kept(self):
        from agentperf import scoring
        from agentperf.privacy import anonymize
        for name in PROFILES:
            doc = _load(name)
            doc.setdefault("measurement_env", {})
            doc["scoring_config"] = scoring.scoring_config_snapshot(
                scoring._weights_for(doc))
            for key in ("corpus", "measurement_env", "scoring_config"):
                doc[key]["operator_note"] = SECRET
                doc[key]["nested"] = {"where": [SECRET]}
            before = {k: copy.deepcopy(doc[k]) for k in
                      ("corpus", "measurement_env", "scoring_config")}
            clean, _changes = anonymize(copy.deepcopy(doc))
            text = json.dumps(clean)
            self.assertNotIn("Alice", text, name)
            self.assertNotIn("Private Repo", text, name)
            for key, value in before.items():
                for child, kept in value.items():
                    if child in ("operator_note", "nested"):
                        continue
                    self.assertEqual(clean[key][child], kept, (name, key))
            mine = scoring.score(clean, _load(PROFILES[0]))
            theirs = scoring.score(doc, _load(PROFILES[0]))
            self.assertEqual(mine["agentmark"], theirs["agentmark"], name)
            self.assertEqual(scoring.shell_env_policies(clean),
                             scoring.shell_env_policies(doc), name)


    def test_nested_started_at_and_schema_strings_are_scrubbed(self):
        # Round-9 verification: a nested "started_at" was cut to ten
        # characters without scrubbing ("/home/Bob/"), and schema-
        # position strings (benchmark ids, kept fields) passed through
        # whole even when they held a home path.
        from agentperf.privacy import anonymize
        nested = {"nested": [{"started_at": "/home/Bob/Private Repo/file"}],
                  "list": [[{"started_at": "C:\\Users\\Bob\\x"}]]}
        for key in ("corpus", "measurement_env", "scoring_config"):
            doc = _load("macos-m1-pro")
            doc.setdefault(key, {})["operator_note"] = copy.deepcopy(nested)
            doc["benchmarks"]["/Users/Bob/secret"] = copy.deepcopy(
                doc["benchmarks"]["process.shell"])
            doc["profile"] = "/Users/Bob/Private Repo"
            doc["started_at"] = "/home/Bob/Private Repo"
            text = json.dumps(anonymize(doc)[0])
            self.assertNotIn("Bob", text, key)
            self.assertNotIn("Private Repo", text, key)
        doc = _load("macos-m1-pro")
        clean, _c = anonymize(copy.deepcopy(doc))
        self.assertEqual(clean["started_at"], doc["started_at"][:10])

    def test_every_position_and_form_is_scrubbed(self):
        # Round-9 second verification (283b673): F1-F6 reproducers.
        from agentperf import scoring
        from agentperf.privacy import anonymize
        base = {"label": "box", "profile": "standard", "system": {},
                "skipped": {},
                "benchmarks": {"process.shell": {
                    "category": "process", "title": "Shell start",
                    "unit": "ms", "lower_is_better": True, "weight": 1.0,
                    "scored": True, "median": 1.0, "samples": [1.0],
                    "meta": {}}}}
        who = {"hostname": "BUILDHOST7",
               "workdir": "/home/Bob/Private Repo"}
        cases = [
            ({"started_at": "2001:0db8:1111:2222:3333:4444:5555:6666"},
             ["2001:0db8", "1111"]),
            ({"started_at": "box99.private-tail.ts.net"}, ["box99"]),
            ({"profile": "C:\\Documents and Settings\\Bob\\Private Repo"},
             ["Bob", "Private"]),
            ({"profile": "~Bob"}, ["Bob"]),
            ({"profile": "/home/<user>Bob/Private Repo"}, ["Bob", "Private"]),
            ({"profile": "AA:BB:CC:DD:EE:FF"}, ["AA:BB"]),
            ({"corpus": {"note": {"n": [{"started_at": "~_bob/Private Repo"}]}}},
             ["bob", "Private"]),
            ({"note": "~123bob/Private Repo"}, ["123bob", "Private"]),
            ({"note": "~\u00c9lodie/Private Repo"}, ["lodie", "Private"]),
            ({"system": dict(who, cpu_model="Bob CPU"), "profile": "Bob",
              "tool_versions": {"python": "BUILDHOST7"},
              "reference_profile": {"name": "Bob"}}, ["Bob", "BUILDHOST7"]),
            ({"system": dict(who), "note": {"Bob": "owner",
                                            "BUILDHOST7": "machine"}},
             ["Bob", "BUILDHOST7"]),
            ({"system": {"workdir": "/home/Al/Private Repo"}, "note": "Al"},
             ['"Al"']),
            ({"system": {"hostname": "pc"}, "note": "pc"}, ['"pc"']),
            ({"note": {"source": "C:\\Documents and Settings\\Bob\\x",
                       "owner": "Bob"}}, ["Bob"]),
            ({"note": {"source": "~Bob", "owner": "Bob"}}, ["Bob"]),
            ({"tools": {"python": "/home/Bob/bin/Bob"}}, ["Bob"]),
        ]
        for patch, forbidden in cases:
            doc = copy.deepcopy(base)
            doc.update(copy.deepcopy(patch))
            scoring.validate_result(copy.deepcopy(doc))
            clean, _c = anonymize(doc)
            text = json.dumps(clean, ensure_ascii=False)
            for word in forbidden:
                self.assertNotIn(word, text, (patch, text))
        # A benchmark id or meta value cannot shelter a name.
        doc = copy.deepcopy(base)
        doc["system"] = dict(who)
        doc["benchmarks"]["Bob"] = copy.deepcopy(
            doc["benchmarks"]["process.shell"])
        doc["benchmarks"]["process.shell"]["meta"]["method"] = "Bob"
        doc["note"] = "Bob"
        self.assertNotIn("Bob", json.dumps(anonymize(doc)[0]))
        # Keys that scrub to one placeholder keep both values.
        doc = copy.deepcopy(base)
        doc["system"] = dict(who)
        doc["skipped"] = {"Bob": "owner", "BUILDHOST7": "machine"}
        skipped = anonymize(doc)[0]["skipped"]
        self.assertEqual(sorted(skipped.values()), ["machine", "owner"])
        self.assertNotIn("Bob", json.dumps(skipped))

    def test_fields_agentperf_does_not_write_are_dropped(self):
        # Round-9 third verification (f137e3c): hand-added fields are
        # dropped whole, at every level, instead of scrubbed.
        from agentperf.privacy import anonymize
        doc = _load("macos-m1-pro")
        doc["owner"] = "files"
        doc["system"]["hostname"] = "files"
        doc["system"]["owner_note"] = {"who": "Bob"}
        doc["corpus"]["note"] = "Bob"
        doc["benchmarks"]["process.shell"]["note"] = "Bob"
        doc["benchmarks"]["process.shell"]["meta"]["owner"] = "Bob"
        doc["combined_runs"] = [{"started_at": "2026-10-01T10:00:00",
                                 "system": {"hostname": "BUILDHOST7"}}]
        doc["reference_profile"]["owner"] = "Bob"
        doc["tools"]["notes"] = "Bob"
        clean, changes = anonymize(doc)
        text = json.dumps(clean)
        for word in ("owner", "Bob", "BUILDHOST7", "owner_note", '"note"',
                     '"notes"'):
            self.assertNotIn(word, text)
        self.assertEqual(clean["combined_runs"], [{"started_at": "2026-10-01"}])
        self.assertTrue(any("does not write" in c for c in changes), changes)

    def test_address_and_home_forms_from_the_third_review(self):
        from agentperf.privacy import anonymize
        base = _load("macos-m1-pro")
        cases = [
            ({"workdir": '"/home/Bob"'}, "Bob"),
            ({"workdir": "/home/Bob\nnext line"}, "Bob"),
            ({"workdir": "C:\\Users/Bob/private"}, "Bob"),
            ({"workdir": "/home//Bob/private"}, "Bob"),
            ({"workdir": "/home/bob-/private"}, "bob-"),
        ]
        for system, name in cases:
            doc = copy.deepcopy(base)
            doc["system"].update(system)
            doc["system"]["os_version"] = name + " " + name + "Laptop"
            doc["skipped"] = {"process.node": "owner " + name}
            text = json.dumps(anonymize(doc)[0])
            self.assertNotIn(name, text, system)
        for value in ("~123bob", "~.bob", "ip:2001:db8:1111:2222:3333:4444:5555:6666",
                      "ip:fe80::1", "_10.20.30.40_", "_AA:BB:CC:DD:EE:FF_",
                      "_box99.private-tail.ts.net_", "aabb.ccdd.eeff",
                      '"bob"@example.com', "2001:db8::42"):
            doc = copy.deepcopy(base)
            doc["skipped"] = {"process.node": "seen " + value}
            doc["system"]["os_version"] = value
            clean = anonymize(doc)[0]
            for where in (clean["skipped"]["process.node"],
                          clean["system"]["os_version"]):
                for frag in ("bob", "2001:db8", "fe80", "10.20.30", "AA:BB",
                             "box99", "aabb.ccdd"):
                    self.assertNotIn(frag, where, (value, where))
        # Ordinary text with colons and tildes is left alone.
        doc = copy.deepcopy(base)
        doc["skipped"] = {"process.node": "std::string at 12:30:45, ~4 KB, ::1"}
        self.assertEqual(anonymize(doc)[0]["skipped"]["process.node"],
                         "std::string at 12:30:45, ~4 KB, ::1")

    def test_schema_word_names_never_move_scores(self):
        from agentperf import scoring
        from agentperf.privacy import anonymize
        ref = _load(PROFILES[0])
        names = ("work", "pc", "go", "is", "ms", "git", "bash", "shell",
                 "read", "copy", "linux", "standard", "sanitized")
        for name in PROFILES:
            base = _load(name)
            before = scoring.score(base, ref)["agentmark"]
            for who in names:
                doc = copy.deepcopy(base)
                doc["system"]["hostname"] = who
                doc["system"]["workdir"] = "/home/{}/agentperf".format(who)
                clean, _c = anonymize(doc)
                self.assertEqual(clean["benchmarks"], base["benchmarks"],
                                 (name, who))
                self.assertEqual(clean["fingerprint"], base["fingerprint"])
                self.assertEqual(scoring.score(clean, ref)["agentmark"],
                                 before, (name, who))

    def test_bundled_share_output_unchanged_by_the_guard(self):
        from agentperf import scoring
        from agentperf.privacy import anonymize
        for name in PROFILES:
            doc = _load(name)
            clean, _c = anonymize(copy.deepcopy(doc))
            self.assertEqual(clean["benchmarks"], doc["benchmarks"], name)
            for key in ("corpus", "measurement_env", "profile",
                        "fingerprint", "suite_version"):
                self.assertEqual(clean.get(key), doc.get(key), (name, key))
            self.assertEqual(scoring.fingerprint(clean), doc["fingerprint"])


class ShellPolicyProvenanceTest(unittest.TestCase):
    """R2: a benchmark taken from one input must not inherit another
    input's measurement_env fallback."""

    def _pair(self):
        a = _doc({"process.shell": _entry("process", 1.0)},
                 "sanitized (removed: BASH_ENV)")
        b = _doc({"process.parallel": _entry("process", 2.0)},
                 "inherited shell env")
        return a, b

    def test_combine_refuses_disjoint_differing_fallbacks(self):
        from agentperf.cli import combine_runs
        a, b = self._pair()
        with self.assertRaises(ValueError) as caught:
            combine_runs([a, b])
        self.assertIn("process.parallel", str(caught.exception))
        with self.assertRaises(ValueError):
            combine_runs([b, a])

    def test_unrecorded_policy_never_reads_as_sanitized(self):
        from agentperf.cli import combine_runs
        a, _b = self._pair()
        old = _doc({"process.parallel": _entry("process", 2.0)}, None)
        with self.assertRaises(ValueError):
            combine_runs([a, old])

    def test_merge_command_refuses_and_writes_nothing(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        a, b = self._pair()
        paths = []
        for i, doc in enumerate((a, b)):
            path = os.path.join(tmp, "{}.json".format(i))
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(doc, fh)
            paths.append(path)
        out = os.path.join(tmp, "merged.json")
        for argv in (["merge"] + paths + ["--out", out],
                     ["combine"] + paths + ["--out", out]):
            code, _o, err = _quiet_main(argv)
            self.assertEqual(code, 2, (argv[0], err))
            self.assertNotIn("Traceback", err)
            self.assertFalse(os.path.exists(out), argv[0])

    def test_agreeing_or_per_benchmark_policies_still_merge(self):
        from agentperf import scoring
        from agentperf.cli import combine_runs
        a, b = self._pair()
        b["measurement_env"]["shell_launch"] = "sanitized (removed: BASH_ENV)"
        out = combine_runs([a, b])
        self.assertEqual(set(scoring.shell_env_policies(out).values()),
                         {"sanitized"})
        # A benchmark's own note travels with it, whatever the fallback.
        a, b = self._pair()
        b["benchmarks"]["process.parallel"]["meta"]["env_policy"] = \
            "sanitized (removed: BASH_ENV)"
        out = combine_runs([a, b])
        self.assertEqual(scoring.shell_env_policies(out)["process.parallel"],
                         "sanitized")


class ReportFieldsTest(unittest.TestCase):
    """R3: a result missing what reports print is bad input (exit 2),
    never a KeyError in report."""

    def test_missing_header_or_entry_fields_are_refused(self):
        from agentperf import scoring
        from agentperf.remote import _valid_fetched
        base = _load("macos-m1-pro")
        self.assertTrue(_valid_fetched(copy.deepcopy(base), base["label"]))
        bid = next(iter(base["benchmarks"]))
        cases = []
        for key in ("label", "profile", "system", "skipped"):
            doc = copy.deepcopy(base)
            del doc[key]
            cases.append(doc)
        for key in ("title", "unit"):
            doc = copy.deepcopy(base)
            del doc["benchmarks"][bid][key]
            cases.append(doc)
        for doc in cases:
            self.assertFalse(_valid_fetched(doc, base["label"]))
            with self.assertRaises(ValueError):
                scoring.validate_result(doc)

    def test_report_command_exits_cleanly(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        doc = _load("macos-m1-pro")
        del doc["profile"]
        path = os.path.join(tmp, "r.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
        code, _o, err = _quiet_main(["report", path])
        self.assertEqual(code, 2, err)
        self.assertNotIn("Traceback", err)
        self.assertIn("profile", err)


if __name__ == "__main__":
    unittest.main()


class ShareAllowlistTest(unittest.TestCase):
    """share keeps only fields agentperf writes. These tests keep that
    list in step with the code that writes them."""

    def test_every_system_probe_key_is_kept_or_dropped_on_purpose(self):
        # Runs the real probe on this machine; CI covers Linux, macOS,
        # and Windows, so every platform's keys are checked.
        from agentperf import env
        from agentperf.privacy import _DROP_SYSTEM, _SYSTEM_FIELDS
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        system = env.collect(tmp)
        unknown = set(system) - set(_SYSTEM_FIELDS) - set(_DROP_SYSTEM) \
            - {"hostname", "workdir", "top_processes"}
        self.assertEqual(unknown, set())

    def test_meta_and_tool_keys_written_by_the_code_are_kept(self):
        import re
        from agentperf.privacy import _META_FIELDS, _TOOL_FIELDS
        pkg = os.path.join(ROOT, "agentperf")
        notes, tools = set(), set()
        for folder, _dirs, files in os.walk(pkg):
            for name in files:
                if not name.endswith(".py"):
                    continue
                with open(os.path.join(folder, name), encoding="utf-8") as fh:
                    text = fh.read()
                for call in re.findall(r"ctx\.note\(([^)]*)\)", text):
                    notes.update(re.findall(r"(\w+)=", call))
                tools.update(re.findall(r'found\["(\w+)"\]', text))
        self.assertTrue(notes and tools)
        self.assertEqual(notes - set(_META_FIELDS), set())
        self.assertEqual(tools - set(_TOOL_FIELDS), set())

    def test_a_real_result_keeps_every_field_through_share(self):
        from agentperf import scoring
        from agentperf.privacy import anonymize
        from agentperf.runner import run_suite
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            doc = run_suite(os.path.join(tmp, "work"), profile="quick",
                            label="probe",
                            only=["process.python", "process.shell"])
        doc = scoring.validate_result(json.loads(json.dumps(doc)))
        clean, changes = anonymize(doc)
        self.assertFalse(any("does not write" in c for c in changes), changes)
        for key in doc:
            if key not in ("run_id",):
                self.assertIn(key, clean)
        for bid, entry in doc["benchmarks"].items():
            self.assertEqual(sorted(clean["benchmarks"][bid]), sorted(entry))
        self.assertEqual(clean["fingerprint"], doc["fingerprint"])
        self.assertEqual(clean["measurement_env"], doc["measurement_env"])


class ShareFourthReviewTest(unittest.TestCase):
    """Round-9 fourth verification (c1a0a77): R1-R7."""

    def _share(self, patch_system=None, **fields):
        from agentperf.privacy import anonymize
        doc = _load("macos-m1-pro")
        doc["system"].update(patch_system or {})
        doc.update(fields)
        return anonymize(doc)[0]

    def test_machine_text_is_scrubbed_like_free_text(self):
        # R1: a custom power plan named after the host; R3: a domain-
        # qualified Windows profile folder hides the short account name.
        clean = self._share({"hostname": "work",
                             "power_plan": "work performance"})
        self.assertNotIn("work", clean["system"]["power_plan"])
        clean = self._share({"workdir": "C:\\Users\\bob.DOMAIN\\agentperf",
                             "power_plan": "bob performance"})
        self.assertNotIn("bob", clean["system"]["power_plan"])

    def test_ipv6_behind_a_hex_label(self):
        # R2
        from agentperf.privacy import _scrub_ipv6
        self.assertEqual(_scrub_ipv6("IPv6:2001:db8::4"), "IPv6:<ip>")
        self.assertEqual(_scrub_ipv6("[2001:db8::4]:443"), "[<ip>]:443")
        for text in ("std::string", "12:30:45", "::1"):
            self.assertEqual(_scrub_ipv6(text), text)

    def test_unicode_equivalent_names(self):
        # R4: macOS paths can be decomposed; case folding maps ß to ss.
        for home, printed in (("Bo\u0308b", "B\u00f6b"), ("Gro\u00df", "GROSS")):
            clean = self._share({"workdir": "/Users/{}/agentperf".format(home)},
                                tool_versions={"git": "git version 2.51.0." + printed})
            self.assertNotIn(printed, clean["tool_versions"]["git"])

    def test_quote_inside_a_home_folder_name(self):
        # R5
        clean = self._share(skipped={"process.node": 'Permission denied: '
                                     '/Users/Bob"Smith/PrivateClient/bash'})
        self.assertNotIn("PrivateClient", json.dumps(clean))
        # A path ending at a closing quote is ordinary and kept scrubbed.
        clean = self._share(skipped={"process.node": 'cannot open '
                                     '"/Users/Bob/x": denied'})
        self.assertEqual(clean["skipped"]["process.node"],
                         'cannot open "/Users/<user>": denied')

    def test_single_label_mail_domain(self):
        # R6; "python@3.12" (Homebrew) is not an address.
        clean = self._share(skipped={"process.node": "alice@buildbox ran "
                                     "python@3.12"})
        text = clean["skipped"]["process.node"]
        self.assertNotIn("alice@buildbox", text)
        self.assertIn("python@3.12", text)

    def test_generated_values_survive_colliding_names(self):
        # R7: a host named "quick" must not rename the quick profile.
        from agentperf import scoring
        from agentperf.privacy import anonymize
        ref = _load(PROFILES[0])
        for name in PROFILES:
            base = _load(name)
            for profile in ("quick", "standard"):
                for who in ("quick", "standard", "system", "sh", "bash",
                            "process", "work"):
                    doc = copy.deepcopy(base)
                    doc["profile"] = profile
                    doc["system"]["hostname"] = who
                    doc["system"]["workdir"] = "/home/{}/w".format(who)
                    clean, _c = anonymize(doc)
                    self.assertEqual(clean["profile"], profile, (name, who))
                    self.assertEqual(clean["kind"], base["kind"])
                    self.assertEqual(clean["benchmarks"], base["benchmarks"])
                    self.assertEqual(scoring.compatibility_warnings(clean, ref),
                                     scoring.compatibility_warnings(doc, ref))


class ShareFifthReviewTest(unittest.TestCase):
    """Round-9 fifth verification (a1e0b54): N1-N4."""

    def _plan(self, workdir, plan):
        from agentperf.privacy import anonymize
        doc = _load("windows-i7-12700k")
        doc["system"]["workdir"] = workdir
        doc["system"]["power_plan"] = plan
        return anonymize(doc)[0]["system"]["power_plan"]

    def test_quote_split_home_paths_fail_closed(self):
        # N1
        from agentperf.privacy import anonymize
        doc = _load("macos-m1-pro")
        for bad in ('/Users/Bob"!Smith/PrivateClient/bash',
                    '/Users/Bob" Smith/PrivateClient/bash'):
            doc["skipped"] = {"process.node": "error: '{}'".format(bad)}
            text = json.dumps(anonymize(copy.deepcopy(doc))[0])
            self.assertNotIn("PrivateClient", text)
            self.assertNotIn("Smith", text)

    def test_digit_led_mail_host(self):
        # N2
        from agentperf.privacy import _scrub_paths
        self.assertEqual(_scrub_paths("alice@3build"), "<email>")
        self.assertEqual(_scrub_paths("python@3.12"), "python@3.12")

    def test_qualified_and_punctuated_accounts(self):
        # N3
        for home, account in (("_bob.DOMAIN", "bob"), (".bob.DOMAIN", "bob"),
                              ("_al.CORP", "al")):
            plan = self._plan("C:\\Users\\{}\\agentperf".format(home),
                              "{} performance".format(account))
            self.assertNotIn(account, plan.lower(), home)

    def test_unc_and_wsl_homes(self):
        # N4, plus WSL views of Windows homes and other home roots.
        for workdir, account in (
                ("\\\\wsl.localhost\\Ubuntu\\home\\Bob\\agentperf", "Bob"),
                ("\\\\fileserver\\Users\\Al\\agentperf", "Al"),
                ("/mnt/c/Users/Carol/agentperf", "Carol"),
                ("/var/home/dave/agentperf", "dave")):
            plan = self._plan(workdir, "{} performance".format(account))
            self.assertNotIn(account, plan, workdir)


class ShareSixthReviewTest(unittest.TestCase):
    """Round-9 sixth verification (1eab866): P1-P3, plus any absolute
    path in free text."""

    def _skip(self, reason, system=None):
        from agentperf.privacy import anonymize
        doc = _load("macos-m1-pro")
        doc["system"].update(system or {})
        doc["skipped"] = {"process.node": reason}
        return anonymize(doc)[0]

    def test_absolute_paths_in_free_text_are_replaced(self):
        # P1, and non-home folders that can name a client.
        clean = self._skip("error: No such file: "
                           "'/opt/build.10.20.30.40/bin/bash'")
        self.assertEqual(clean["skipped"]["process.node"],
                         "error: No such file: '<path>/bash'")
        clean = self._skip("cannot read 'D:\\Client Plans\\Acme\\notes.txt': "
                           "denied")
        self.assertEqual(clean["skipped"]["process.node"],
                         "cannot read '<path>': denied")
        clean = self._skip("missing C:\\Program Files\\Git\\bin\\bash.exe")
        self.assertEqual(clean["skipped"]["process.node"], "missing <path>/bash.exe")
        clean = self._skip("no '/Users/O'Brien Plans/x' and '/srv/Acme Co/y' here")
        self.assertNotIn("Brien", json.dumps(clean))
        self.assertNotIn("Acme", json.dumps(clean))
        for text in ("HTTP 403 from https://api.github.com/repos",
                     "rg 14 MB/s and/or N/A", "zlib/1.2.12 liblzma/5.4.3"):
            self.assertEqual(self._skip(text)["skipped"]["process.node"], text)

    def test_short_names_glued_to_words(self):
        # P2
        for system, plan in (({"workdir": "/home/Al/w"}, "AlLaptop"),
                             ({"hostname": "pc"}, "pcLaptop")):
            from agentperf.privacy import anonymize
            doc = _load("windows-i7-12700k")
            doc["system"].update(system)
            doc["system"]["power_plan"] = plan
            out = anonymize(doc)[0]["system"]["power_plan"]
            self.assertNotIn(plan[:2], out)
            self.assertIn(out, ("<name>Laptop", "<withheld>"))
        doc = _load("windows-i7-12700k")
        doc["system"]["workdir"] = "/home/Al/w"
        doc["system"]["power_plan"] = "Balanced"
        from agentperf.privacy import anonymize
        self.assertEqual(anonymize(doc)[0]["system"]["power_plan"], "Balanced")

    def test_configurable_file_system_type(self):
        # P3
        from agentperf.privacy import anonymize
        doc = _load("wsl2-i7-12700k")
        doc["system"]["hostname"] = "work"
        doc["system"]["fs_type"] = "fuse.work"
        self.assertNotIn("work", anonymize(doc)[0]["system"]["fs_type"])


class ShareSeventhReviewTest(unittest.TestCase):
    """Round-9 seventh verification (98e1b2d): Q1-Q3."""

    def _skip(self, reason, system=None):
        from agentperf.privacy import anonymize
        doc = _load("macos-m1-pro")
        doc["system"].update(system or {})
        doc["skipped"] = {"process.node": reason}
        return anonymize(doc)[0]["skipped"]["process.node"]

    def test_diagnostics_survive_path_replacement(self):
        # Q1: the tool and its exit status stay readable.
        self.assertEqual(self._skip("error: /opt/x/bash exited with code 7"),
                         "error: <path>/bash exited with code 7")
        # Something path-like later on the line keeps the path running.
        self.assertNotIn("Sons", self._skip("D:\\Clients\\Acme and Sons\\x exited"))

    def test_spawn_errors_name_the_tool_not_its_path(self):
        from agentperf.benches.process import _spawn_ms
        with self.assertRaises(RuntimeError) as caught:
            _spawn_ms([sys.executable, "-c", "import sys; sys.exit(7)"], 1)
        self.assertEqual(str(caught.exception),
                         "{} exited with code 7".format(
                             os.path.basename(sys.executable)))

    def test_short_and_uncased_names_inside_words(self):
        # Q2
        for system, text, name in (({"workdir": "/home/Al/w"}, "Albuild", "Al"),
                                   ({"hostname": "pc"}, "pcbuild", "pc"),
                                   ({"workdir": "/home/\u674e\u660e/w"},
                                    "\u674e\u660eLaptop", "\u674e\u660e")):
            self.assertNotIn(name, self._skip(text, system))
        self.assertEqual(self._skip("Balanced", {"workdir": "/home/Al/w"}),
                         "Balanced")

    def test_home_folder_starting_with_a_quote(self):
        # Q3
        out = self._skip('error: /tmp/home/"bob/PrivateClient/bash exited '
                         'with code 7', {"workdir": '/home/"bob/agentperf'})
        self.assertNotIn("bob", out)
        self.assertNotIn("PrivateClient", out)


class ShareEighthReviewTest(unittest.TestCase):
    """Round-9 eighth verification (ce97845): Z1-Z5."""

    def test_interior_quote_in_a_home_folder(self):
        # Z1
        from agentperf.privacy import anonymize
        doc = _load("macos-m1-pro")
        doc["system"]["workdir"] = '/Users/a"bob/agentperf'
        doc["tool_versions"]["bash"] = 'GNU bash, built by a"bob'
        text = json.dumps(anonymize(doc)[0]["tool_versions"])
        self.assertNotIn("bob", text)

    def test_custom_shell_and_tool_names_are_free_text(self):
        # Z2: a known shell stays; a custom $SHELL loses the name in it.
        from agentperf.privacy import anonymize
        doc = _load("macos-m1-pro")
        doc["system"]["workdir"] = "/home/Al/agentperf"
        doc["benchmarks"]["diag.login_shell"]["meta"]["shell"] = "alshell"
        doc["tools"]["shell"] = "alshell"
        clean = anonymize(doc)[0]
        self.assertNotIn("al", clean["benchmarks"]["diag.login_shell"]["meta"]["shell"])
        self.assertNotIn("al", clean["tools"]["shell"])
        doc = _load("macos-m1-pro")
        doc["system"]["hostname"] = "bash"
        clean = anonymize(doc)[0]
        self.assertEqual(clean["benchmarks"]["process.shell"]["meta"]["shell"],
                         doc["benchmarks"]["process.shell"]["meta"]["shell"])
        self.assertEqual(clean["tools"], doc["tools"])

    def test_timeouts_and_exits_name_the_tool(self):
        # Z3
        import subprocess
        from agentperf.runner import _describe_error
        exc = subprocess.TimeoutExpired(["/Users/Bob/x/bash", "-c", "true"], 30)
        self.assertEqual(_describe_error(exc), "bash timed out after 30 s")
        exc = subprocess.CalledProcessError(3, ["C:\\\\Users\\\\Bob\\\\git.exe", "status"])
        self.assertEqual(_describe_error(exc), "git.exe exited with code 3")

    def test_timestamp_cut_is_reported(self):
        # Z4
        from agentperf.privacy import anonymize
        doc = _load("macos-m1-pro")
        doc["started_at"] = "2026-10-13T10:11:12"
        clean, changes = anonymize(doc)
        self.assertEqual(clean["started_at"], "2026-10-13")
        self.assertTrue(any("to the day" in c for c in changes), changes)

    def test_numeric_single_label_mail_host(self):
        # Z5
        from agentperf.privacy import _scrub_paths
        self.assertEqual(_scrub_paths("alice@123"), "<email>")
        self.assertEqual(_scrub_paths("python@3.12"), "python@3.12")
