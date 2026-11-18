"""Local durable receipts, not a browser driver or platform permission system."""
from __future__ import annotations

import argparse
import hashlib
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import sys


class Refused(ValueError):
    """The requested transition requires reconciliation, not another click."""


def require(condition, message):
    if not condition:
        raise Refused(message)


def text(value):
    return isinstance(value, str) and bool(value.strip())


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def validate_auth(auth):
    require(auth.get("mode") in ("prepare", "generate"), "Unknown execution scope")
    if auth["mode"] == "generate":
        require(text(auth.get("quote")) and text(auth.get("source")),
                "Record the existing explicit human authorization and its source")


def validate_spec(spec):
    for key in ("card", "model", "resolution"):
        require(text(spec.get(key)), f"Missing actual {key}")
    require(re.fullmatch(r"[a-f0-9]{64}", spec.get("prompt_sha256", "")) is not None,
            "Exact adopted prompt SHA256 required")
    require(spec.get("mode") in ("native", "extend"), "Unknown generation mode")
    require(type(spec.get("seconds")) is int and spec["seconds"] > 0,
            "Adopted positive integer seconds required")
    require(isinstance(spec.get("references"), list) and all(
        isinstance(r, dict) and text(r.get("name")) and text(r.get("node"))
        for r in spec["references"]), "Use actual reference names and bound nodes")
    if spec["mode"] == "extend":
        source = spec.get("source", {})
        require(isinstance(source, dict) and all(text(source.get(k)) for k in
                ("task", "result_id", "evidence")), "Actual source result required")
        start, end = source.get("start"), source.get("end")
        require(type(start) in (int, float) and type(end) in (int, float)
                and 0 <= start < end < float("inf"), "Finite actual source interval required")
        require(source.get("qualified") is True, "Check the selected source interval first")
    else:
        require(not spec.get("source"), "Native task must not hide a source dependency")


@contextmanager
def database(path, create=False):
    path = Path(path).resolve()
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
    else:
        require(path.is_file(), "No existing canonical record; do not silently reset")
    # mode=rw refuses to recreate a missing database during recovery.
    uri = path.as_uri() + ("?mode=rwc" if create else "?mode=rw")
    con = sqlite3.connect(uri, uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA synchronous=FULL")
        con.execute("BEGIN IMMEDIATE")
        yield con
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()


def event(con, op, payload):
    con.execute("INSERT INTO events(at, op, payload) VALUES (?, ?, ?)",
                (datetime.now(timezone.utc).isoformat(), op, encode(payload)))


def get_meta(con, key):
    row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    require(row is not None, "Uninitialized record")
    return json.loads(row[0])


def set_meta(con, key, value):
    con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)", (key, encode(value)))


def task(con, task_id):
    row = con.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    require(row is not None, "Unknown task; reconcile the real card first")
    return dict(row)


def adopted_package(con, root):
    exists = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_packages'").fetchone()
    if exists:
        row = con.execute("SELECT package FROM task_packages WHERE root=?", (root,)).fetchone()
        if row:
            return json.loads(row[0])
    return None


def requires_package(con):
    return con.execute("SELECT 1 FROM meta WHERE key='workflow_version'").fetchone() is not None


def check_package(package, spec):
    from prompt_package import verify_package
    try:
        verify_package(package, spec)
    except (ValueError, OSError) as exc:
        raise Refused(f"Adopted prompt package failed verification: {exc}") from exc


def record_spec(con, data, *, amend=False):
    task_id = data.get("id")
    require(text(task_id), "Stable task ID required")
    spec = data.get("spec", {})
    validate_spec(spec)
    package = data.get("package")
    if requires_package(con) or package is not None or adopted_package(con, task_id) is not None:
        require(isinstance(package, dict), "Supply the execution package for the adopted prompt")
        check_package(package, spec)
    if amend:
        old = task(con, task_id)
        require(old["state"] == "prepared" and old["id"] == old["root"],
                "Only an unsubmitted original task can be amended")
        require(text(data.get("evidence")), "Record the reason for changing the prepared card")
        con.execute("UPDATE tasks SET spec=? WHERE id=?", (encode(spec), task_id))
        if con.execute("SELECT 1 FROM sqlite_master WHERE name='saved_cards' AND type='table'").fetchone():
            con.execute("DELETE FROM saved_cards WHERE id=?", (task_id,))
    else:
        con.execute("INSERT INTO tasks(id,root,spec,state) VALUES (?,?,?,'prepared')",
                    (task_id, task_id, encode(spec)))
    if package is not None:
        con.execute("CREATE TABLE IF NOT EXISTS task_packages(root TEXT PRIMARY KEY, package TEXT NOT NULL)")
        con.execute("INSERT OR REPLACE INTO task_packages VALUES (?,?)", (task_id, encode(package)))


