"""Read existing Hermes Kanban stores without loading Hermes or WebUI handlers."""

import os
import re
import sqlite3
import stat
import time
from pathlib import Path

from telemetry import label, open_db, source, sqlite_columns, sqlite_select, timestamp, utc

MAX_BOARDS = 16
MAX_TASKS_PER_BOARD = 100
MAX_TASKS = 180
MAX_DEPENDENCIES = 24
MAX_ATTEMPTS = 8
BOARD_SLUG = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
TASK_STATUSES = frozenset("triage todo scheduled ready running blocked review done archived".split())
RUN_STATUSES = TASK_STATUSES | {"crashed", "timed_out", "failed", "released", "reclaimed"}
TASK_DATES = ("created_at", "started_at", "completed_at", "updated_at")
TASK_FIELDS = ("id", "title", "assignee", "status", "project_id", "tenant", "session_id", *TASK_DATES)
TITLE_LIMIT_BYTES = 1024
CREDENTIAL = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|\b(?:sk[-_]|gh[pousr]_|github_pat_|glpat-|AIza|hf_|xox[baprs]-|AKIA|eyJ)[A-Za-z0-9._-]{8,}"
    r"|\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"
    r"|\b(?:[A-Za-z0-9_-]*[_-])?(?:api[_ -]?key|access[_ -]?token|auth[_ -]?token|token|"
    r"password|passwd|secret|credentials?|authorization)\b[\"']?\s*[:=]\s*[^\s;]+"
    r"|://[^\s/@]+:[^\s/@]+@",
    re.IGNORECASE,
)
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _task_text(value, maximum):
    if not isinstance(value, str):
        return None
    clean = CONTROL.sub("", value).strip()
    if CREDENTIAL.search(clean):
        return "[redacted]"
    return clean[:maximum] or None


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
                slug = label(entry.name)
                if not slug or slug == "default" or not BOARD_SLUG.fullmatch(slug):
                    continue
                candidate = Path(entry.path) / "kanban.db"
                if entry.is_dir(follow_symlinks=False) and candidate.is_file():
                    paths.append((slug, candidate))
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
        task_id = label(row["id"])
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
                            if (parent := label(record["parent_id"]))]
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
        session = label(row["session_id"])
        updated = _last_update(db, events, row, recent_runs)
        task = {
            "id": f"{board}:{task_id}", "board": board, "title": _task_text(row["title"], 180) or task_id,
            "status": state, "assignee": label(row["assignee"]), "project": label(row["project_id"]),
            "tenant": label(row["tenant"]),
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
