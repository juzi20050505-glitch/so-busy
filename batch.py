"""Read one batch manifest and summarize its canonical episode receipts."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys

from receipts import get_meta, require, text
from prompt_package import verify_package


def read_plan(path):
    plan = json.loads(path.read_text(encoding="utf-8-sig"))
    require(isinstance(plan, dict) and text(plan.get("project")), "Batch project required")
    episodes = plan.get("episodes")
    require(isinstance(episodes, list) and episodes, "Batch episodes required")
    names, paths = set(), set()
    for entry in episodes:
        require(isinstance(entry, dict) and text(entry.get("episode")) and text(entry.get("record")),
                "Every episode needs its exact name and record path")
        name = entry["episode"]
        if "target_url" in entry:
            require(text(entry["target_url"]), f"{name}: target_url must identify the intended page")
        record = (path.parent / entry["record"]).resolve()
        require(name not in names and record not in paths, "Duplicate episode or record")
        names.add(name)
        paths.add(record)
        tasks = entry.get("tasks", [])
        require(isinstance(tasks, list), f"{name}: tasks must be a list")
        ids = set()
        extensions = set()
        for task in tasks:
            require(isinstance(task, dict) and text(task.get("id")), f"{name}: task ID required")
            require(task["id"] not in ids, f"{name}: duplicate task {task['id']}")
            ids.add(task["id"])
            if "package" in task:
                require(text(task["package"]), f"{name}: package must be a path")
                task["package"] = str((path.parent / task["package"]).resolve())
            if "source" in task:
                require(text(task["source"]) and task["source"] != task["id"],
                        f"{name}: source must identify another native root")
                extensions.add(task["id"])
        require(all(task.get("source") not in extensions for task in tasks),
                f"{name}: an extension must depend on a native root")
    return plan


def attach_package(item, plan, bound=None):
    manifest_path = plan.get("package")
    registered = bound.get("package") if bound else None
    if not manifest_path and not registered:
        return
    status = {"path": manifest_path} if manifest_path else {"source": "episode_record"}
    item["package"] = status
    try:
        package = json.loads(Path(manifest_path).read_text(encoding="utf-8-sig")) if manifest_path else registered
        verify_package(package, bound["spec"] if bound else None)
        if registered:
            require(Path(package["prompt_path"]) == Path(registered["prompt_path"]),
                    "Planned package points to a different adopted prompt path")
        if plan.get("source"):
            require(package["mode"] == "extend", "Native prompt package has a planned source")
        if not bound:
            for field in ("prompt_path", "prompt_sha256", "mode", "seconds", "references"):
                item[field] = package[field]
            if package["mode"] == "extend" and not plan.get("source") and item["next"] == "prepare_card":
                item["next"] = "locate_source"
        status["state"] = "verified"
    except FileNotFoundError:
        status["state"] = "missing"
    except (ValueError, OSError, KeyError, TypeError) as exc:
        status.update(state="error", error=str(exc))
    if status["state"] != "verified" and item["next"] in (
            "prepare_card", "verify_current_card", "prepare_only", "saved_for_user", "verify_source_interval"):
        item["next"] = "check_package"


def snapshot(path, project, episode):
    # Read authorization, attempts, and archive receipts in one consistent snapshot.
    # SQLite mode=ro also prevents an accidental empty record during recovery.
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as con:
        con.row_factory = sqlite3.Row
        con.execute("BEGIN")
        scope = get_meta(con, "scope")
        require(scope["project"] == project and scope["episode"] == episode,
                "Record scope does not match manifest project and episode")
        rows = [dict(row) for row in con.execute("SELECT * FROM tasks ORDER BY rowid")]
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        packages = {}
        if "task_packages" in tables:
            packages = {root: json.loads(raw) for root, raw
                        in con.execute("SELECT root,package FROM task_packages")}
        saved_specs = dict(con.execute("SELECT id,spec FROM saved_cards")) if "saved_cards" in tables else {}
        for row in rows:
            row["saved"] = saved_specs.get(row["id"]) == row["spec"]
            row["spec"] = json.loads(row["spec"])
            row["package"] = packages.get(row["root"])
        archives = {}
        for row in con.execute("SELECT payload FROM events WHERE op='archive' ORDER BY seq"):
            data = json.loads(row["payload"])
            archives[(data["id"], data["result_id"])] = data
        return scope, get_meta(con, "authorization")["mode"], get_meta(con, "paused"), rows, archives


def archive_step(archive):
    if not archive:
        return "check_download"
    path = Path(archive["path"])
    try:
        if not path.is_file():
            return "locate_archived_file"
        if path.stat().st_size != archive["size"]:
            return "check_archived_file"
    except OSError:
        return "check_archived_file"
    return "archive_recorded"


def summarize_episode(project, entry, record):
    result = {"episode": entry["episode"], "record": str(record), "tasks": []}
    if not record.is_file():
        result.update(record_state="missing", next="locate_record")
        for plan in entry.get("tasks", []):
            item = {"id": plan["id"], "source": plan.get("source"), "receipt_state": "unverified",
                    "next": "locate_record"}
            attach_package(item, plan)
            result["tasks"].append(item)
        return result
    try:
        scope, auth, paused, attempts, archives = snapshot(record, project, entry["episode"])
        require("target_url" not in entry or scope["target_url"] == entry["target_url"],
                "Record target URL differs from the requested episode page")
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError) as exc:
        result.update(record_state="error", next="check_record", error=str(exc))
        return result
    result.update(record_state="read", target_url=scope["target_url"], authorization=auth, paused=paused)
    latest = {row["root"]: row for row in attempts}
    by_id = {row["id"]: row for row in attempts}
    unresolved = any(row["state"] in ("intent", "unknown") for row in attempts)
    planned = {t["id"]: t for t in entry.get("tasks", [])}
    for root in dict.fromkeys([*planned, *latest]):
        plan = planned.get(root, {})
        source = plan.get("source")
        row = latest.get(root)
        if row:
            spec = row["spec"]
            binding = spec.get("source") or {}
            bound_source = by_id.get(binding.get("task"))
            item = {"id": root, "attempt": row["id"], "receipt_state": row["state"],
                    "mode": spec["mode"], "card": spec["card"], "seconds": spec["seconds"],
                    "prompt_sha256": spec["prompt_sha256"], "references": spec["references"]}
            if row["package"] is not None:
                item["prompt_path"] = row["package"]["prompt_path"]
            if spec["mode"] == "extend":
                item["source"] = bound_source["root"] if bound_source else binding["task"]
                item["source_attempt"] = binding["task"]
            if source and source != item.get("source"):
                item["planned_source"] = source
                item["plan_conflict"] = True
            state = row["state"]
            if state in ("intent", "unknown"):
                action = "reconcile_original_card"
            elif state == "processing":
                action = "poll_original_card"
            elif state == "completed":
                item["result_id"] = row["result_id"]
                archive = archives.get((row["id"], row["result_id"]))
                action = archive_step(archive)
                if archive:
                    item["archive"] = {k: archive[k] for k in ("path", "size", "sha256")}
            elif state == "failed":
                item["cause"] = row["cause"]
                action = "review_failure"
            elif state != "prepared":
                action = "check_record"
            elif item.get("plan_conflict"):
                action = "check_source_binding"
            elif spec["mode"] == "extend" and (
                    not bound_source or bound_source["spec"]["mode"] != "native" or
                    bound_source["state"] != "completed" or bound_source["result_id"] != binding["result_id"]):
                action = "check_source_binding"
            elif paused:
                action = "paused"
            elif auth != "generate":
                action = "saved_for_user" if row["saved"] else "prepare_only"
            elif unresolved:
                action = "reconcile_episode_history"
            else:
                action = "verify_current_card"
        else:
            item = {"id": root, "receipt_state": "unregistered"}
            action = "prepare_card"
            if root in by_id:
                action = "check_root_id"
            elif source:
                item["source"] = source
                parent = latest.get(source)
                if parent and parent["spec"]["mode"] != "native":
                    action = "check_source_binding"
                elif parent and parent["state"] == "completed":
                    action = "verify_source_interval"
                elif parent and parent["state"] == "failed":
                    action = "review_source_failure"
                elif parent or source in planned:
                    action = "wait_for_source"
                else:
                    action = "locate_source"
            if paused:
                action = "paused"
        item["next"] = action
        attach_package(item, plan, row)
        result["tasks"].append(item)
    return result


def summarize(path):
    path = Path(path).resolve()
    plan = read_plan(path)
    episodes = [summarize_episode(plan["project"], entry, (path.parent / entry["record"]).resolve())
                for entry in plan["episodes"]]
    counts = Counter(task["next"] for episode in episodes for task in episode["tasks"])
    return {"project": plan["project"], "episodes": episodes, "tasks_by_next_step": dict(counts)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path, help="Project-scoped UTF-8 JSON batch manifest")
    args = parser.parse_args()
    try:
        result = summarize(args.manifest)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Cannot read batch: {exc}\n")
    sys.stdout.buffer.write((json.dumps(result, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    if any(episode["record_state"] == "error" for episode in result["episodes"]):
        parser.exit(2)


if __name__ == "__main__":
    main()