def archive_result(con, data):
    current = task(con, data.get("id"))
    require(current["state"] == "completed" and current["result_id"] == data.get("result_id"),
            "Archive only the exact completed result")
    require(text(data.get("evidence")) and text(data.get("path")),
            "Record the download evidence and actual local video path")
    path = Path(data["path"]).resolve(strict=True)
    require(path.is_file(), "Local video file required")
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as video:
        for block in iter(lambda: video.read(1024 * 1024), b""):
            digest.update(block)
    after = path.stat()
    require(before.st_size > 0 and (before.st_ino, before.st_size, before.st_mtime_ns) ==
            (after.st_ino, after.st_size, after.st_mtime_ns), "Download is empty or changing")
    sha = digest.hexdigest()
    require(data.get("sha256", sha) == sha, "Downloaded bytes differ from the provided digest")
    data = {**data, "path": str(path), "sha256": sha, "size": after.st_size}
    con.execute("CREATE TABLE IF NOT EXISTS archives(id TEXT PRIMARY KEY, path TEXT, sha256 TEXT)")
    old = con.execute("SELECT path,sha256 FROM archives WHERE id=?", (current["id"],)).fetchone()
    if old:
        require(old["sha256"] == sha, "A different file was already recorded for this result")
        if old["path"] == str(path):
            return None
    con.execute("INSERT OR REPLACE INTO archives VALUES (?,?,?)", (current["id"], str(path), sha))
    return data


