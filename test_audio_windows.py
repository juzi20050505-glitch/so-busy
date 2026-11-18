"""Synthetic tests for original-track identity and estimated source-to-plan mapping."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import wave

from audio_windows import audio_windows, file_sha


class AudioWindowsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.audio = root / "原音.wav"
        self.script = root / "台词.json"
        self.result = root / "result.json"
        with wave.open(str(self.audio), "wb") as wav:
            wav.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
            wav.writeframes(bytes(12 * 8000 * 2))
        script_units = [
            {"id": "A", "text": "I see it.", "kind": "dialogue", "speaker": "甲", "listener": "乙"},
            {"id": "B", "text": "There it is.", "kind": "narration", "speaker": None},
            {"id": "C", "text": "I see it.", "kind": "dialogue", "speaker": "甲", "listeners": ["乙", "丙"]},
        ]
        self.script.write_text(json.dumps({"units": script_units}, ensure_ascii=False), encoding="utf-8")
        self.rows = [
            {**line, "estimated_start": start, "estimated_end": end, "issues": issues,
             "boundary_status": "not_playback_verified"}
            for line, (start, end, issues) in zip(script_units, [
                (1.0, 5.0, []), (6.0, 8.0, ["low_word_probability"]), (None, None, ["incomplete_boundary"])])
        ]
        self.info = {
            "source_origin_seconds": 0, "decoded_duration_seconds": 12,
            "script_sha256": file_sha(self.script), "audio_file_sha256": file_sha(self.audio),
            "sentence_candidates": self.rows, "matching": {"units": self.rows},
            "automatic_pass": False, "adoption_status": "candidate_pending_user_confirmation",
            "playback_verified": False, "warnings": ["unmatched_asr_content_requires_review"],
        }
        self.write_result()

    def write_result(self):
        self.result.write_text(json.dumps(self.info), encoding="utf-8")

    def inspect(self, window=None, inserts=()):
        return audio_windows(self.result, self.audio, self.script, window, inserts)

    def test_crossing_and_unlocated_are_explicit(self):
        report = self.inspect((2, 7))
        a, b = report["sentences"]
        self.assertEqual(a["text"], "I see it.")
        self.assertTrue(a["crosses_range_start"])
        self.assertEqual(a["parts"], [{"source": [2, 5], "planned": [0, 3]}])
        self.assertTrue(b["crosses_range_end"])
        self.assertEqual(b["status"], "estimate_with_issues")
        self.assertEqual(b["issues"], ["low_word_probability"])
        self.assertEqual(report["source_without_sentence_estimate"], [[5, 6]])
        unlocated = report["unlocated_episode_sentences"][0]
        self.assertEqual((unlocated["id"], unlocated["listeners"], unlocated["status"]), ("C", ["乙", "丙"], "unlocated"))
        self.assertFalse(report["automatic_pass"])

    def test_leading_mid_sentence_and_trailing_inserts(self):
        report = self.inspect((2, 7), [(7, 1), (3, 0.5), (2, 2)])
        self.assertEqual(report["inserts"], [{"source_at": 2, "planned": [0, 2]},
                                              {"source_at": 3, "planned": [3, 3.5]},
                                              {"source_at": 7, "planned": [7.5, 8.5]}])
        self.assertEqual(report["sentences"][0]["parts"], [{"source": [2, 3], "planned": [2, 3]},
                                                          {"source": [3, 5], "planned": [3.5, 5.5]}])
        self.assertEqual(report["sentences"][0]["inserts_within_sentence"], [3])
        self.assertEqual(report["planned_segment_seconds"], 8.5)

    def test_touching_edges_do_not_assign_adjacent_speech(self):
        report = self.inspect((5, 6))
        self.assertEqual(report["sentences"], [])
        self.assertEqual(report["source_without_sentence_estimate"], [[5, 6]])
        self.assertEqual(self.inspect()["planned_segment_seconds"], 12)

    def test_canonical_candidate_content_is_checked(self):
        candidate = {"matching": self.info["matching"], "source_content_hashes": {"script": file_sha(self.script)},
                     "audio_file_sha256": file_sha(self.audio), "decoded_duration_seconds": 12}
        encoded = json.dumps(candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        self.info.update(candidate=candidate, candidate_content_sha256=hashlib.sha256(encoded).hexdigest())
        self.write_result()
        self.assertEqual(len(self.inspect()["sentences"]), 2)
        self.rows[0]["speaker"] = "丙"
        self.write_result()
        with self.assertRaisesRegex(ValueError, "recorded candidate"):
            self.inspect()

    def test_audio_and_script_file_identity(self):
        for path in (self.audio, self.script):
            with self.subTest(path=path):
                original = path.read_bytes()
                path.write_bytes(original + b" ")
                with self.assertRaisesRegex(ValueError, "differs"):
                    self.inspect()
                path.write_bytes(original)

    def test_invalid_range_and_insert(self):
        for window, inserts in [((-1, 3), []), ((0, 13), []), ((0, 0), []),
                                ((0, 3), [(4, 1)]), ((0, 3), [(0, -1)]),
                                ((0, 3), [(2, 1), (2, 1)]), ((0, 3), [(2, float("inf"))])]:
            with self.subTest(window=window, inserts=inserts), self.assertRaises(ValueError):
                self.inspect(window, inserts)

    def test_invalid_estimated_edges_are_never_used_for_planning(self):
        self.rows[1]["estimated_end"] = 15
        self.write_result()
        report = self.inspect()
        self.assertEqual([r["id"] for r in report["sentences"]], ["A"])
        self.assertEqual(report["unlocated_episode_sentences"][0]["status"], "invalid_estimate")

    def test_cli_emits_utf8_json(self):
        cmd = [sys.executable, str(Path(__file__).with_name("audio_windows.py")), str(self.result),
               "--audio", str(self.audio), "--script", str(self.script), "--range", "0", "6", "--insert", "1", "1"]
        run = subprocess.run(cmd, capture_output=True, check=True)
        self.assertEqual(json.loads(run.stdout)["sentences"][0]["speaker"], "甲")


if __name__ == "__main__":
    unittest.main()
