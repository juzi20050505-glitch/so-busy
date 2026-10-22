"""Check the upload allowlist and local Markdown links; never reads runtime data."""
from pathlib import Path
import re
import subprocess
import sys


def check(root):
    approved = (root / "repository-files.txt").read_text(encoding="utf-8").splitlines()
    errors = []
    if len(approved) != len(set(approved)):
        errors.append("Duplicate upload entry")
    for name in approved:
        path = root / name
        if Path(name).name != name or not path.is_file():
            errors.append(f"Missing or non-root upload entry: {name}")
            continue
        body = path.read_text(encoding="utf-8")
        # Check accidental project copies or embedded credentials, not ordinary source links.
        patterns = [r"gh[pousr]_[A-Za-z0-9]{20,}", r"github_pat_[A-Za-z0-9_]{20,}",
                    r"-----BEGIN (?:RSA |OPENSSH )?PRIVATE KEY-----",
                    r"https?://[^\s)]+[?&](?:token|access_token|api_key)=[^\s)]+"]
        if any(re.search(pattern, body) for pattern in patterns):
            errors.append(f"Possible embedded credential: {name}")
        if path.suffix == ".md":
            for target in re.findall(r"\]\(([^)]+)\)", body):
                if "://" not in target and not target.startswith("#"):
                    local = target.split("#", 1)[0]
                    if local and (not (root / local).is_file() or local not in approved):
                        errors.append(f"Broken or unapproved link: {name} -> {target}")
    # A repository-wide staging mistake must fail even if ignored files are forced in.
    if (root / ".git").exists():
        result = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True)
        tracked = result.stdout.decode("utf-8").strip("\0").split("\0") if result.stdout else []
        errors.extend(f"Unapproved tracked file: {name}" for name in tracked if name not in approved)
    return approved, errors


if __name__ == "__main__":
    approved, errors = check(Path(__file__).resolve().parent)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        raise SystemExit(1)
    print(f"OK: {len(approved)} approved files; links and credential patterns checked")