def apply(path, op, data):
    """Commit each transition before returning. Reading never returns click_once."""
    output = {"recorded": op}
    with database(path, create=(op == "init")) as con:
        if op == "init":
            exists = con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            require(not exists, "Existing record cannot be initialized again")
            scope, auth = data.get("scope", {}), data.get("authorization", {})
            require(all(text(scope.get(k)) for k in ("project", "episode", "target_url")),
                    "Exact current project, episode and target URL required")
            validate_auth(auth)
            con.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            con.execute("CREATE TABLE tasks(id TEXT PRIMARY KEY, root TEXT NOT NULL, "
                        "retry_of TEXT UNIQUE, spec TEXT NOT NULL, state TEXT NOT NULL, "
                        "result_id TEXT, cause TEXT)")
            con.execute("CREATE TABLE events(seq INTEGER PRIMARY KEY, at TEXT NOT NULL, "
                        "op TEXT NOT NULL, payload TEXT NOT NULL)")
            set_meta(con, "scope", scope)
            set_meta(con, "authorization", auth)
            set_meta(con, "paused", False)
            set_meta(con, "workflow_version", 2)
        else:
            get_meta(con, "scope")
            if op == "authorize":
                validate_auth(data)
                set_meta(con, "authorization", data)
            elif op in ("register", "amend"):
                record_spec(con, data, amend=op == "amend")
            elif op in ("pause", "resume"):
                require(text(data.get("evidence")), "Record the user decision and current context")
                set_meta(con, "paused", op == "pause")
            elif op in ("intent", "confirm_prepared"):
                require(not get_meta(con, "paused"), "Paused; reconcile the current scope first")
                current = task(con, data.get("id"))
                require(current["state"] == "prepared", "Intent already exists; query history")
                require(encode(data.get("visible")) == current["spec"],
                        "Current page differs from adopted prompt, assets or parameters")
                require(data.get("scope") == get_meta(con, "scope"), "Wrong project or episode")
                require(text(data.get("evidence")), "Record the same-page readback evidence")
                spec = json.loads(current["spec"])
                package = adopted_package(con, current["root"])
                if requires_package(con) or package is not None:
                    require(isinstance(package, dict), "Missing adopted package; reconcile the existing record")
                    check_package(package, spec)
                if spec["mode"] == "extend":
                    src = task(con, spec["source"]["task"])
                    require(src["state"] == "completed" and src["result_id"] == spec["source"]["result_id"],
                            "Source must be the exact completed adopted result")
                    require(json.loads(src["spec"])["mode"] == "native",
                            "This minimal helper supports native sources only")
                if op == "confirm_prepared":
                    con.execute("CREATE TABLE IF NOT EXISTS saved_cards(id TEXT PRIMARY KEY, spec TEXT NOT NULL)")
                    con.execute("INSERT OR REPLACE INTO saved_cards VALUES (?,?)", (current["id"], current["spec"]))
                else:
                    require(get_meta(con, "authorization")["mode"] == "generate",
                            "Preparation does not authorize generation")
                    unresolved = con.execute("SELECT id FROM tasks WHERE state IN ('intent','unknown')").fetchone()
                    require(unresolved is None, "Resolve the existing unknown intent before any new click")
                    active_cards = con.execute("SELECT spec FROM tasks WHERE state='processing'")
                    require(all(json.loads(row[0])["card"] != spec["card"] for row in active_cards),
                            "This card already has a processing attempt; reconcile its receipt")
                    require(data.get("no_active_same_task") is True, "Reconcile the actual queue/history first")
                    con.execute("UPDATE tasks SET state='intent' WHERE id=?", (current["id"],))
                    output["click_once"] = current["id"]
            elif op == "observe":
                current = task(con, data.get("id"))
                state = data.get("state")
                require(current["state"] in ("intent", "unknown", "processing"),
                        "Only reconcile an existing submitted attempt")
                require(state in ("unknown", "processing", "completed", "failed"), "Invalid receipt state")
                require(text(data.get("evidence")), "Visible receipt evidence required")
                if state == "completed":
                    require(text(data.get("result_id")), "Actual result identity required")
                if state == "failed":
                    require(data.get("cause") in ("balance", "access", "platform", "prompt", "model", "unknown"),
                            "Keep the observed failure cause, including unknown")
                con.execute("UPDATE tasks SET state=?, result_id=?, cause=? WHERE id=?",
                            (state, data.get("result_id"), data.get("cause"), current["id"]))
            elif op == "archive":
                data = archive_result(con, data)
                if data is None:
                    return {"recorded": op, "already_recorded": True}
            elif op == "retry":
                old = task(con, data.get("id"))
                require(old["state"] == "failed", "Unknown or active attempts cannot be retried")
                require(old["cause"] in ("balance", "access", "platform"),
                        "Creative repair needs a revised adopted plan; do not relabel its cause")
                require(text(data.get("new_id")) and text(data.get("condition_evidence")),
                        "Record the resolved technical condition and a fresh attempt ID")
                require(data.get("no_active_same_task") is True,
                        "Check original history for late acceptance before retrying")
                require(not get_meta(con, "paused"), "Paused; no retry preparation")
                require(get_meta(con, "authorization")["mode"] == "generate", "Generation scope required")
                con.execute("INSERT INTO tasks(id,root,retry_of,spec,state) VALUES (?,?,?,?,'prepared')",
                            (data["new_id"], old["root"], old["id"], old["spec"]))
            else:
                raise Refused("Unsupported operation")
        event(con, op, deepcopy(data))
    # A return value can only be delivered after the SQLite commit succeeds.
    return output


def status(path):
    with database(path) as con:
        return {"scope": get_meta(con, "scope"), "authorization": get_meta(con, "authorization"),
                "paused": get_meta(con, "paused"),
                "tasks": [dict(r) for r in con.execute("SELECT * FROM tasks ORDER BY rowid")],
                "events": [dict(r) for r in con.execute("SELECT * FROM events ORDER BY seq")],
                "next": "Reconcile actual queue/history; status never grants a fresh click"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", required=True, type=Path)
    parser.add_argument("--event", type=Path, help="UTF-8 JSON containing op and data; omit for status")
    parser.add_argument("--package", type=Path, help="Adopted execution-package JSON for register or amend")
    args = parser.parse_args()
    try:
        if args.package and not args.event:
            parser.error("--package requires a register or amend event")
        if args.event:
            value = json.loads(args.event.read_text(encoding="utf-8-sig"))
            if args.package:
                require(value["op"] in ("register", "amend") and "package" not in value["data"],
                        "Provide one package for a register or amend event")
                value["data"]["package"] = json.loads(args.package.read_text(encoding="utf-8-sig"))
            result = apply(args.record, value["op"], value["data"])
        else:
            result = status(args.record)
    except (Refused, sqlite3.Error, OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f"No click permission: {exc}\n")
    sys.stdout.buffer.write((json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
