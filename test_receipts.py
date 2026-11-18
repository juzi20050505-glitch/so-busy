"""Synthetic transition tests; no platform, accounts or real project evidence."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
from pathlib import Path
import sqlite3
import tempfile
import unittest

from receipts import Refused, apply, status
from prompt_package import build_package


class ReceiptsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "run.sqlite"
        self.body = Path(self.tmp.name) / "adopted.txt"
        self.body.write_text("She hands him the box.", encoding="utf-8")
        self.package = build_package(self.body, "native", 10, ["actor"])
        self.scope = {"project": "synthetic", "episode": "01", "target_url": "https://example.invalid/p"}
        self.auth = {"mode": "generate", "quote": "synthetic test authorization", "source": "fixture"}
        self.spec = {"card": "synthetic-card", "prompt_sha256": self.package["prompt_sha256"],
                     "references": [{"name": "actor", "node": "synthetic-node"}],
                     "mode": "native", "seconds": 10, "model": "test-model", "resolution": "test"}
        apply(self.path, "init", {"scope": self.scope, "authorization": self.auth})
        self.register("s1", self.spec)

    def register(self, task_id, spec):
        package = build_package(self.body, spec["mode"], spec["seconds"],
                                [ref["name"] for ref in spec["references"]])
        apply(self.path, "register", {"id": task_id, "spec": spec, "package": package})

    def intent(self, task_id="s1", visible=None):
        return apply(self.path, "intent", {"id": task_id, "scope": self.scope,
                     "visible": visible or self.spec, "no_active_same_task": True,
                     "evidence": "synthetic same-page verification"})

    def observe(self, state, task_id="s1", **extra):
        return apply(self.path, "observe", {"id": task_id, "state": state,
                     "evidence": "synthetic visible receipt", **extra})

    def test_prepare_cannot_submit(self):
        apply(self.path, "authorize", {"mode": "prepare"})
        with self.assertRaises(Refused):
            self.intent()

    def test_authorization_needs_human_source(self):
        with self.assertRaises(Refused):
            apply(self.path, "authorize", {"mode": "generate"})

    def test_existing_record_cannot_reset(self):
        with self.assertRaises(Refused):
            apply(self.path, "init", {"scope": self.scope, "authorization": self.auth})

    def test_status_never_reissues_permission(self):
        self.assertEqual(self.intent()["click_once"], "s1")
        self.assertNotIn("click_once", status(self.path))
        with self.assertRaises(Refused):
            self.intent()

    def test_page_reference_mismatch_refused(self):
        changed = deepcopy(self.spec)
        changed["references"][0]["node"] = "wrong-node"
        with self.assertRaises(Refused):
            self.intent(visible=changed)

    def test_page_seconds_mismatch_refused(self):
        with self.assertRaises(Refused):
            self.intent(visible={**self.spec, "seconds": 11})

    def test_unknown_attempt_blocks_other_tasks(self):
        self.intent()
        self.observe("unknown")
        self.register("s2", {**self.spec, "card": "synthetic-other"})
        with self.assertRaises(Refused):
            self.intent("s2", {**self.spec, "card": "synthetic-other"})

    def test_processing_native_does_not_block_independent_native(self):
        self.intent()
        self.observe("processing")
        spec = {**self.spec, "card": "synthetic-other"}
        self.register("s2", spec)
        self.assertEqual(self.intent("s2", spec)["click_once"], "s2")

    def test_completed_requires_actual_result(self):
        self.intent()
        with self.assertRaises(Refused):
            self.observe("completed")

    def test_another_task_id_cannot_resubmit_processing_card(self):
        self.intent()
        self.observe("processing")
        self.register("other", self.spec)
        with self.assertRaises(Refused):
            self.intent("other")

    def test_balance_recovery_keeps_original_failure(self):
        self.intent()
        self.observe("failed", cause="balance")
        apply(self.path, "retry", {"id": "s1", "new_id": "s1-retry",
              "condition_evidence": "synthetic balance recovered; original not accepted",
              "no_active_same_task": True})
        self.intent("s1-retry")
        tasks = status(self.path)["tasks"]
        self.assertEqual(tasks[0]["state"], "failed")
        self.assertEqual(tasks[0]["cause"], "balance")
        self.assertEqual(tasks[1]["root"], "s1")
        self.assertEqual(tasks[1]["retry_of"], "s1")

    def test_recovery_evidence_required(self):
        self.intent()
        self.observe("failed", cause="balance")
        with self.assertRaises(Refused):
            apply(self.path, "retry", {"id": "s1", "new_id": "retry", "no_active_same_task": True})

    def test_unknown_and_processing_cannot_retry(self):
        self.intent()
        for state in ("unknown", "processing"):
            self.observe(state)
            with self.assertRaises(Refused):
                apply(self.path, "retry", {"id": "s1", "new_id": "retry",
                      "condition_evidence": "synthetic", "no_active_same_task": True})

    def test_creative_failure_not_technical_retry(self):
        self.intent()
        self.observe("failed", cause="model")
        with self.assertRaises(Refused):
            apply(self.path, "retry", {"id": "s1", "new_id": "retry",
                  "condition_evidence": "synthetic", "no_active_same_task": True})

    def test_pause_resume_does_not_reset_intent(self):
        self.intent()
        apply(self.path, "pause", {"evidence": "synthetic user pause"})
        apply(self.path, "resume", {"evidence": "synthetic user resume; page reconciled"})
        with self.assertRaises(Refused):
            self.intent()
        self.observe("completed", result_id="synthetic-result")

    def test_concurrent_intent_gets_one_permission(self):
        def attempt(_):
            try:
                return self.intent()
            except Refused:
                return {}
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, range(2)))
        self.assertEqual(sum("click_once" in result for result in results), 1)

    def test_missing_canonical_record_not_recreated(self):
        with self.assertRaises(Refused):
            status(Path(self.tmp.name) / "missing.sqlite")

    def test_failed_transaction_keeps_prepared_state(self):
        with closing(sqlite3.connect(self.path)) as con:
            con.execute("CREATE TRIGGER refuse_event BEFORE INSERT ON events "
                        "BEGIN SELECT RAISE(ABORT, 'synthetic storage failure'); END")
            con.commit()
        with self.assertRaises(sqlite3.Error):
            self.intent()
        self.assertEqual(status(self.path)["tasks"][0]["state"], "prepared")

    def test_extension_waits_for_exact_source(self):
        spec = {**self.spec, "card": "synthetic-extension", "mode": "extend", "seconds": 3,
                "source": {"task": "s1", "result_id": "synthetic-result", "start": 0,
                           "end": 9, "qualified": True, "evidence": "synthetic source check"}}
        self.register("s2", spec)
        with self.assertRaises(Refused):
            self.intent("s2", spec)
        self.intent()
        self.observe("completed", result_id="synthetic-result")
        self.assertEqual(self.intent("s2", spec)["click_once"], "s2")

    def test_package_required_for_new_record(self):
        with self.assertRaises(Refused):
            apply(self.path, "register", {"id": "s2", "spec": self.spec})

    def test_prepare_saved_then_amended(self):
        apply(self.path, "authorize", {"mode": "prepare"})
        data = {"id": "s1", "scope": self.scope, "visible": self.spec, "evidence": "saved card matches"}
        self.assertNotIn("click_once", apply(self.path, "confirm_prepared", data))
        with self.assertRaises(Refused):
            self.intent()
        apply(self.path, "amend", {"id": "s1", "evidence": "new draft card", "package": self.package,
                                   "spec": {**self.spec, "card": "other-card"}})
        with self.assertRaises(Refused):
            apply(self.path, "confirm_prepared", data)
        with closing(sqlite3.connect(self.path)) as con:
            self.assertEqual(0, con.execute("SELECT count(*) FROM saved_cards").fetchone()[0])

    def test_changed_file_requires_readoption_before_click(self):
        self.body.write_text("She closes the box.", encoding="utf-8")
        with self.assertRaises(Refused):
            self.intent()
        updated = build_package(self.body, "native", 10, ["actor"])
        spec = {**self.spec, "prompt_sha256": updated["prompt_sha256"]}
        amend = {"id": "s1", "evidence": "director adopted revision", "spec": spec, "package": updated}
        apply(self.path, "amend", amend)
        self.assertEqual(self.intent(visible=spec)["click_once"], "s1")
        with self.assertRaises(Refused):
            apply(self.path, "amend", amend)

    def test_legacy_record_resumes_without_package(self):
        # An actual v0.1 schema has no package table or format marker.
        with closing(sqlite3.connect(self.path)) as con:
            con.execute("DROP TABLE task_packages")
            con.execute("DELETE FROM meta WHERE key='workflow_version'")
            con.commit()
        self.assertEqual(self.intent()["click_once"], "s1")

    def test_retry_verifies_the_original_adopted_file(self):
        self.intent()
        self.observe("failed", cause="platform")
        apply(self.path, "retry", {"id": "s1", "new_id": "another", "condition_evidence": "recovered",
                                   "no_active_same_task": True})
        self.body.unlink()
        with self.assertRaises(Refused):
            self.intent("another")

    def test_archive_requires_exact_result_and_is_idempotent(self):
        media = Path(self.tmp.name) / "result.mp4"
        media.write_bytes(b"synthetic video contents")
        data = {"id": "s1", "path": str(media), "result_id": "r1", "evidence": "fixture download"}
        with self.assertRaises(Refused):
            apply(self.path, "archive", data)
        self.intent()
        self.observe("completed", result_id="r1")
        with self.assertRaises(Refused):
            apply(self.path, "archive", {**data, "result_id": "other"})
        apply(self.path, "archive", data)
        self.assertTrue(apply(self.path, "archive", data)["already_recorded"])
        media.write_bytes(b"other")
        with self.assertRaises(Refused):
            apply(self.path, "archive", data)
        self.assertEqual(1, sum(e["op"] == "archive" for e in status(self.path)["events"]))


if __name__ == "__main__":
    unittest.main()
