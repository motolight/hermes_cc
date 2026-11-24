"""Read existing Hermes Kanban stores without loading Hermes or WebUI handlers."""

import json
import os
import re
import sqlite3
import stat
import time
from pathlib import Path

from safe_text import CONTROL, LOCAL_PATH, PRIVATE_BLOCK, SECRET, SENSITIVE_LINK, UNSAFE_FIELD
from telemetry import bounded_read, label, model_name, open_db, source, sqlite_columns, sqlite_select, timestamp, utc

MAX_BOARDS = 16
MAX_TASKS_PER_BOARD = 100
MAX_TASKS = 180
MAX_DEPENDENCIES = 24
MAX_ATTEMPTS = 8
MAX_DETAIL_RUNS = 24
MAX_DETAIL_COMMENTS = 48
MAX_DETAIL_EVENTS = 48
MAX_LINKS = 48
MAX_TASK_DETAIL_BYTES = 500 * 1024
BOARD_SLUG = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
TASK_STATUSES = frozenset("triage todo scheduled ready running blocked review done archived".split())
RUN_STATUSES = TASK_STATUSES | {"crashed", "timed_out", "failed", "released", "reclaimed", "stale", "gave_up",
                              "spawn_failed", "rate_limited", "changes_requested", "cancelled", "dependency_wait"}
TASK_DATES = ("created_at", "started_at", "completed_at", "updated_at")
TASK_FIELDS = ("id", "title", "assignee", "status", "project_id", "tenant", "session_id", *TASK_DATES)
TITLE_LIMIT_BYTES = 1024
BODY_LIMIT_BYTES = 96 * 1024
RESULT_LIMIT_BYTES = 48 * 1024
NOTE_LIMIT_BYTES = 6 * 1024
JSON_LIMIT_BYTES = 24 * 1024
OMITTED = "[omitted: text exceeds the collection limit]"
PULL_REQUEST = re.compile(r"https://github\.com/([A-Za-z0-9-]{1,39}/[A-Za-z0-9_.-]{1,100})/pull/([1-9][0-9]{0,19})\Z")
BLOCK_KINDS = frozenset({"dependency", "needs_input", "capability", "transient"})
BLOCK_EVENTS = {"blocked": "blocked", "gave_up": "blocked", "dependency_wait": "todo",
                "descendant_invalidated": "todo", "block_loop_detected": "triage"}
EVENT_TEXT_FIELDS = ("reason", "summary", "note", "error", "detail", "classification")
EVENT_LABEL_FIELDS = ("actor", "author", "by", "assignee", "profile", "kind", "status", "from", "to",
                      "old_status", "new_status", "source_status", "parent_id", "child_id", "parent", "child", "outcome")
SIGNED_QUERY = re.compile(r"[?&](?:key|sig(?:nature)?|token|auth|code|x-amz-[a-z-]+)=[^\s&#]+", re.I)


def _sensitive(value):
    return any(pattern.search(value) for pattern in (SECRET, PRIVATE_BLOCK, UNSAFE_FIELD, SIGNED_QUERY))


def _task_text(value, maximum):
    clean = _detail_text(value)
    return clean.strip()[:maximum] if clean else None


def _task_label(value):
    safe = label(value)
    return safe if safe and not _sensitive(safe) else None


def _board_enabled(directory):
    """Hermes leaves archived board tombstones beside the database."""
    path = directory / "board.json"
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            raise ValueError("Invalid Kanban board metadata")
        metadata = json.loads(bounded_read(path, JSON_LIMIT_BYTES))
    except FileNotFoundError:
        return True  # Legacy named boards may have no metadata.
    if not isinstance(metadata, dict):
        raise ValueError("Invalid Kanban board metadata")
    return not metadata.get("archived")


