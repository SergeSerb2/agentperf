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
    return {"label": label, "benchmarks": benches, "skipped": {}, "system": system or {}}


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


class PrivacyTest(unittest.TestCase):
    def test_anonymize_strips_private_fields(self):
        doc = _doc("my-laptop", {"process.shell": _entry("process", 5.0)}, {
            "hostname": "my-laptop.local", "workdir": "/Users/alice/.agentperf/work",
            "cpu_model": "Apple M1", "os_family": "macos",
            "top_processes": [{"name": "SecretApp", "cpu": 99.0}],
            "security_agents": ["Bitdefender"],
            "antivirus": [{"name": "Bitdefender Antivirus", "active": True}],
        })
        doc["run_id"] = "abc"
        doc["tools"] = {"git": "/Users/alice/bin/git"}
        clean, changes = privacy.anonymize(doc)
        text = json.dumps(clean)
        for needle in ("alice", "my-laptop", "Bitdefender", "SecretApp", "abc"):
            self.assertNotIn(needle, text)
        self.assertEqual(clean["system"]["processes_over_80pct"], 1)
        self.assertTrue(clean["system"]["antivirus"][0]["active"])
        self.assertTrue(clean["anonymized"])
        self.assertIn("hostname", doc["system"])  # input not mutated


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
            with open(html_path, encoding="utf-8") as fh:
                self.assertIn("Biggest wins", fh.read())
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
