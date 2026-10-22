"""Local durable receipts, not a browser driver or platform permission system."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3


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
        else:
            get_meta(con, "scope")
            if op == "authorize":
                validate_auth(data)
                set_meta(con, "authorization", data)
            elif op == "register":
                require(text(data.get("id")), "Stable task ID required")
                validate_spec(data.get("spec", {}))
                con.execute("INSERT INTO tasks(id,root,spec,state) VALUES (?,?,?,'prepared')",
                            (data["id"], data["id"], encode(data["spec"])))
            elif op in ("pause", "resume"):
                require(text(data.get("evidence")), "Record the user decision and current context")
                set_meta(con, "paused", op == "pause")
            elif op == "intent":
                require(not get_meta(con, "paused"), "Paused; no new submission")
                require(get_meta(con, "authorization")["mode"] == "generate",
                        "Preparation does not authorize generation")
                current = task(con, data.get("id"))
                require(current["state"] == "prepared", "Intent already exists; query history")
                unresolved = con.execute("SELECT id FROM tasks WHERE state IN ('intent','unknown')").fetchone()
                require(unresolved is None, "Resolve the existing unknown intent before any new click")
                require(encode(data.get("visible")) == current["spec"],
                        "Current page differs from adopted prompt, assets or parameters")
                require(data.get("scope") == get_meta(con, "scope"), "Wrong project or episode")
                require(data.get("no_active_same_task") is True and text(data.get("evidence")),
                        "Reconcile the current queue/history and exact bound nodes first")
                spec = json.loads(current["spec"])
                if spec["mode"] == "extend":
                    src = task(con, spec["source"]["task"])
                    require(src["state"] == "completed" and src["result_id"] == spec["source"]["result_id"],
                            "Source must be the exact completed adopted result")
                    require(json.loads(src["spec"])["mode"] == "native",
                            "This minimal helper supports native sources only")
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
    args = parser.parse_args()
    try:
        if args.event:
            value = json.loads(args.event.read_text(encoding="utf-8-sig"))
            result = apply(args.record, value["op"], value["data"])
        else:
            result = status(args.record)
    except (Refused, sqlite3.Error, OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f"No click permission: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