def _boards(home, deadline):
    paths = [("default", home / "kanban.db")]
    limited = False
    try:
        kanban = home / "kanban"
        root = kanban / "boards"
        # A profile's database must not be discovered through an unrelated symlink.
        if not all(stat.S_ISDIR(directory.lstat().st_mode) for directory in (kanban, root)):
            return paths, True
        with os.scandir(root) as entries:
            for index, entry in enumerate(entries):
                if index >= 128 or time.monotonic() >= deadline:
                    limited = True
                    break
                slug = _task_label(entry.name)
                if not slug or slug == "default" or not BOARD_SLUG.fullmatch(slug):
                    continue
                candidate = Path(entry.path) / "kanban.db"
                if entry.is_dir(follow_symlinks=False) and candidate.is_file():
                    try:
                        if _board_enabled(candidate.parent):
                            paths.append((slug, candidate))
                    except (OSError, ValueError):
                        limited = True
    except FileNotFoundError:
        pass
    except OSError:
        limited = True
    paths.sort(key=lambda item: (item[0] != "default", item[0]))
    return paths[:MAX_BOARDS], limited or len(paths) > MAX_BOARDS


def _task_rows(db):
    columns = sqlite_columns(db, "tasks")
    if not {"id", "title", "status"} <= columns:
        raise ValueError("Unsupported Kanban schema")
    fields = []
    for name in TASK_FIELDS:
        if name not in columns:
            fields.append(f"NULL AS {name}")
        elif name == "title":
            # Drop oversized text as a whole so truncated credentials cannot escape filtering.
            fields.append(f"CASE WHEN length(CAST({name} AS BLOB)) <= {TITLE_LIMIT_BYTES} "
                          f"THEN {name} END AS {name}")
        else:
            fields.append(name)
    order = [f"COALESCE({name}, 0)" for name in TASK_DATES if name in columns]
    recent = f"MAX({', '.join(['0', *order])}) DESC, " if order else ""
    return db.execute(f"SELECT {', '.join(fields)} FROM tasks ORDER BY {recent}id LIMIT ?",
                      (MAX_TASKS_PER_BOARD + 1,)).fetchall()


def _last_update(db, events, task, runs):
    values = [task[key] for key in TASK_DATES]
    if {"task_id", "created_at"} <= events:
        latest = db.execute("SELECT created_at FROM task_events WHERE task_id = ? ORDER BY created_at DESC LIMIT 1",
                            (task["id"],)).fetchone()
        if latest:
            values.append(latest["created_at"])
    for run in runs or ():
        values.extend((run["started_at"], run["ended_at"]))
    times = (timestamp(value) for value in values)
    return max((value for value in times if value is not None), default=None)


def _read_board(db, board, deadline):
    db.execute("BEGIN")  # Keep cards, dependency links and attempts in the same read snapshot.
    rows = _task_rows(db)
    links = sqlite_columns(db, "task_links")
    runs = sqlite_columns(db, "task_runs")
    events = sqlite_columns(db, "task_events")
    tasks = []
    limited = len(rows) > MAX_TASKS_PER_BOARD
    for row in rows[:MAX_TASKS_PER_BOARD]:
        if time.monotonic() >= deadline:
            limited = True
            break
        task_id = _task_label(row["id"])
        if not task_id:
            limited = True
            continue
        state = row["status"] if row["status"] in TASK_STATUSES else "unknown"
        limited |= state == "unknown"
        dependencies = None
        if {"parent_id", "child_id"} <= links:
            parents = db.execute("SELECT parent_id FROM task_links WHERE child_id = ? ORDER BY parent_id LIMIT ?",
                                 (task_id, MAX_DEPENDENCIES + 1)).fetchall()
            limited |= len(parents) > MAX_DEPENDENCIES
            dependencies = [f"{board}:{parent}" for record in parents[:MAX_DEPENDENCIES]
                            if (parent := _task_label(record["parent_id"]))]
        history = None
        recent_runs = None
        if {"id", "task_id", "status"} <= runs:
            order = "started_at DESC, id DESC" if "started_at" in runs else "id DESC"
            recent_runs = sqlite_select(db, "task_runs", ("status", "started_at", "ended_at"), runs,
                                        f"WHERE task_id = ? ORDER BY {order} LIMIT ?", (task_id, MAX_ATTEMPTS + 1))
            limited |= len(recent_runs) > MAX_ATTEMPTS
            history = [
                {"status": run["status"] if run["status"] in RUN_STATUSES else "unknown",
                 "at": utc(timestamp(run["ended_at"])) or utc(timestamp(run["started_at"]))}
                for run in recent_runs[:MAX_ATTEMPTS]
            ]
        session = _task_label(row["session_id"])
        updated = _last_update(db, events, row, recent_runs)
        task = {
            "id": f"{board}:{task_id}", "board": board, "title": _task_text(row["title"], 180) or task_id,
            "status": state, "assignee": _task_label(row["assignee"]), "project": _task_label(row["project_id"]),
            "tenant": _task_label(row["tenant"]),
            "dependsOn": dependencies, "attempts": history, "updatedAt": utc(updated), "session": session,
        }
        if session:
            task["sessionHref"] = f"/session/{session}"
        tasks.append((updated if updated is not None else -1, task))
    return tasks, limited


