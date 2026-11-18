"""Bind a segment's adopted prompt bytes and planned references to a local execution package."""
import argparse
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile


# Only explicit generation durations and template artefacts are structural facts.
# Original-audio timings, shot timings, dialogue and editorial notes use other clocks.
GENERATED_DURATION = re.compile(
    r"^(?:[-*]\s+)?(?:本段生成时长|本段生成秒数|本次延长新增时长)"
    r"\s*[:：=]\s*(\d+(?:\.\d+)?)\s*(?:秒|s)\s*[。.]?$",
    re.IGNORECASE,
)
TEMPLATE_LINES = {
    "【镜头ID｜具体起止】",
    "本段事件、具体生成时长、画幅、统一风格及声音方案。",
    "实际角色、场景、必要布局或接续参考，各自承担什么。",
}


def build_package(prompt_path, mode, seconds, references):
    """Read the adopted body file; references are names in the planned binding order."""
    if mode not in ("native", "extend"):
        raise ValueError("mode must be native or extend")
    if type(seconds) is not int or seconds < 1:
        raise ValueError("Adopt a positive integer duration in seconds")
    if not isinstance(references, list) or not all(
        isinstance(name, str) and name.strip() for name in references
    ):
        raise ValueError("references must be an ordered list of nonempty names")
    if not isinstance(prompt_path, (str, Path)) or not str(prompt_path).strip():
        raise ValueError("Missing adopted prompt path")
    try:
        path = Path(prompt_path).resolve(strict=True)
        raw = path.read_bytes()
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"Cannot read adopted prompt {prompt_path}: {exc}") from exc
    encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    try:
        body = raw.decode(encoding)
    except UnicodeError as exc:
        raise ValueError(f"Save the prompt as UTF-8 or UTF-16 with a BOM: {path}") from exc
    if not body.strip() or "\x00" in body:
        raise ValueError(f"Empty or invalid prompt body: {path}")
    for number, line in enumerate(body.splitlines(), start=1):
        line = line.strip()
        if line in TEMPLATE_LINES:
            raise ValueError(f"Replace the unfilled prompt template at line {number}")
        duration = GENERATED_DURATION.fullmatch(line)
        if duration and Decimal(duration[1]) != seconds:
            raise ValueError(f"Adopted seconds differ from the prompt at line {number}")
    return {
        "version": 1,
        "prompt_path": str(path),
        "prompt_sha256": hashlib.sha256(raw).hexdigest(),
        "mode": mode,
        "seconds": seconds,
        "references": list(references),
    }


def verify_package(package, spec=None):
    """Re-read the prompt. With a receipt spec, verify its corresponding adopted fields."""
    if not isinstance(package, dict) or type(package.get("version")) is not int or package["version"] != 1:
        raise ValueError("Unsupported prompt package version")
    path = package.get("prompt_path")
    if not isinstance(path, str) or not Path(path).is_absolute():
        raise ValueError("Package must identify the absolute adopted prompt path")
    actual = build_package(path, package.get("mode"), package.get("seconds"), package.get("references"))
    if package.get("prompt_sha256") != actual["prompt_sha256"]:
        raise ValueError("Adopted prompt bytes changed; check the revision before submission")
    if spec is None:
        return
    if not isinstance(spec, dict) or not isinstance(spec.get("references"), list):
        raise ValueError("Missing receipt specification or ordered page references")
    if type(spec.get("seconds")) is not int:
        raise ValueError("Receipt must specify integer seconds")
    for key in ("prompt_sha256", "mode", "seconds"):
        if spec.get(key) != actual[key]:
            raise ValueError(f"Receipt {key} differs from the adopted prompt package")
    refs = spec["references"]
    if not all(isinstance(ref, dict) for ref in refs) or [ref.get("name") for ref in refs] != actual["references"]:
        raise ValueError("Page reference names or order differ from the adopted prompt package")


def save_package(path, package):
    """Publish a complete package without replacing an earlier revision or source file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(package, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as pending:
        temp_path = Path(pending.name)
    try:
        temp_path.write_bytes(encoded)
        try:
            if os.name == "nt":
                os.rename(temp_path, path)  # On Windows, an existing destination fails.
            else:
                os.link(temp_path, path)
        except FileExistsError:
            if path.read_bytes() != encoded:
                raise ValueError(f"Output already exists with different content; use a new path: {path}")
    finally:
        temp_path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("body", nargs="?", type=Path, help="Adopted body to send to the video model")
    parser.add_argument("--mode", choices=("native", "extend"))
    parser.add_argument("--seconds", type=int)
    parser.add_argument("--ref", action="append", default=[], help="Exact reference name, in binding order")
    parser.add_argument("--out", type=Path, help="Optional new execution-package JSON file")
    parser.add_argument("--verify", type=Path, help="Recheck an existing package against the adopted file")
    args = parser.parse_args()
    try:
        if args.verify:
            if args.body or args.mode or args.seconds is not None or args.ref or args.out:
                parser.error("--verify only takes an existing package file")
            package = json.loads(args.verify.read_text(encoding="utf-8-sig"))
            verify_package(package)
        else:
            if args.body is None:
                parser.error("Provide an adopted prompt file or --verify PACKAGE.json")
            package = build_package(args.body, args.mode, args.seconds, args.ref)
            if args.out:
                save_package(args.out, package)
    except (ValueError, OSError) as exc:
        parser.exit(2, f"Prompt package: {exc}\n")
    sys.stdout.buffer.write((json.dumps(package, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
