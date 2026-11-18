"""Synthetic adopted-prompt and execution-package regression tests."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from prompt_package import build_package, save_package, verify_package


class PromptPackageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.body = self.root / "采用稿.md"
        self.body.write_bytes("【全局】\r\n本段生成时长：6秒\r\n甲把信递给乙。".encode("utf-8-sig"))
        self.refs = ["甲·日常", "会客厅"]

    def package(self):
        return build_package(self.body, "native", 6, self.refs)

    def spec(self, package):
        return {"prompt_sha256": package["prompt_sha256"], "mode": "native", "seconds": 6,
                "references": [{"name": name, "node": f"test-{i}"} for i, name in enumerate(self.refs)]}

    def test_exact_bytes_and_order(self):
        package = self.package()
        self.assertEqual(package["prompt_sha256"], hashlib.sha256(self.body.read_bytes()).hexdigest())
        self.assertEqual(package["references"], self.refs)
        self.assertIsNone(verify_package(package, self.spec(package)))
        raw = self.body.read_bytes()
        for altered in (raw.replace(b"\r\n", b"\n"), raw.removeprefix(b"\xef\xbb\xbf")):
            self.body.write_bytes(altered)
            with self.assertRaisesRegex(ValueError, "bytes changed"):
                verify_package(package)

    def test_spec_comparison(self):
        package = self.package()
        spec = self.spec(package)
        for field, value in (("prompt_sha256", "b" * 64), ("mode", "extend"), ("seconds", 4),
                             ("seconds", 6.0), ("references", list(reversed(spec["references"])))):
            with self.subTest(field=field), self.assertRaises(ValueError):
                verify_package(package, {**spec, field: value})

    def test_old_formats_and_audio_times(self):
        for text in ("【动作与表演】\n一人递出信件。", "【镜头1｜0–6秒】\n源音轨 12.5–18.5。", "无人空镜。"):
            self.body.write_bytes(text.encode("utf-16"))
            package = build_package(self.body, "extend", 3, [])
            verify_package(package)

    def test_unfilled_template_and_declared_duration(self):
        for text in ("【镜头ID｜具体起止】", "本段生成时长：5.5秒", "本次延长新增时长: 8s"):
            self.body.write_text(text, encoding="utf-8")
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.package()

    def test_missing_invalid_or_empty_file(self):
        for raw in (b"  \r\n", b"\0abc", b"\xff"):
            self.body.write_bytes(raw)
            with self.assertRaises(ValueError):
                self.package()
        self.body.unlink()
        with self.assertRaises(ValueError):
            self.package()

    def test_refuse_invalid_metadata(self):
        for mode, seconds, refs in (("native", True, []), ("native", 0, []), ("other", 1, []),
                                   ("native", 1, "asset"), ("extend", 1, [""])):
            with self.subTest(mode=mode, seconds=seconds, refs=refs), self.assertRaises(ValueError):
                build_package(self.body, mode, seconds, refs)
        for changes in ({"version": True}, {"prompt_path": "body.md"}, {"references": None}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                verify_package({**self.package(), **changes})

    def test_output_is_repeatable_and_preserves_prior_files(self):
        path = self.root / "packages" / "s01.json"
        package = self.package()
        save_package(path, package)
        save_package(path, package)
        with self.assertRaises(ValueError):
            save_package(path, {**package, "mode": "extend"})
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), package)
        raw = self.body.read_bytes()
        with self.assertRaises(ValueError):
            save_package(self.body, package)
        self.assertEqual(self.body.read_bytes(), raw)
        self.assertEqual(list(path.parent.iterdir()), [path])

    def test_cli_create_and_verify(self):
        cli = [sys.executable, str(Path(__file__).with_name("prompt_package.py"))]
        path = self.root / "s01.json"
        result = subprocess.run([*cli, str(self.body), "--mode", "native", "--seconds", "6",
                                 "--ref", self.refs[0], "--ref", self.refs[1], "--out", str(path)],
                                capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout), self.package())
        subprocess.run([*cli, "--verify", str(path)], capture_output=True, check=True)
        self.body.write_text("乙收下信件。", encoding="utf-8")
        result = subprocess.run([*cli, "--verify", str(path)], capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")


if __name__ == "__main__":
    unittest.main()