def collect_kanban(hermes_home: Path, deadline: float):
    """Collect the existing boards under the configured shared Hermes root."""
    boards, limited = _boards(hermes_home, deadline)
    records = []
    read = 0
    for slug, path in boards:
        if time.monotonic() >= deadline:
            limited = True
            break
        try:
            with open_db(path, deadline) as db:
                db.text_factory = lambda data: data.decode("utf-8", errors="replace")
                tasks, truncated = _read_board(db, slug, deadline)
            records.extend(tasks)
            limited |= truncated
            read += 1
        except FileNotFoundError:
            limited |= slug != "default"
        except (OSError, ValueError, sqlite3.Error):
            limited = True
    records.sort(key=lambda item: (-item[0], item[1]["id"]))
    limited |= len(records) > MAX_TASKS
    if not read:
        status = "unavailable"
        detail = "Kanban board data could not be read." if limited else "No initialized Kanban boards were found."
    else:
        status = "partial" if limited else "available"
        detail = f"Read {read} existing Kanban board(s)."
        if limited:
            detail += " Some boards or records are unavailable or exceed collection limits."
    return [task for _, task in records[:MAX_TASKS]], source("kanban", "Hermes Kanban", status, detail)


def _detail_columns(names, columns, text_limits=None, prefix=""):
    """Build a projection over adapter constants, bounding text before it leaves SQLite."""
    selected = []
    for name in names:
        if name not in columns:
            selected.append(f"NULL AS {name}")
        elif maximum := (text_limits or {}).get(name):
            col = f"{prefix}{name}"
            selected.append(f"CASE WHEN length(CAST({col} AS BLOB)) > {maximum} "
                            f"THEN '{OMITTED}' ELSE {col} END AS {name}")
        else:
            selected.append(f"{prefix}{name}")
    return ", ".join(selected)


def _detail_text(value):
    if not isinstance(value, str):
        return None
    clean = CONTROL.sub("", value)
    if _sensitive(clean):
        return "[redacted]"
    clean = LOCAL_PATH.sub("[local path]", SENSITIVE_LINK.sub(r"\1?[redacted]", clean))
    return clean if clean.strip() else None


