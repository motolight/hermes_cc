"""Bounded, read-only adapters for the local Hermes Command Center."""

import csv
import hashlib
import http.client
import io
import ipaddress
import json
import math
import os
import re
import sqlite3
import stat
import subprocess
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path, PureWindowsPath

from safe_text import display_text

MAX_PROFILES = 24
MAX_SESSIONS = 80
MAX_DELEGATIONS = 50
MAX_USAGE = 800
MESSAGES_PER_SESSION = 3
DB_TIME_LIMIT = 2.0
READ_TIMEOUT = 0.75
MAX_METRICS_BYTES = 1024 * 1024
MAX_PI_STATUS_BYTES = 128 * 1024
MAX_PI_JOBS = 80
PI_FRESH_SECONDS = 120
UNIT_NAMES = (
    ("hermes-webui.service", "Hermes WebUI"),
    ("hermes-gateway.service", "Hermes Gateway"),
    ("pi-web.service", "Pi Web"),
    ("pi-web-sessiond.service", "Pi Web session daemon"),
    ("hermes-webui-project-folders.service", "Hermes project folders"),
    ("pi-bridge.service", "Pi Bridge"),
    ("vllm.service", "vLLM"),
)
TOKEN_FIELDS = {
    "input": "input_tokens",
    "output": "output_tokens",
    "cached": "cache_read_tokens",
    "reasoning": "reasoning_tokens",
    "requests": "api_call_count",
}
SESSION_SOURCES = {
    "web": "WebUI", "webui": "WebUI", "telegram": "Telegram", "discord": "Discord",
    "cli": "CLI", "tui": "TUI", "api": "API", "api_server": "API", "cron": "Scheduled job",
    "delegate": "Delegated session", "kanban": "Kanban worker", "child": "Child session",
}
MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@+\-/]{0,119}\Z")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\Z")
SECRET_LIKE = re.compile(r"(?:^|[/@:])(?:sk[-_]|gh[pousr]_|glpat-|AIza|eyJ|hf_|xox[baprs]-)", re.I)
VLLM_SERIES = {
    "running": ("vllm:num_requests_running",),
    "waiting": ("vllm:num_requests_waiting",),
    "kvCache": ("vllm:kv_cache_usage_perc", "vllm:gpu_cache_usage_perc"),
    "generation": ("vllm:generation_tokens_total",),
    "ttftSum": ("vllm:time_to_first_token_seconds_sum",),
    "ttftCount": ("vllm:time_to_first_token_seconds_count",),
}
VLLM_NAMES = frozenset(name for names in VLLM_SERIES.values() for name in names)
VLLM_COUNTERS = ("generation", "ttftSum", "ttftCount")
VLLM_PRIVATE_NETWORKS = tuple(ipaddress.IPv4Network(cidr) for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


@dataclass(frozen=True)
class MetricsOrigin:
    host: str
    port: int
    tls: bool = False


def parse_metrics_origin(value):
    match = re.fullmatch(r"(https?)://(\[::1\]|[0-9]{1,3}(?:\.[0-9]{1,3}){3}):([0-9]{1,5})", value)
    if not match:
        return None
    scheme, host, raw_port = match.groups()
    port = int(raw_port)
    if not 1 <= port <= 65535:
        return None
    if host == "[::1]":
        return MetricsOrigin("::1", port, scheme == "https")
    try:
        address = ipaddress.IPv4Address(host)
    except ipaddress.AddressValueError:
        return None
    if address.is_loopback or any(address in network for network in VLLM_PRIVATE_NETWORKS):
        return MetricsOrigin(str(address), port, scheme == "https")
    return None


def label(value):
    if isinstance(value, str) and IDENTIFIER.fullmatch(value) and not SECRET_LIKE.search(value):
        return value
    return None


def model_name(value):
    if (isinstance(value, str) and MODEL_ID.fullmatch(value)
            and not SECRET_LIKE.search(value) and not value.startswith(("http:", "https:"))
            and not any(part in {"", ".", ".."} for part in value.split("/"))
            and not re.match(r"[A-Za-z]:/", value)
            and not value.startswith(("home/", "mnt/", "tmp/", "var/", "Users/"))):
        return value
    return None


def number(value, maximum=1e15):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if math.isfinite(value) and 0 <= value <= maximum:
        return int(value) if value.is_integer() else value
    return None


def utc(value):
    value = number(value, maximum=253402300799)
    if value is not None:
        try:
            return datetime.fromtimestamp(value, timezone.utc).isoformat()
        except (OSError, OverflowError, ValueError):
            pass
    return None


def timestamp(value):
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                return parsed.timestamp()
        except (OSError, OverflowError, ValueError):
            pass
    return number(value, maximum=253402300799)


def source(key, name, status, detail):
    return {"id": key, "label": name, "status": status, "detail": detail}


def bounded_read(path, maximum):
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("Not a regular file")
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError("File exceeds collection limit")
    return data


def profile_databases(home):
    """Only consider default and immediate named profile state stores."""
    paths = [("default", home / "state.db")]
    truncated = False
    try:
        with os.scandir(home / "profiles") as entries:
            for index, entry in enumerate(entries):
                if index >= 128 or len(paths) == MAX_PROFILES:
                    truncated = True
                    break
                profile = label(entry.name)
                if profile and profile != "default" and entry.is_dir(follow_symlinks=False):
                    paths.append((profile, Path(entry.path) / "state.db"))
    except FileNotFoundError:
        pass
    except OSError:
        truncated = True
    return sorted(paths), truncated


def sqlite_columns(db, table):
    # Table names below are module literals, with no configuration or request input.
    return {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}


def sqlite_select(db, table, names, columns, where="", args=()):
    selected = ", ".join(name if name in columns else f"NULL AS {name}" for name in names)
    return db.execute(f"SELECT {selected} FROM {table} {where}", args).fetchall()


@contextmanager
def open_db(path, deadline):
    original = path.lstat()
    if not stat.S_ISREG(original.st_mode):
        raise ValueError("Not a regular state database")
    # SQLite can create WAL bookkeeping when its directory is writable.
    # Check for an existing pair before opening a live WAL reader.
    with path.open("rb") as stream:
        header = stream.read(100)
    if len(header) < 100 or header[:16] != b"SQLite format 3\0":
        raise ValueError("Unsupported state database")
    wal_mode = header[18:20] == b"\x02\x02"
    wal_files = [path.with_name(path.name + suffix) for suffix in ("-wal", "-shm")]
    wal_present = [sidecar.exists() for sidecar in wal_files] if wal_mode else []
    checkpointed = wal_mode and not any(wal_present)
    if wal_mode and not checkpointed and not all(stat.S_ISREG(sidecar.lstat().st_mode) for sidecar in wal_files):
        raise ValueError("WAL bookkeeping unavailable")
    # Clean, offline WAL files keep their WAL header after the writer closes.
    # Read these without generating SQLite bookkeeping. The read is discarded
    # if any sidecar or change to the main database is detected afterward.
    access = "?mode=ro&immutable=1" if checkpointed else "?mode=ro"
    db = sqlite3.connect(path.absolute().as_uri() + access, uri=True, timeout=0.08)
    try:
        db.row_factory = sqlite3.Row
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        db.execute("PRAGMA query_only = ON")
        db.execute("PRAGMA trusted_schema = OFF")
        db.execute("PRAGMA temp_store = MEMORY")
        yield db
    finally:
        db.close()
    if checkpointed:
        current = path.lstat()
        before = (original.st_dev, original.st_ino, original.st_size, original.st_mtime_ns, original.st_ctime_ns)
        after = (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns, current.st_ctime_ns)
        if before != after or any(sidecar.exists() for sidecar in wal_files):
            raise ValueError("Checkpointed store changed while reading")


def node_id(profile, kind, record_id):
    identity = f"{profile}\0{kind}\0{record_id}".encode()
    return f"{kind}:" + hashlib.sha256(identity).hexdigest()[:24]


def project_name(root):
    if isinstance(root, str):
        return label(PureWindowsPath(root).name)
    return None


def active_sessions(db):
    active = {}
    admissions = sqlite_columns(db, "session_admissions")
    if {"target_session_id", "status", "owner_epoch"} <= admissions and "epoch" in sqlite_columns(db, "runtime_epoch"):
        order = " ORDER BY MAX(seq) DESC" if "seq" in admissions else ""
        for row in db.execute(
            "SELECT target_session_id, MAX(status = 'started') AS running FROM session_admissions "
            "WHERE status = 'queued' OR "
            "(status = 'started' AND owner_epoch = (SELECT epoch FROM runtime_epoch WHERE singleton = 1)) "
            "GROUP BY target_session_id" + order + " LIMIT 160"
        ):
            active[row["target_session_id"]] = "running" if row["running"] else "queued"
    executions = sqlite_columns(db, "worker_executions")
    if {"session_id", "kind", "status", "owner_epoch"} <= executions and "epoch" in sqlite_columns(db, "runtime_epoch"):
        for row in db.execute(
            "SELECT DISTINCT session_id, kind FROM worker_executions "
            "WHERE status = 'running' AND owner_epoch = "
            "(SELECT epoch FROM runtime_epoch WHERE singleton = 1) LIMIT 160",
        ):
            active[row["session_id"]] = "running"
    return active


def agent(profile, record_id, *, kind, name, role, model=None, status="unknown",
          project=None, session=None, started_at=None, updated_at=None, detail=None):
    return {
        "id": node_id(profile, kind, record_id), "name": name, "kind": kind, "role": role,
        "model": model, "profile": profile, "status": status, "task": None,
        "project": project, "team": None, "session": session, "startedAt": utc(started_at),
        "updatedAt": utc(updated_at), "detail": detail, "href": None,
    }


def usage_row(row, session, at, model, task=None, *, counter_scope="session", counter_key=None, counter_created=None):
    return {
        "at": utc(at), "agent": session["id"], "project": session["project"],
        "model": model, "task": task, "session": session["session"],
        "counterScope": counter_scope, "counterKey": counter_key,
        "counterCreatedAt": timestamp(session["startedAt"]) if counter_created is None else counter_created,
        **{field: number(row[column]) for field, column in TOKEN_FIELDS.items()},
        "ttftMs": None, "tokensPerSecond": None, "durationMs": None, "contextTokens": None,
        "errors": None, "retries": None, "orchestration": None,
    }


def session_usage(db, rows, sessions):
    columns = sqlite_columns(db, "session_model_usage")
    per_model = []
    route_fields = ("billing_provider", "billing_base_url", "billing_mode")
    if sessions and {"session_id", "model", "task", "last_seen", *TOKEN_FIELDS.values()} <= columns:
        placeholders = ",".join("?" for _ in sessions)
        # Per-route records overlap the session counters. Prefer route records
        # when present, including auxiliary calls (which can exist only here).
        dimensions = [field for field in route_fields if field in columns]
        selected_dimensions = "".join(f", {field}" for field in dimensions)
        first_seen = "MIN(first_seen)" if "first_seen" in columns else "NULL"
        per_model = db.execute(
            f"SELECT session_id, substr(model, 1, 121) AS model, substr(task, 1, 81) AS task, "
            f"MAX(last_seen) AS last_seen, {first_seen} AS created, "
            + ", ".join(f"SUM({column}) AS {column}" for column in TOKEN_FIELDS.values())
            + selected_dimensions
            + f" FROM session_model_usage WHERE session_id IN ({placeholders}) "
            "GROUP BY session_id, session_model_usage.model, session_model_usage.task" + selected_dimensions
            + " ORDER BY last_seen DESC LIMIT ?",
            (*sessions, MAX_USAGE + 1),
        ).fetchall()
    usage = [
        usage_row(row, sessions[row["session_id"]], row["last_seen"],
                  model_name(row["model"]), label(row["task"]), counter_scope="route",
                  counter_created=timestamp(row["created"]), counter_key=hashlib.sha256(json.dumps([row["model"], row["task"],
                                                        *(row[k] for k in dimensions)]).encode()).hexdigest())
        for row in per_model[:MAX_USAGE]
    ]
    covered = {row["session_id"] for row in per_model}
    # Historical absolute counters may have no equivalent route data. A
    # selected route cannot be inferred from the aggregate, and some route
    # rows represent auxiliary calls absent from the session total.
    totals = {}
    for row in per_model[:MAX_USAGE]:
        counter = totals.setdefault(row["session_id"], dict.fromkeys(TOKEN_FIELDS.values(), 0))
        for column in counter:
            counter[column] += number(row[column]) or 0
    partial = len(per_model) > MAX_USAGE or any(
        row["id"] in totals and any((number(row[column]) or 0) > totals[row["id"]][column]
                                     for column in TOKEN_FIELDS.values())
        for row in rows
    )
    if len(per_model) <= MAX_USAGE:
        for row in rows:
            if row["id"] not in covered and row["id"] in sessions:
                session = sessions[row["id"]]
                entry = usage_row(row, session, row["last_activity_at"] or row["ended_at"] or row["started_at"],
                                  session["model"])
                # Zero-initialized counters alone do not establish observed traffic.
                if any(entry[field] for field in TOKEN_FIELDS) or (session["active"] and any(entry[field] is not None for field in TOKEN_FIELDS)):
                    usage.append(entry)
    return usage, partial


def run_state(value):
    return {
        "running": "running", "stalling": "stalled", "finalizing": "running",
        "completed": "completed", "complete": "completed", "succeeded": "completed",
        "failed": "failed", "error": "failed", "cancelled": "cancelled",
        "canceled": "cancelled", "interrupted": "interrupted", "stopped": "interrupted",
        "resumable": "paused", "claimed": "queued", "queued": "queued",
    }.get(value, "unknown")


def current_linux_owner(pid, start):
    if type(pid) is not int or type(start) is not int or pid <= 0 or start <= 0 or not hasattr(os, "geteuid"):
        return False
    path = Path("/proc") / str(pid) / "stat"
    try:
        if path.stat().st_uid != os.geteuid():
            return False
        fields = bounded_read(path, 8192).rpartition(b")")[2].split()
        return fields[0] not in {b"Z", b"X"} and int(fields[19]) == start
    except (OSError, IndexError, ValueError):
        return False


def event_for(node, kind, from_id=None):
    return {
        "id": node["id"] + ":" + kind, "at": node["updatedAt"] or node["startedAt"],
        "channel": "technical",
        "kind": kind, "from": from_id, "to": node["id"],
        "title": node["name"], "detail": node["status"], "project": node["project"],
        "team": node["team"], "session": node["session"],
    }


def message_events(db, sessions, deadline):
    columns = sqlite_columns(db, "messages")
    if not {"id", "session_id", "timestamp", "role"} <= columns:
        return [], False
    visibility = (" AND active = 1" if "active" in columns else "")
    tool_column = "tool_name" if "tool_name" in columns else "NULL AS tool_name"
    if "_compressed_summary" in columns:
        visibility += " AND _compressed_summary = 0"
    events = []
    title = {"user": "User message", "assistant": "Hermes message", "tool": "Tool result"}
    for session_id, node in sessions.items():
        if time.monotonic() >= deadline:
            return events, True
        rows = db.execute(
            f"SELECT id, role, timestamp, {tool_column} FROM messages WHERE session_id = ? "
            "AND role IN ('user', 'assistant', 'tool')" + visibility + " ORDER BY timestamp DESC LIMIT ?",
            (session_id, MESSAGES_PER_SESSION),
        ).fetchall()
        for row in rows:
            at = utc(row["timestamp"])
            if at is None or type(row["id"]) is not int:
                continue
            outgoing = row["role"] == "assistant"
            is_tool = row["role"] == "tool"
            tool = label(row["tool_name"]) if is_tool else None
            if "lastAction" not in node:
                node["lastAction"] = f"Tool result · {tool}" if tool else title[row["role"]]
                node["lastActivityAt"] = at
            events.append({
                **event_for(node, "tool" if is_tool else "message"),
                "id": f"{node['id']}:message:{row['id']}", "at": at,
                "from": node["id"] if outgoing else None,
                "to": None if outgoing else node["id"], "title": f"Tool result · {tool}" if tool else title[row["role"]],
                "detail": "Hermes tool metadata" if is_tool else "Hermes message metadata",
            })
    return events, False


def delegation_state(db, sessions, profile):
    nodes, links, events = [], [], []
    tables = (
        ("async_delegations", "delegation_id", "dispatched_at", "delegation", "Async delegation",
         ("parent_session_id", "origin_session_id")),
        ("resumable_subagents", "subagent_id", "created_at", "subagent", "Resumable subagent", ()),
    )
    clipped = False
    delegations = {}
    for table, identity, started, kind, name, parents in tables:
        columns = sqlite_columns(db, table)
        if not {identity, "state", started, "updated_at"} <= columns:
            continue
        json_fields = {}
        if kind == "subagent" and "record_json" in columns:
            # The pinned upstream record ties this durable run to a child
            # session and, for async children, its exact delegation unit.
            # Extract only those two scalar IDs in SQLite.
            try:
                db.execute("SELECT json_valid('{}')").fetchone()
            except sqlite3.OperationalError:
                pass
            else:
                json_fields = {
                    entry["subagent_id"]: (label(entry["child_session"]), label(entry["delegation_unit"]))
                    for entry in db.execute(
                        "SELECT subagent_id, CASE WHEN length(record_json) <= 65536 AND json_valid(record_json) "
                        "THEN json_extract(record_json, '$.session_id') END AS child_session, "
                        "CASE WHEN length(record_json) <= 65536 AND json_valid(record_json) "
                        "THEN json_extract(record_json, '$.unit_id') END AS delegation_unit "
                        "FROM resumable_subagents ORDER BY updated_at DESC LIMIT ?",
                        (MAX_DELEGATIONS,),
                    )
                }
        rows = sqlite_select(
            db, table, (identity, "state", started, "updated_at", "completed_at", "origin_ui_session_id",
                        "owner_pid", "owner_started_at", *parents), columns,
            "ORDER BY updated_at DESC LIMIT ?", (MAX_DELEGATIONS + 1,),
        )
        clipped |= len(rows) > MAX_DELEGATIONS
        for row in rows[:MAX_DELEGATIONS]:
            record_id = label(row[identity])
            if not record_id:
                continue
            parent = next((sessions[row[key]] for key in (*parents, "origin_ui_session_id") if row[key] in sessions), None)
            child_id, unit_id = json_fields.get(record_id, (None, None))
            child = sessions.get(child_id)
            parent = parent or delegations.get(unit_id)
            context = child or parent
            bound_session = child_id or (context["session"] if context else None)
            node = agent(
                profile, record_id, kind=kind, name=name, role=kind,
                model=child["model"] if child else None, status=run_state(row["state"]),
                project=context["project"] if context else None,
                session=bound_session, started_at=row[started],
                updated_at=row["updated_at"], detail="Persisted Hermes run state",
            )
            active_status = node["status"] in {"running", "queued", "stalled"}
            updated_at = timestamp(row["updated_at"])
            recent = updated_at is not None and 0 <= time.time() - updated_at <= 120
            node["active"] = active_status and row["completed_at"] is None and bool(
                (child and child.get("active")) or current_linux_owner(row["owner_pid"], row["owner_started_at"])
                or (recent and parent and parent.get("active")))
            if active_status and not node["active"]:
                node["reportedStatus"] = node["status"]
                node["status"] = "unknown"
                node["detail"] = "The durable record has a nonterminal state without current owner activity."
            nodes.append(node)
            if kind == "delegation":
                delegations[record_id] = node
            if parent:
                links.append({"from": parent["id"], "to": node["id"], "kind": "delegation"})
            if child:
                links.append({"from": node["id"], "to": child["id"], "kind": "execution"})
            task_data = {}
            if kind == "delegation" and {"task_json", "result_json"} & columns:
                fields = [f"CASE WHEN length({field}) <= 65536 THEN {field} END AS {field}" if field in columns else f"NULL AS {field}"
                          for field in ("task_json", "result_json")]
                native = db.execute(f"SELECT {', '.join(fields)} FROM async_delegations WHERE delegation_id = ?", (record_id,)).fetchone()
                if native:
                    task_data = {field: parse_object(native[field]) for field in ("task_json", "result_json")}
            goal = display_text(task_data.get("task_json", {}).get("goal"))
            result = task_data.get("result_json", {})
            if goal:
                node["task"] = display_text(goal, 180, single_line=True)
            event = event_for(node, kind, parent["id"] if parent else None)
            if kind == "delegation" and (parent or goal):
                event.update(id=node["id"] + ":delegated", at=node["startedAt"], channel="communication",
                             title="Hermes delegation", body=goal, thread=node["id"])
            events.append(event)
            if kind == "delegation" and result:
                failure = display_text(result.get("error"))
                summary = display_text(result.get("summary"))
                if summary or failure:
                    events.append({
                        **event, "id": node["id"] + ":result", "at": utc(row["completed_at"]) or node["updatedAt"],
                        "from": node["id"], "to": parent["id"] if parent else None,
                        "channel": "communication", "kind": "error" if failure else "result", "thread": node["id"],
                        "title": "Hermes delegation failed" if failure else "Hermes delegation result",
                        "body": failure or summary, "reason": failure,
                    })
                    node["lastAction"] = "Reported an error" if failure else "Returned a result"
                if failure and node["status"] in {"failed", "stalled", "interrupted"}:
                    node["issue"] = {"reason": failure, "at": utc(row["completed_at"]) or node["updatedAt"],
                                     "lastActivityAt": node["updatedAt"]}
    return nodes, links, events, clipped


def parse_object(value):
    try:
        obj = json.loads(value) if value else {}
    except (ValueError, TypeError):
        return {}
    return obj if isinstance(obj, dict) else {}


def read_profile(path, profile, deadline):
    with open_db(path, deadline) as db:
        # One SQLite read transaction keeps session counters and model rows at
        # the same WAL snapshot. Text returned here consists of metadata only.
        db.execute("BEGIN")
        columns = sqlite_columns(db, "sessions")
        if not {"id", "source", "started_at"} <= columns:
            raise ValueError("Unsupported sessions schema")
        fields = (
            "id", "source", "model", "parent_session_id", "started_at", "ended_at", "last_activity_at",
            "git_repo_root", *TOKEN_FIELDS.values(),
        )
        rows = sqlite_select(
            db, "sessions", fields, columns,
            ("WHERE COALESCE(hidden, 0) = 0 " if "hidden" in columns else "")
            + "ORDER BY started_at DESC LIMIT ?", (MAX_SESSIONS + 1,),
        )
        clipped = len(rows) > MAX_SESSIONS
        rows = [row for row in rows[:MAX_SESSIONS] if label(row["id"])]
        active = active_sessions(db)
        known = {row["id"] for row in rows}
        extra = [sid for sid in active if sid not in known and label(sid)]
        if extra:
            placeholders = ",".join("?" for _ in extra)
            condition = " AND COALESCE(hidden, 0) = 0" if "hidden" in columns else ""
            rows.extend(sqlite_select(db, "sessions", fields, columns, f"WHERE id IN ({placeholders})" + condition, extra))
        nodes, links, events, sessions = [], [], [], {}
        titles = {}
        if "title" in columns and rows:
            ids = [row["id"] for row in rows]
            placeholders = ",".join("?" for _ in ids)
            titles = {row["id"]: display_text(row["title"], 180, single_line=True) for row in db.execute(
                f"SELECT id, CASE WHEN length(title)<=4096 THEN title END AS title FROM sessions WHERE id IN ({placeholders})", ids)}
        for row in rows:
            channel = SESSION_SOURCES.get(row["source"], "Hermes session")
            kind = "worker" if row["source"] in {"delegate", "child", "kanban", "cron"} else "session"
            status = active.get(row["id"], "ended" if row["ended_at"] is not None else "unknown")
            node = agent(
                profile, row["id"], kind=kind, name=channel, role="Hermes",
                model=model_name(row["model"]), status=status, project=project_name(row["git_repo_root"]),
                session=row["id"], started_at=row["started_at"],
                updated_at=row["last_activity_at"] or row["ended_at"] or row["started_at"],
                detail="Persisted Hermes session metadata",
            )
            node["task"] = titles.get(row["id"])
            node["active"] = row["id"] in active
            sessions[row["id"]] = node
            nodes.append(node)
        for row in rows:
            node = sessions[row["id"]]
            parent = sessions.get(row["parent_session_id"])
            # The same column is also used by compressed conversation
            # segments. Their edges indicate lineage, never delegation.
            if parent and parent["id"] != node["id"]:
                links.append({"from": parent["id"], "to": node["id"], "kind": "lineage"})
            if node["updatedAt"]:
                events.append(event_for(node, "session"))
        usage, usage_clipped = session_usage(db, rows, sessions)
        runs, run_links, run_events, runs_clipped = delegation_state(db, sessions, profile)
        active_first = dict(sorted(sessions.items(), key=lambda item: not item[1]["active"]))
        messages, messages_clipped = message_events(db, active_first, deadline)
    missing_counters = not set(TOKEN_FIELDS.values()) <= columns
    limits = []
    if clipped:
        limits.append(f"{MAX_SESSIONS} historical sessions per profile; current owner records are checked separately.")
    if runs_clipped:
        limits.append(f"{MAX_DELEGATIONS} recent delegation records per native table.")
    if messages_clipped:
        limits.append("The read deadline curtailed technical message metadata.")
    if usage_clipped:
        limits.append("Usage routes are limited or overlap an older cumulative session count.")
    if missing_counters:
        limits.append("This profile schema does not expose all native token counters.")
    return (
        nodes + runs, links + run_links, events + run_events + messages, usage,
        limits,
    )


def collect_hermes(home):
    nodes, links, events, usage = [], [], [], []
    paths, limited_profiles = profile_databases(home)
    limits = set()
    if limited_profiles:
        limits.add(f"Profile discovery was limited to {MAX_PROFILES} stores or the profile directory could not be read.")
    available, unavailable = 0, 0
    failures = Counter()
    seen = set()
    deadline = time.monotonic() + DB_TIME_LIMIT
    for profile, path in paths:
        if time.monotonic() >= deadline:
            limits.add(f"The {DB_TIME_LIMIT:g}s cycle deadline curtailed profile reads.")
            break
        try:
            info = path.lstat()
            identity = (info.st_dev, info.st_ino)
            if info.st_ino and identity in seen:
                continue
            values = read_profile(path, profile, deadline)
        except (OSError, ValueError, sqlite3.Error) as error:
            unavailable += 1
            if isinstance(error, FileNotFoundError):
                reason = "Native state.db or existing WAL bookkeeping is missing"
            elif isinstance(error, PermissionError):
                reason = "Access to the native state files was denied"
            elif isinstance(error, sqlite3.Error):
                reason = "SQLite timed out, was busy or could not read the expected schema"
            else:
                reason = "The store changed while reading or uses an unsupported format"
            failures[reason] += 1
            continue
        if info.st_ino:
            seen.add(identity)
        available += 1
        limits.update(values[4])
        nodes.extend(values[0])
        links.extend(values[1])
        events.extend(values[2])
        usage.extend(values[3])
    if available == 0:
        status, detail = "unavailable", "No readable Hermes profile state."
    elif unavailable or limits:
        status, detail = (
            "partial",
            f"{available} profile stores read. {unavailable} stores could not be read.",
        )
    else:
        status, detail = "available", f"{available} profile stores read. Usage is cumulative per session or model."
    info = source("hermes", "Hermes profile state", status, detail)
    info.update(limits=sorted(limits), missing=[f"{count} · {reason}" for reason,count in sorted(failures.items())],
                impact="Historical runs and token coverage may be incomplete." if unavailable or limits else None,
                recommendation="Inspect the profile store and check filesystem read permissions." if unavailable else None)
    return nodes, links, events, usage, info


GPU_QUERY = (
    "nvidia-smi", "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
    "--format=csv,noheader,nounits",
)


def gpu_probe(origin, user):
    if not user:
        return list(GPU_QUERY), "NVIDIA GPUs", 1.2
    if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", user) or origin is None:
        raise ValueError("Unsupported GPU SSH configuration")
    address = ipaddress.IPv4Address(origin.host)
    if not any(address in network for network in VLLM_PRIVATE_NETWORKS):
        raise ValueError("GPU SSH requires a private vLLM host")
    options = [
        "BatchMode=yes", "StrictHostKeyChecking=yes", "UpdateHostKeys=no", "VerifyHostKeyDNS=no",
        "IdentityAgent=none", "ForwardAgent=no", "ClearAllForwardings=yes", "ConnectTimeout=2",
        "ConnectionAttempts=1", "ServerAliveInterval=2", "LogLevel=ERROR",
    ]
    return [
        "ssh", "-nT", "-F", os.devnull, *[item for option in options for item in ("-o", option)],
        "-l", user, str(address), " ".join(GPU_QUERY),
    ], "NVIDIA GPUs on vLLM host", 3.6


def collect_gpu(origin=None, user=None):
    command, label_text, timeout = gpu_probe(origin, user)
    result = subprocess.run(
        command, capture_output=True, check=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )
    rows = list(islice(csv.reader(io.StringIO(result.stdout), skipinitialspace=True), 33))
    gpus = []
    partial = len(rows) > 32
    for row in rows[:32]:
        if len(row) != 7 or not row[0].strip().isdigit():
            partial = True
            continue
        index, name, utilization, used, total, temperature, power = (value.strip() for value in row)
        gpu = {
            "id": str(int(index)), "name": name if re.fullmatch(r"[A-Za-z0-9 ._()+-]{1,100}", name) else "NVIDIA GPU",
            "utilization": number(utilization, 100), "memoryUsed": number(used), "memoryTotal": number(total),
            "temperature": number(temperature), "power": number(power),
        }
        partial |= any(gpu[field] is None for field in ("utilization", "memoryUsed", "memoryTotal", "temperature", "power"))
        gpus.append(gpu)
    status = ("partial" if partial else "available") if gpus else "unavailable"
    detail = f"{len(gpus)} GPUs reported by NVIDIA." if gpus else "No readable NVIDIA GPU telemetry."
    return gpus, source("gpu", label_text, status, detail)


def collect_metrics(origin):
    connection = http.client.HTTPSConnection if origin.tls else http.client.HTTPConnection
    conn = connection(origin.host, origin.port, timeout=READ_TIMEOUT)
    deadline = time.monotonic() + 1.25
    try:
        conn.request("GET", "/metrics", headers={"Connection": "close", "Accept": "text/plain"})
        response = conn.getresponse()
        if response.status != 200:
            raise ValueError("Metrics unavailable")
        body = bytearray()
        while time.monotonic() < deadline:
            chunk = response.read1(min(65536, MAX_METRICS_BYTES + 1 - len(body)))
            if not chunk:
                break
            body.extend(chunk)
            if len(body) > MAX_METRICS_BYTES:
                raise ValueError("Metrics exceed collection limit")
        else:
            raise TimeoutError("Metrics deadline")
    finally:
        conn.close()
    return parse_metrics(body.decode("utf-8"))


def parse_metrics(body):
    samples = {}
    for line in body.splitlines():
        match = re.fullmatch(r"([a-zA-Z_:][a-zA-Z_0-9:]*)(\{.*\})?\s+(\S+)(?:\s+\d+)?", line)
        if match:
            name, labels, value = match.groups()
            if name in VLLM_NAMES:
                parsed = number(value)
                if parsed is not None:
                    samples.setdefault(name, {})[labels or ""] = parsed
    result = {}
    for kind, names in VLLM_SERIES.items():
        series = next((samples[name] for name in names if name in samples), {})
        if kind == "kvCache":
            values = [value for value in series.values() if value <= 1]
            result[kind] = max(values, default=None)
        elif kind in VLLM_COUNTERS:
            result[kind] = series
        else:
            result[kind] = sum(series.values()) if series else None
    return result


def collect_systemd(scope):
    services = {}
    reached = False
    units = dict(UNIT_NAMES)
    scopes = ("system", "user") if scope == "both" else (scope,)
    for manager in scopes:
        try:
            result = subprocess.run(
                ["systemctl", f"--{manager}", "show", "--no-pager", "--property=Id,LoadState,ActiveState", *units],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=0.75,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        reached |= result.returncode == 0 or bool(result.stdout)
        for block in re.split(r"\r?\n\r?\n", result.stdout.strip()):
            fields = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
            unit = fields.get("Id")
            if unit not in units or fields.get("LoadState") != "loaded":
                continue
            state = fields.get("ActiveState")
            if state not in {"active", "inactive", "failed", "activating", "deactivating", "reloading", "maintenance"}:
                state = "unknown"
            if unit in services and services[unit]["status"] == "active":
                continue
            services[unit] = {"id": unit.removesuffix(".service"), "name": units[unit],
                              "status": state, "detail": f"systemd {manager} unit"}
    state = "available" if services else "partial" if reached else "unavailable"
    detail = f"{len(services)} recognized systemd units reported." if reached else "No systemd status reported."
    return list(services.values()), source("systemd", "Local services", state, detail)


def pi_job_status(job, fresh):
    finished = timestamp(job.get("finished_at"))
    exit_code = job.get("exit_code")
    if finished is not None and type(exit_code) is int:
        return "completed" if exit_code == 0 else "failed"
    if finished is not None:
        return "ended"
    if fresh and job.get("group") == "active":
        return "active"
    return "unknown"


def collect_pi(webui_state):
    """Use only the small status cache already generated by the Pi Lamp extension."""
    lamp_status = webui_state / "extensions" / "pi-lamp" / "status.json"
    try:
        payload = json.loads(bounded_read(lamp_status, MAX_PI_STATUS_BYTES))
    except (OSError, ValueError, RecursionError):
        payload = None
    nodes, events, services = [], [], []
    valid_lamp = isinstance(payload, dict) and isinstance(payload.get("jobs"), list)
    fresh = False
    if valid_lamp:
        generated = timestamp(payload.get("generated_at"))
        fresh = generated is not None and -15 <= time.time() - generated <= PI_FRESH_SECONDS
        for job in payload["jobs"][:MAX_PI_JOBS]:
            if not isinstance(job, dict) or not label(job.get("job_id")):
                continue
            created = timestamp(job.get("created_at"))
            finished = timestamp(job.get("finished_at"))
            pi_web = job.get("pi_web")
            project = label(pi_web.get("project_name")) if isinstance(pi_web, dict) else None
            status = pi_job_status(job, fresh)
            node = agent(
                None, job["job_id"], kind="worker", name="Pi job", role="Pi Bridge", project=project,
                status=status, started_at=created, updated_at=finished,
                detail="Aggregated Pi Lamp event",
            )
            nodes.append(node)
            kind = "error" if status == "failed" else "result" if status == "completed" else "status"
            events.append(event_for(node, kind))
        services.append({"id": "pi-lamp", "name": "Pi Lamp", "status": "active" if fresh else "unknown",
                         "detail": "Recent status cache" if fresh else "Status cache is stale or lacks a valid timestamp"})
    if not valid_lamp:
        status, detail = "unavailable", "No readable Pi Lamp status cache."
    elif fresh and len(payload["jobs"]) <= MAX_PI_JOBS:
        status, detail = "available", "Recent aggregated Pi Lamp status."
    else:
        status, detail = "partial", "Pi Lamp status is stale or exceeds collection limits."
    return nodes, events, services, source("pi", "Pi Bridge and Lamp", status, detail)
