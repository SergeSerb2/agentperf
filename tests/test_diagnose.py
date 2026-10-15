"""Tests for the v0.2 explanation layer: diagnosis, privacy, bundled refs."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from agentperf import diagnose, privacy


def _entry(category, median, lower=True, weight=1.0, scored=True, samples=None, ops=1):
    return {"category": category, "title": category, "unit": "ms" if lower else "MB/s",
            "lower_is_better": lower, "weight": weight, "scored": scored, "ops": ops,
            "median": median, "samples": samples or [median], "cv": 0.0, "noisy": False, "meta": {}}


def _doc(label, benches, system=None):
    return {"label": label, "profile": "standard", "benchmarks": benches,
            "skipped": {}, "system": system or {}}


REF = _doc("ref", {
    "process.shell": _entry("process", 10.0),
    "process.exec": _entry("process", 5.0),
    "files.read": _entry("files", 100.0, ops=2000),
    "compute.sha256": _entry("compute", 1000.0, lower=False),
}, {"cpu_model": "RefCPU", "python": "3.12.0"})


class DiagnoseTest(unittest.TestCase):
    def test_gain_if_fixed_reaches_reference(self):
        half = _doc("half", {
            "process.shell": _entry("process", 20.0),
            "process.exec": _entry("process", 10.0),
            "files.read": _entry("files", 200.0, ops=2000),
            "compute.sha256": _entry("compute", 500.0, lower=False),
        })
        gain = diagnose.gain_if_fixed(half, REF, list(half["benchmarks"]))
        self.assertAlmostEqual(gain["base"], 500.0)
        self.assertAlmostEqual(gain["if_fixed"], 1000.0)

    def test_attribution_covers_categories(self):
        attr = diagnose.attribution(REF, REF)
        self.assertEqual(attr["biggest_drags"], [])
        self.assertAlmostEqual(attr["score"]["agentmark"], 1000.0)

    def test_antivirus_cause_needs_probe_and_pattern(self):
        slow = _doc("slow", {
            "process.shell": _entry("process", 100.0),
            "process.exec": _entry("process", 50.0),
            "files.read": _entry("files", 1000.0, ops=2000),
            "compute.sha256": _entry("compute", 1000.0, lower=False),
        }, {"antivirus": [{"name": "X", "active": True}]})
        topics = [c["topic"] for c in diagnose.causes(slow, REF)]
        self.assertTrue(any("Antivirus" in t for t in topics))
        for c in diagnose.causes(slow, REF):
            self.assertTrue(c["evidence"], "every cause must cite evidence")

    def test_no_cause_without_evidence_on_reference(self):
        self.assertEqual(diagnose.causes(REF, REF), [])

    def test_windows_gets_wsl2_win_ranked_first(self):
        win = _doc("win", {
            "process.shell": _entry("process", 100.0),
            "process.exec": _entry("process", 50.0),
            "files.read": _entry("files", 1000.0, ops=2000),
            "compute.sha256": _entry("compute", 1000.0, lower=False),
        }, {"os_family": "windows", "cpu_model": "RefCPU", "python": "3.12.0"})
        items = diagnose.causes(win, REF)
        self.assertTrue(items)
        self.assertIn("WSL2", items[0]["topic"])
        # The same-PC gap must carry its conditions, never a bare number.
        evidence = " ".join(items[0]["evidence"])
        self.assertIn("third-party real-time antivirus", evidence)
        self.assertIn("Windows Search indexer", evidence)
        self.assertIn("Git Bash", evidence)
        self.assertIn("no run with", evidence)
        self.assertTrue(items[0]["evidence"])

    def test_mnt_workdir_is_top_finding_in_wsl(self):
        doc = _doc("wsl", {
            "process.shell": _entry("process", 100.0),
            "files.read": _entry("files", 5000.0, ops=2000),
            "compute.sha256": _entry("compute", 100.0, lower=False),
        }, {"os_family": "wsl", "wsl": True, "workdir": "/mnt/c/code/repo",
            "cpu_model": "OtherCPU", "python": "3.12.0"})
        items = diagnose.causes(doc, REF)
        self.assertIn("/mnt/", items[0]["topic"] + items[0]["summary"])
        self.assertEqual(items[0]["confidence"], "observed")

    def test_mnt_detection_from_cwd_flag(self):
        # Launch dir on /mnt/ only: noted, but the score is not compromised,
        # so it must not be the top "nothing else matters" finding.
        doc = _doc("wsl", {"files.read": _entry("files", 500.0, ops=2000)},
                   {"os_family": "wsl", "cwd_on_windows_drive": True})
        items = diagnose.causes(doc, REF)
        mnt = [c for c in items if "/mnt/" in c["topic"]]
        self.assertTrue(mnt)
        self.assertIn("not affected", mnt[0]["summary"])
        self.assertEqual(mnt[0]["affected"], [])
        self.assertLess(mnt[0]["priority"], 100)

    def test_fast_native_windows_is_not_told_to_move_to_wsl2(self):
        fast_win = _doc("win", {
            "process.shell": _entry("process", 9.0),
            "process.exec": _entry("process", 4.0),
            "files.read": _entry("files", 90.0, ops=2000),
            "compute.sha256": _entry("compute", 1100.0, lower=False),
        }, {"os_family": "windows", "cpu_model": "OtherCPU", "python": "3.12.0"})
        topics = [c["topic"] for c in diagnose.causes(fast_win, REF)]
        self.assertFalse(any("WSL2" in t for t in topics), topics)

    def test_minor_wins_are_partitioned_below_five_points(self):
        major, minor = diagnose.partition_causes([
            {"topic": "big", "gain_points": 94.0, "affected": ["a"]},
            {"topic": "tiny", "gain_points": 0.825, "affected": ["b"]},
            {"topic": "unscored", "gain_points": 0.0, "affected": []},
        ])
        self.assertEqual([c["topic"] for c in major], ["big", "unscored"])
        self.assertEqual([c["topic"] for c in minor], ["tiny"])


class GainFormatTest(unittest.TestCase):
    def test_gains_render_as_whole_numbers(self):
        from agentperf.util import fmt_gain
        self.assertEqual(fmt_gain(94.0), "94")
        self.assertEqual(fmt_gain(0.825), "1")
        self.assertEqual(fmt_gain(1234.4), "1,234")
        self.assertEqual(fmt_gain(None), "-")
        self.assertEqual(fmt_gain(0), "0")


class PrivacyTest(unittest.TestCase):
    def test_anonymize_strips_private_fields(self):
        doc = _doc("my-laptop", {"process.shell": _entry("process", 5.0)}, {
            "hostname": "my-laptop.local", "workdir": "/Users/alice/.agentperf/work",
            "cpu_model": "Apple M1", "os_family": "macos",
            "top_processes": [{"name": "SecretApp", "cpu": 99.0}],
            "security_agents": ["ExampleAV"],
            "antivirus": [{"name": "ExampleAV Antivirus", "active": True}],
        })
        doc["run_id"] = "abc"
        doc["tools"] = {"git": "/Users/alice/bin/git"}
        clean, changes = privacy.anonymize(doc)
        text = json.dumps(clean)
        for needle in ("alice", "my-laptop", "ExampleAV", "SecretApp", "abc"):
            self.assertNotIn(needle, text)
        self.assertEqual(clean["system"]["processes_over_80pct"], 1)
        self.assertTrue(clean["system"]["antivirus"][0]["active"])
        self.assertTrue(clean["anonymized"])
        self.assertIn("hostname", doc["system"])  # input not mutated


    def test_anonymize_scrubs_addresses_and_windows_paths_in_free_text(self):
        # Sensitive literals are assembled from parts so the public tree stays
        # clean under the privacy grep (see SECURITY.md / release checklist).
        b = chr(92)  # backslash
        win_home = "C:" + b + "Users" + b + "alice"
        ip_a = ".".join(("192", "168", "1", "42"))
        ip_b = ".".join(("100", "101", "102", "103"))
        ip_c = ".".join(("10", "0", "0", "5"))
        tail = "box.example" + ".ts" + ".net"
        doc = _doc("alice-box", {"process.shell": _entry("process", 5.0)}, {
            "hostname": "alice-box.local", "workdir": win_home + b + ".agentperf" + b + "work",
            "cpu_model": "Some CPU", "os_family": "windows",
            "note": ("ssh alice-box at {} and {}, mac aa:bb:cc:dd:ee:ff, host {}, "
                     "user alice, ipv6 fe80::1ff:fe23:4567, paths /home/alice/x and "
                     "{} and C:/Users/alice/z").format(
                         ip_a, ip_b, tail, win_home + b + "y"),
        })
        doc["skipped"] = {"x": "failed for alice at " + ip_c}
        clean, changes = privacy.anonymize(doc)
        text = json.dumps(clean)
        for needle in ("alice", "alice-box", ip_a, ip_b, ip_c,
                       "aa:bb", "fe80::", ".ts" + ".net"):
            self.assertNotIn(needle, text, needle)
        self.assertTrue(any("scrubbed" in c for c in changes), changes)


class BundledReferencesTest(unittest.TestCase):
    def test_bundled_profiles_are_clean_and_anchor_scores_1000(self):
        from agentperf import scoring
        from agentperf.cli import _all_bundled_references, _load_reference
        docs = _all_bundled_references()
        self.assertGreaterEqual(len(docs), 3)
        anchor = _load_reference(None)
        self.assertIsNotNone(anchor)
        self.assertAlmostEqual(scoring.score(anchor, anchor)["agentmark"], 1000.0)
        for doc in docs:
            text = json.dumps(doc)
            for needle in ("/Users/", "/home/", "hostname"):
                self.assertNotIn(needle, text, doc["label"])
            self.assertNotIn("top_processes", doc["system"])


class HtmlEscapeTest(unittest.TestCase):
    def test_markup_in_labels_is_inert_in_both_reports(self):
        from agentperf.report_html import write_html, write_report
        payload = '<img src=x onerror=alert(1)><script>alert(2)</script>'
        ref = _doc("ref", REF["benchmarks"], dict(REF["system"]))
        ref.update({"suite_version": "0.2.0", "schema": 1, "profile": "quick",
                    "corpus": {"fixture_hash": "x"}})
        evil = json.loads(json.dumps(ref))
        evil["label"] = payload
        evil["system"]["cpu_model"] = payload
        tmp = tempfile.mkdtemp()
        try:
            for name, write in (("single", lambda p: write_report(evil, ref, p)),
                                ("compare", lambda p: write_html([evil], ref, p))):
                path = os.path.join(tmp, name + ".html")
                write(path)
                with open(path, encoding="utf-8") as fh:
                    page = fh.read()
                self.assertNotIn("<img src=x", page, name)
                self.assertNotIn("<script>alert(2)", page, name)
                self.assertIn("&lt;img", page, name)
        finally:
            shutil.rmtree(tmp)


class CliSmokeTest(unittest.TestCase):
    def test_share_and_report_commands(self):
        from agentperf.cli import main
        tmp = tempfile.mkdtemp()
        try:
            src = os.path.join(tmp, "r.json")
            doc = _doc("mine", {k: v for k, v in REF["benchmarks"].items()}, {"hostname": "h"})
            doc.update({"suite_version": "0.2.0", "schema": 1, "profile": "quick",
                        "corpus": {"fixture_hash": "x"}, "system": dict(REF["system"], hostname="h")})
            with open(src, "w", encoding="utf-8") as fh:
                json.dump(doc, fh)
            out = os.path.join(tmp, "share.json")
            self.assertEqual(main(["share", src, "--out", out]), 0)
            html_path = os.path.join(tmp, "r.html")
            self.assertEqual(main(["report", src, "--html", html_path]), 0)
            # Expected failures are clean messages with rc 2, not tracebacks.
            self.assertEqual(main(["report", os.path.join(tmp, "missing.json")]), 2)
            bad = os.path.join(tmp, "bad.json")
            with open(bad, "w", encoding="utf-8") as fh:
                fh.write("not json")
            self.assertEqual(main(["share", bad]), 2)
            with open(bad, "w", encoding="utf-8") as fh:
                json.dump({"hello": 1}, fh)
            self.assertEqual(main(["compare", bad]), 2)
            with open(html_path, encoding="utf-8") as fh:
                self.assertIn("Biggest wins", fh.read())
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