def _detail_json(value):
    try:
        parsed = json.loads(value) if isinstance(value, str) else None
    except (ValueError, RecursionError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _record_id(value):
    return value if type(value) is int and 0 < value <= 2**53 - 1 else None


def _at(value):
    return utc(timestamp(value))


def _history_rows(db, table, task_id, names, text_limits, maximum, limits, section):
    columns = sqlite_columns(db, table)
    if not {"id", "task_id"} <= columns:
        return []
    fields = _detail_columns(names, columns, text_limits)
    clock = {"task_runs": "started_at"}.get(table, "created_at")
    order = f"{clock} DESC, " if clock in columns else ""
    rows = db.execute(f"SELECT {fields} FROM {table} WHERE task_id = ? ORDER BY {order}id DESC LIMIT ?",
                      (task_id, maximum + 1)).fetchall()
    if len(rows) > maximum:
        limits.add(section)
    rows = rows[:maximum]
    if any(row[name] == OMITTED for row in rows for name in text_limits):
        limits.add(f"{section}Text")
    return rows


def _dependencies(db, board, task, columns, limits):
    if not {"parent_id", "child_id"} <= sqlite_columns(db, "task_links"):
        return None, None
    fields = _detail_columns(("title", "status"), columns, {"title": TITLE_LIMIT_BYTES}, "t.")
    directions = (("parent_id", "child_id", "dependsOn"), ("child_id", "parent_id", "dependents"))
    grouped = []
    for linked, origin, section in directions:
        rows = db.execute(f"SELECT l.{linked} AS id, {fields}, t.id AS existing FROM task_links l "
                          f"LEFT JOIN tasks t ON t.id = l.{linked} WHERE l.{origin} = ? "
                          f"ORDER BY l.{linked} LIMIT ?", (task, MAX_DEPENDENCIES + 1)).fetchall()
        if len(rows) > MAX_DEPENDENCIES:
            limits.add(section)
        grouped.append([
            {"id": f"{board}:{key}", "title": _task_text(row["title"], 180) or key,
             "status": row["status"] if row["status"] in TASK_STATUSES else "unknown",
             "available": row["existing"] is not None}
            for row in rows[:MAX_DEPENDENCIES] if (key := _task_label(row["id"]))
        ])
    return tuple(grouped)


def _event(row):
    payload = _detail_json(row["payload"])
    summary = next((_detail_text(payload[name]) for name in EVENT_TEXT_FIELDS
                    if isinstance(payload.get(name), str) and payload[name].strip()), None)
    details = {name: value for name in EVENT_LABEL_FIELDS if (value := _task_label(payload.get(name)))}
    fields = payload.get("fields", payload.get("changed_fields"))
    if isinstance(fields, list):
        changed = [field for item in fields[:24] if (field := _task_label(item))]
        if changed:
            details["changed_fields"] = changed
    return {"id": _record_id(row["id"]), "kind": _task_label(row["kind"]) or "unknown", "at": _at(row["created_at"]),
            "runId": _record_id(row["run_id"]), "summary": summary, "details": details}


def _attempt(row):
    return {"id": _record_id(row["id"]), "status": row["status"] if row["status"] in RUN_STATUSES else "unknown",
            "profile": _task_label(row["profile"]), "step": _task_label(row["step_key"]),
            "startedAt": _at(row["started_at"]), "endedAt": _at(row["ended_at"]),
            "heartbeatAt": _at(row["last_heartbeat_at"]), "outcome": _task_label(row["outcome"]),
            "summary": _detail_text(row["summary"]), "error": _detail_text(row["error"])}


def _pull_requests(links, candidates, limits):
    seen = set()
    for url, evidence in candidates:
        if not isinstance(url, str) or _sensitive(url) or not (match := PULL_REQUEST.fullmatch(url)):
            continue
        identity = (match[1].lower(), match[2])
        if identity in seen:
            continue
        if len(links) >= MAX_LINKS:
            limits.add("links")
            break
        seen.add(identity)
        links.append({"kind": "pullRequest", "id": f"{match[1]}#{match[2]}", "href": url, "evidence": evidence})


def _link_events(db, task_id, limits):
    columns = sqlite_columns(db, "task_events")
    if not {"id", "task_id", "kind", "payload"} <= columns:
        return []
    fields = _detail_columns(("kind", "run_id", "payload"), columns, {"payload": JSON_LIMIT_BYTES})
    events = []
    for kind in ("owner_admitted", "pr_acceptance"):
        rows = db.execute(f"SELECT {fields} FROM task_events WHERE task_id = ? AND kind = ? "
                          "ORDER BY id DESC LIMIT ?", (task_id, kind, MAX_DETAIL_RUNS + 1)).fetchall()
        if len(rows) > MAX_DETAIL_RUNS or any(row["payload"] == OMITTED for row in rows):
            limits.add("links")
        events.extend(rows[:MAX_DETAIL_RUNS])
    return events


def _task_links(db, task, runs, limits):
    links = []
    origin = _task_label(task["session_id"])
    if origin:
        links.append({"kind": "session", "id": origin, "href": f"/session/{origin}",
                      "evidence": "originatingSession"})
    sessions = {origin}
    receipts = []
    for event in _link_events(db, task["id"], limits):
        payload = _detail_json(event["payload"])
        if event["kind"] == "pr_acceptance":
            receipts.append((payload.get("pr_url"), "completionAcceptance"))
            continue
        session = _task_label(payload.get("session_id"))
        run_id = _record_id(event["run_id"])
        if session and run_id and session not in sessions:
            links.append({"kind": "session", "id": session, "href": f"/session/{session}",
                          "evidence": "workerSession", "runId": run_id})
            sessions.add(session)
    published = [(_detail_json(run["metadata"]).get("published_pr"), "publishedByTaskRun") for run in runs]
    _pull_requests(links, [*receipts, *published, (task["completion_contract"], "requiredByTask")], limits)
    return links


def _blocked_reason(db, task):
    if task["status"] not in {"blocked", "todo", "triage"}:
        return None
    columns = sqlite_columns(db, "task_events")
    if {"task_id", "id", "kind", "payload"} <= columns:
        fields = _detail_columns(("kind", "payload"), columns, {"payload": JSON_LIMIT_BYTES})
        transitions = (*BLOCK_EVENTS, "created", "unblocked", "completed", "archived", "restored", "changes_requested",
                       "review_reopened", "status", "reclaimed", "stale", "timed_out", "crashed", "spawn_failed", "rate_limited")
        values = ", ".join("?" for _ in transitions)
        latest = db.execute(f"SELECT {fields} FROM task_events WHERE task_id = ? AND kind IN ({values}) "
                            "ORDER BY id DESC LIMIT 1", (task["id"], *transitions)).fetchone()
        if latest:
            payload = _detail_json(latest["payload"])
            status = payload.get("status") if latest["kind"] == "status" else BLOCK_EVENTS.get(latest["kind"])
            if status == task["status"]:
                if reason := _detail_text(payload.get("reason") or payload.get("error")):
                    return reason
    return _detail_text(task["last_failure_error"] or task["last_spawn_error"]) if task["status"] == "blocked" else None


def _task_detail(db, board, task_id):
    db.text_factory = lambda data: data.decode("utf-8", errors="replace")
    db.execute("BEGIN")
    columns = sqlite_columns(db, "tasks")
    if not {"id", "title", "status"} <= columns:
        raise ValueError("Unsupported Kanban schema")
    names = (*TASK_FIELDS, "body", "completion_contract", "result", "created_by", "block_kind", "last_failure_error",
             "last_spawn_error", "current_run_id", "last_heartbeat_at", "model_override", "provider_override")
    text_limits = {"title": TITLE_LIMIT_BYTES, "body": BODY_LIMIT_BYTES, "result": RESULT_LIMIT_BYTES,
                   "last_failure_error": NOTE_LIMIT_BYTES, "last_spawn_error": NOTE_LIMIT_BYTES,
                   "completion_contract": NOTE_LIMIT_BYTES}
    fields = _detail_columns(names, columns, text_limits)
    task = db.execute(f"SELECT {fields} FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if task is None:
        return None
    limits = {name for name in text_limits if task[name] == OMITTED}
    comments = _history_rows(db, "task_comments", task_id, ("id", "author", "body", "created_at"),
                             {"body": NOTE_LIMIT_BYTES}, MAX_DETAIL_COMMENTS, limits, "comments")
    events = _history_rows(db, "task_events", task_id, ("id", "kind", "payload", "run_id", "created_at"),
                           {"payload": JSON_LIMIT_BYTES}, MAX_DETAIL_EVENTS, limits, "events")
    runs = _history_rows(db, "task_runs", task_id, (
        "id", "profile", "step_key", "status", "started_at", "ended_at", "last_heartbeat_at", "outcome",
        "summary", "error", "metadata",
    ), {"summary": NOTE_LIMIT_BYTES, "error": NOTE_LIMIT_BYTES, "metadata": JSON_LIMIT_BYTES},
        MAX_DETAIL_RUNS, limits, "attempts")
    depends_on, dependents = _dependencies(db, board, task_id, columns, limits)
    attempts = [_attempt(row) for row in runs]
    history = [_event(row) for row in events]
    current_run_id = _record_id(task["current_run_id"]) if task["status"] == "running" else None
    current = next((run for run in attempts if current_run_id and run["id"] == current_run_id and
                    run["status"] == "running" and run["endedAt"] is None), None)
    current_event = next((event for event in history if current_run_id and event["runId"] == current_run_id), None)
    activity = None
    if current:
        activity = {"profile": current["profile"], "startedAt": current["startedAt"],
                    "heartbeatAt": current["heartbeatAt"] or _at(task["last_heartbeat_at"]),
                    "latestEvent": current_event}
    latest_summary = next((run["summary"] for run in attempts if run["status"] == "done" and run["summary"]), None)
    dates = {key: _at(task[name]) for key, name in zip(("createdAt", "startedAt", "completedAt"), TASK_DATES)}
    stamps = [task[name] for name in (*TASK_DATES, "last_heartbeat_at")]
    stamps.extend(row["created_at"] for row in (*comments, *events))
    stamps.extend(row[col] for row in runs for col in ("started_at", "ended_at", "last_heartbeat_at"))
    updated = max((t for value in stamps if (t := timestamp(value)) is not None), default=None)
    links = _task_links(db, task, runs, limits)
    return {"id": f"{board}:{task_id}", "board": board, "title": _detail_text(task["title"]) or task_id,
            "status": task["status"] if task["status"] in TASK_STATUSES else "unknown",
            "assignee": _task_label(task["assignee"]), "createdBy": _task_label(task["created_by"]),
            "executor": current["profile"] if current else None,
            "project": _task_label(task["project_id"]), "tenant": _task_label(task["tenant"]),
            "modelOverride": model_name(task["model_override"]), "providerOverride": _task_label(task["provider_override"]),
            "description": _detail_text(task["body"]), "criteria": _detail_text(task["completion_contract"]),
            "result": _detail_text(task["result"]) or (latest_summary if task["status"] == "done" else None),
            "blockedReason": _blocked_reason(db, task),
            "blockKind": task["block_kind"] if task["status"] == "blocked" and task["block_kind"] in BLOCK_KINDS else None,
            **dates, "updatedAt": utc(updated), "dependsOn": depends_on, "dependents": dependents,
            "comments": [{"id": _record_id(row["id"]), "author": _task_label(row["author"]),
                          "body": _detail_text(row["body"]), "at": _at(row["created_at"])} for row in comments],
            "events": history, "attempts": attempts, "currentRunId": current_run_id,
            "activity": activity, "links": links, "limits": sorted(limits)}


def _fit_detail(task):
    """Keep the current card and newest items when heavy history fills the response budget."""
    if task is None:
        return None

    def size(value):
        return len(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8"))

    used = size(task)
    limits = set(task["limits"])
    history = ("comments", "events", "attempts")
    while used > MAX_TASK_DETAIL_BYTES - 64:
        section = max(history, key=lambda name: len(task[name]))
        if len(task[section]) <= 1:
            break  # The fixed fields and one record per section fit independently of history.
        used -= size(task[section].pop()) + 1
        limits.add(section)
    task["limits"] = sorted(limits)
    return task


def read_kanban_task(hermes_home: Path, key: str, deadline: float):
    """Read one native card through the same board discovery and safety boundaries as the list."""
    if not isinstance(key, str) or len(key) > 145:
        return None
    board, separator, task_id = key.partition(":")
    if not separator or not BOARD_SLUG.fullmatch(board) or not _task_label(task_id):
        return None
    boards, _ = _boards(hermes_home, deadline)
    path = dict(boards).get(board)
    if path is None:
        return None
    if time.monotonic() >= deadline:
        raise TimeoutError("Kanban task read exceeded the deadline")
    try:
        with open_db(path, deadline) as db:
            result = _task_detail(db, board, task_id)
        return _fit_detail(result)
    except FileNotFoundError:
        return None
