"""Offline batch tests using temporary manifests and real local receipts."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from batch import summarize
from prompt_package import build_package
from receipts import Refused, apply, event


class BatchTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.manifest = self.folder / "batch.json"
        self.record = self.folder / "ep01.sqlite"
        self.scope = {"project": "demo", "episode": "01", "target_url": "https://example.invalid/ep01"}
        self.body = self.folder / "prompt.md"
        self.body.write_text("Synthetic prompt", encoding="utf-8")
        package = build_package(self.body, "native", 5, [])
        self.spec = {"card": "card", "prompt_sha256": package["prompt_sha256"], "references": [], "mode": "native",
                     "seconds": 5, "model": "example", "resolution": "demo"}
        self.entry = {"episode": "01", "record": "ep01.sqlite", "tasks": [
            {"id": "A"}, {"id": "B", "source": "A"}, {"id": "C"}]}
        apply(self.record, "init", {"scope": self.scope, "authorization": {
            "mode": "generate", "quote": "fixture", "source": "test"}})

    def report(self, *entries):
        self.manifest.write_text(json.dumps({"project": "demo", "episodes": entries or [self.entry]}),
                                 encoding="utf-8")
        return summarize(self.manifest)

    def register(self, name, spec=None):
        spec = spec or {**self.spec, "card": name}
        package = build_package(self.body, spec["mode"], spec["seconds"], [])
        apply(self.record, "register", {"id": name, "spec": spec, "package": package})

    def intent(self, name, card=None):
        apply(self.record, "intent", {"id": name, "visible": {**self.spec, "card": card or name},
                                     "scope": self.scope, "no_active_same_task": True, "evidence": "fixture"})

    def observe(self, name, state, **details):
        apply(self.record, "observe", {"id": name, "state": state, "evidence": "fixture", **details})

    def tasks(self):
        return {t["id"]: t for t in self.report()["episodes"][0]["tasks"]}

    def test_scope_mismatch_isolated_and_missing_record_not_created(self):
        second = {"episode": "02", "record": "ep02.sqlite", "tasks": [{"id": "x"}]}
        wrong = {"episode": "03", "record": self.entry["record"]}
        report = self.report(second, wrong)
        self.assertEqual([e["record_state"] for e in report["episodes"]], ["missing", "error"])
        self.assertFalse((self.folder / "ep02.sqlite").exists())
        self.assertEqual(report["episodes"][0]["tasks"][0]["next"], "locate_record")
        self.assertFalse(report["episodes"][1]["tasks"])

    def test_explicit_target_url_must_match(self):
        self.entry["target_url"] = "https://example.invalid/other-production"
        self.assertEqual(self.report()["episodes"][0]["record_state"], "error")

    def test_invalid_duplicate_record_and_plan_id(self):
        with self.assertRaises(Refused):
            self.report(self.entry, {"episode": "02", "record": "./ep01.sqlite"})
        self.entry["tasks"].append({"id": "A"})
        with self.assertRaises(Refused):
            self.report()

    def test_dependency_cycles_and_extension_of_extension_are_rejected(self):
        for first, second in (("B", "A"), ("C", "A")):
            self.entry["tasks"] = [{"id": "A", "source": first}, {"id": "B", "source": second},
                                   {"id": "C"}]
            with self.assertRaises(Refused):
                self.report()

    def test_planned_package_checked_before_registration_and_after_edits(self):
        package_path = self.folder / "A.json"
        self.entry["tasks"][0]["package"] = package_path.name
        self.assertEqual(self.tasks()["A"]["package"]["state"], "missing")
        package_path.write_text(json.dumps(build_package(self.body, "native", 5, [])), encoding="utf-8")
        self.assertEqual(self.tasks()["A"]["package"]["state"], "verified")
        self.assertEqual(self.tasks()["A"]["prompt_path"], str(self.body))
        self.register("A")
        self.body.write_text("changed", encoding="utf-8")
        self.assertEqual(self.tasks()["A"]["next"], "check_package")
        package_path.write_text(json.dumps(build_package(self.body, "native", 5, [])), encoding="utf-8")
        self.assertEqual(self.tasks()["A"]["package"]["state"], "error")

    def test_supplements_plan_with_registered_roots_and_does_not_touch_record(self):
        self.register("extra")
        before = self.record.read_bytes()
        tasks = self.tasks()
        self.assertEqual(tasks["A"]["next"], "prepare_card")
        self.assertEqual(tasks["B"]["next"], "wait_for_source")
        self.assertEqual(tasks["extra"]["next"], "verify_current_card")
        self.assertEqual(tasks["extra"]["references"], [])
        self.assertEqual(tasks["extra"]["prompt_path"], str(self.body))
        self.assertEqual(self.record.read_bytes(), before)
        self.assertNotIn("click_once", json.dumps(self.report()))

    def test_unknown_receipt_blocks_new_same_episode_click_and_retry(self):
        self.register("A")
        self.register("C")
        self.intent("A")
        self.assertEqual(self.tasks()["A"]["next"], "reconcile_original_card")
        self.observe("A", "unknown")
        self.assertEqual(self.tasks()["C"]["next"], "reconcile_episode_history")
        self.observe("A", "processing")
        tasks = self.tasks()
        self.assertEqual(tasks["A"]["next"], "poll_original_card")
        self.assertEqual(tasks["C"]["next"], "verify_current_card")

    def test_prepare_scope_and_pause(self):
        self.register("A")
        apply(self.record, "authorize", {"mode": "prepare"})
        self.assertEqual(self.tasks()["A"]["next"], "prepare_only")
        apply(self.record, "pause", {"evidence": "fixture"})
        self.assertEqual(self.tasks()["C"]["next"], "paused")
        self.assertEqual(self.tasks()["A"]["next"], "paused")

    def test_only_actual_saved_card_matches_adopted_spec(self):
        self.register("A")
        apply(self.record, "authorize", {"mode": "prepare"})
        saved = {**self.spec, "card": "A"}
        apply(self.record, "confirm_prepared", {"scope": self.scope, "id": "A", "visible": saved,
                                               "evidence": "fixture saved and read back"})
        self.assertEqual(self.tasks()["A"]["next"], "saved_for_user")
        package = build_package(self.body, "native", 6, [])
        apply(self.record, "amend", {"id": "A", "spec": {**saved, "seconds": 6}, "package": package,
                                    "evidence": "fixture new timing"})
        self.assertEqual(self.tasks()["A"]["next"], "prepare_only")

    def test_extension_checks_exact_source_and_keeps_retry_under_root(self):
        self.register("A")
        self.intent("A")
        self.observe("A", "failed", cause="platform")
        self.assertEqual(self.tasks()["B"]["next"], "review_source_failure")
        apply(self.record, "retry", {"id": "A", "new_id": "A2", "no_active_same_task": True,
                                    "condition_evidence": "fixture"})
        self.intent("A2", card="A")
        self.observe("A2", "completed", result_id="chosen")
        self.assertEqual(self.tasks()["B"]["next"], "verify_source_interval")
        extension = {**self.spec, "mode": "extend", "source": {"task": "A2", "result_id": "chosen",
                     "start": 0, "end": 4, "qualified": True, "evidence": "fixture"}}
        self.register("B", extension)
        tasks = self.tasks()
        self.assertNotIn("A2", tasks)
        self.assertEqual(tasks["A"]["attempt"], "A2")
        self.assertEqual(tasks["B"]["source"], "A")
        self.assertEqual(tasks["B"]["next"], "verify_current_card")
        self.register("C", {**extension, "source": {**extension["source"], "result_id": "other"}})
        self.assertEqual(self.tasks()["C"]["next"], "check_source_binding")

    def test_only_archive_receipt_for_exact_result_counts(self):
        self.register("A")
        self.intent("A")
        self.observe("A", "completed", result_id="chosen")
        self.assertEqual(self.tasks()["A"]["next"], "check_download")
        clip = self.folder / "clip.bin"
        clip.write_bytes(b"fixture")
        # Fixtures mimic committed archive events, including an unrelated result.
        archive = {"id": "A", "result_id": "other", "path": str(clip), "size": 7, "sha256": "b" * 64}
        with closing(sqlite3.connect(self.record)) as con:
            event(con, "archive", archive)
            con.commit()
            self.assertEqual(self.tasks()["A"]["next"], "check_download")
        apply(self.record, "archive", {"id": "A", "result_id": "chosen", "path": str(clip),
                                      "evidence": "fixture"})
        self.assertEqual(self.tasks()["A"]["next"], "archive_recorded")
        clip.write_bytes(b"changed-size")
        self.assertEqual(self.tasks()["A"]["next"], "check_archived_file")
        clip.unlink()
        self.assertEqual(self.tasks()["A"]["next"], "locate_archived_file")


if __name__ == "__main__":
    unittest.main()
