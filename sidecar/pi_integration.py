"""Read native Pi Bridge jobs and Open Agents tool lifecycles without invoking either service."""

import ipaddress
import json
import os
import re
import stat
import time
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from safe_text import display_text
from telemetry import (MAX_PI_JOBS, MAX_PI_STATUS_BYTES, PI_FRESH_SECONDS, agent,
                       label, model_name, node_id, number, source, timestamp, utc)

MAX_JOB_BYTES = 256 * 1024
MAX_JOB_ENTRIES = 512
MAX_TRANSCRIPTS = 20
MAX_TRANSCRIPT_BYTES = 512 * 1024
MAX_SESSION_ENTRIES = 1024
MAX_RECORDS = 2_048
MAX_OPEN_AGENTS = 30
MAX_FEEDBACK = 20
READ_SECONDS = 1.5
TERMINAL = {"completed": "completed", "failed": "failed", "error": "failed", "cancelled": "cancelled",
            "canceled": "cancelled", "interrupted": "interrupted", "stopped": "interrupted"}
PI_WEB_FIELDS = ("machine", "project", "session", "workspace")
PI_WEB_VALUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_. -]{0,95}\Z")
PI_WEB_NETWORKS = tuple(ipaddress.IPv4Network(cidr) for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


def record(value):
    return value if isinstance(value, dict) else {}


def records(value, limit):
    return (entry for entry in value[-limit:] if isinstance(entry, dict)) if isinstance(value, list) else ()


def read_json(data):
    return record(json.loads(data))


def no_link(path, *, directory):
    info = path.lstat()
    mode = stat.S_ISDIR if directory else stat.S_ISREG
    return mode(info.st_mode) and not (
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


@contextmanager
def native_file(root, *parts):
    """Open a known relative file without following a link under the source directory."""
    if not parts or any(part in {"", ".", ".."} or Path(part).name != part for part in parts):
        raise ValueError("Invalid source path")
    target = root.joinpath(*parts)
    file_flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    handles = []
    try:
        if os.open in os.supports_dir_fd and hasattr(os, "O_NOFOLLOW"):
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY
            handles.append(os.open(root, flags))
            for part in parts[:-1]:
                handles.append(os.open(part, flags, dir_fd=handles[-1]))
            descriptor = os.open(parts[-1], file_flags | os.O_NOFOLLOW, dir_fd=handles[-1])
        else:
            if not all(no_link(root.joinpath(*parts[:index]), directory=True) for index in range(len(parts))):
                raise ValueError("Linked source directory")
            if not no_link(target, directory=False):
                raise ValueError("Linked source file")
            descriptor = os.open(target, file_flags)
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError("Non-regular source file")
            yield stream
    finally:
        for handle in reversed(handles):
            os.close(handle)


def bounded_json(root, parts, maximum):
    with native_file(root, *parts) as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError("File size limit")
    return read_json(data)


def job_key(value):
    value = label(value)
    return value if value and value.startswith("pb-") else None


def enumerate_jobs(root, limits, deadline):
    ranked = []
    if not no_link(root, directory=True):
        raise ValueError("Unsafe jobs directory")
    with os.scandir(root) as entries:
        for index, entry in enumerate(entries):
            if index >= MAX_JOB_ENTRIES or time.monotonic() >= deadline:
                limits.add(f"Job discovery stopped at {MAX_JOB_ENTRIES} directory entries or the read deadline.")
                break
            key = job_key(entry.name)
            if key and entry.is_dir(follow_symlinks=False):
                try:
                    info = (root / key / "job.json").lstat()
                except OSError:
                    continue
                if stat.S_ISREG(info.st_mode):
                    ranked.append((info.st_mtime_ns, key))
    return [key for _, key in sorted(ranked, reverse=True)]


def within_age(when, now, seconds=PI_FRESH_SECONDS):
    at = timestamp(when)
    return at is not None and -15 <= now - at <= seconds


def native_status(job, lamp_live, now):
    raw = job.get("status")
    if isinstance(raw, str) and raw in TERMINAL:
        return TERMINAL[raw], False
    watchdog = record(job.get("watchdog"))
    last_activity = watchdog.get("last_activity_at")
    if raw == "running" and (lamp_live or within_age(last_activity, now)):
        watchdog_state = watchdog.get("state")
        attention = watchdog_state == "suspected_stall" or (
            watchdog_state not in ("healthy", "recovered") and timestamp(watchdog.get("needs_attention_at")) is not None
        )
        return ("stalled" if attention else "running"), True
    if raw == "queued" and (lamp_live or within_age(job.get("updated_at"), now)):
        return "queued", True
    return "unknown", False


def event(node, key, kind, title, at, *, sender=None, recipient=None, body=None,
          reason=None, channel="communication", thread=None):
    return {
        "id": node["id"] + ":" + key, "channel": channel, "kind": kind,
        "title": title, "at": utc(timestamp(at)), "from": sender, "to": recipient,
        "fromName": node["name"] if sender == node["id"] else None,
        "toName": node["name"] if recipient == node["id"] else None,
        "body": body, "reason": reason, "thread": thread or node["id"],
        "project": node["project"], "team": None, "session": node["session"], "href": node["href"],
    }


def bridge_events(node, job, parent, limits):
    events = []
    target = node["id"]
    if node["task"]:
        events.append(event(node, "task", "delegation", "Delegated Pi task", job.get("created_at"),
                            sender=parent, recipient=target, body=display_text(job.get("task"))))
    feedbacks = job.get("feedbacks")
    if isinstance(feedbacks, list) and len(feedbacks) > MAX_FEEDBACK:
        limits.add(f"At most {MAX_FEEDBACK} recent feedback entries per Pi job.")
    feedbacks = feedbacks if isinstance(feedbacks, list) else []
    start_index = max(0, len(feedbacks) - MAX_FEEDBACK)
    origin_id = label(record(job.get("origin")).get("ui_session_id"))
    for index, feedback in enumerate(feedbacks[start_index:], start=start_index):
        feedback = record(feedback)
        body = display_text(feedback.get("text"))
        if body:
            key = node_id(target, "feedback", f"{feedback.get('at')}:{index}")
            feedback_origin = record(feedback.get("origin")).get("ui_session_id")
            events.append(event(node, key, "feedback", "Feedback received by Pi", feedback.get("at"),
                                sender=parent if origin_id and origin_id == feedback_origin else None,
                                recipient=target, body=body))
    delivery = record(job.get("delivery"))
    sent = delivery.get("ok") is True and delivery.get("attempted") is True
    outcome_at = delivery.get("at") if sent else job.get("updated_at")
    result, error = display_text(job.get("final_result")), display_text(job.get("error"))
    if result:
        events.append(event(node, "result", "result", "Pi result delivered" if sent else "Pi result recorded",
                            outcome_at, sender=target, recipient=parent if sent else None, body=result))
    if error:
        events.append(event(node, "error", "error", "Pi reported an error", outcome_at,
                            sender=target, recipient=parent if sent and not result else None, body=error))
    if delivery.get("attempted") is True and delivery.get("ok") is False:
        reason = display_text(delivery.get("reason"), 280) or display_text(delivery.get("error"), 280)
        events.append(event(node, "delivery", "error", "Pi delivery failed", delivery.get("at"),
                            sender=target, reason=reason, channel="technical"))
    if node["status"] == "stalled":
        issue = node["issue"]
        events.append(event(node, "watchdog", "error", "Pi watchdog requested attention", issue["at"],
                            sender=target, reason=issue["reason"], channel="technical"))
    return events


def session_parts(job, root):
    value = job.get("pi_session_file")
    if isinstance(value, str) and len(value) < 4096:
        path = Path(value)
        if path.is_absolute() and path.suffix == ".jsonl":
            try:
                parts = path.relative_to(root.absolute()).parts
            except ValueError:
                return None
            if len(parts) == 2 and ".." not in parts:
                return parts
    return None


def enumerate_sessions(root, wanted, limits, deadline):
    matches = defaultdict(list)
    if not no_link(root, directory=True):
        raise ValueError("Unsafe Pi sessions directory")
    count = 0
    with os.scandir(root) as projects:
        for project in projects:
            count += 1
            if count > MAX_SESSION_ENTRIES or time.monotonic() >= deadline:
                break
            if not project.is_dir(follow_symlinks=False):
                continue
            with os.scandir(project.path) as sessions:
                for session in sessions:
                    count += 1
                    if count > MAX_SESSION_ENTRIES or time.monotonic() >= deadline:
                        break
                    if session.name.endswith(".jsonl") and session.is_file(follow_symlinks=False):
                        suffix = session.name[:-6].rsplit("_", 1)[-1]
                        if suffix in wanted:
                            matches[suffix].append((project.name, session.name))
    if count > MAX_SESSION_ENTRIES or time.monotonic() >= deadline:
        limits.add(f"Pi session discovery stopped at {MAX_SESSION_ENTRIES} directory entries or the read deadline.")
    return matches


def transcript(root, parts, session_id):
    with native_file(root, *parts) as stream:
        header = stream.readline(4096)
        if not header.endswith(b"\n"):
            raise ValueError("Invalid Pi session header")
        header_record = read_json(header)
        if header_record.get("type") != "session" or header_record.get("id") != session_id:
            raise ValueError("Pi session ID mismatch")
        size = os.fstat(stream.fileno()).st_size
        clipped = size - len(header) > MAX_TRANSCRIPT_BYTES
        if clipped:
            stream.seek(size - MAX_TRANSCRIPT_BYTES - 1)
            if stream.read(1) != b"\n":
                stream.readline(MAX_TRANSCRIPT_BYTES)
        data = stream.read(MAX_TRANSCRIPT_BYTES)
    lines = data.splitlines()
    clipped |= len(lines) > MAX_RECORDS
    entries, uncertain = [], bool(data and not data.endswith(b"\n"))
    for line in lines[-MAX_RECORDS:]:
        try:
            entries.append(read_json(line))
        except (ValueError, RecursionError):
            uncertain = True
    return entries, clipped, uncertain


def open_agent_runs(node, entries, uncertain, limits, owns_call):
    calls, results = defaultdict(list), defaultdict(list)
    model = None
    latest_turn = None
    for index, entry in enumerate(entries):
        if entry.get("type") == "model_change":
            model = model_name(entry.get("modelId")) or model
        if entry.get("type") != "message":
            continue
        message = record(entry.get("message"))
        at = timestamp(message.get("timestamp"))
        at = timestamp(entry.get("timestamp")) if at is None or at > 253402300799 else at
        role = message.get("role")
        if role in ("assistant", "user"):
            latest_turn = index
        if role == "assistant":
            model = model_name(message.get("model")) or model
            for part in records(message.get("content"), 200):
                if part.get("type") == "toolCall" and part.get("name") == "subagent" and label(part.get("id")):
                    calls[part["id"]].append((index, at, record(part.get("arguments"))))
        elif role == "toolResult" and message.get("toolName") == "subagent":
            if label(message.get("toolCallId")):
                results[message["toolCallId"]].append((index, at, message))
    node["model"] = model
    if len(calls) > MAX_OPEN_AGENTS:
        limits.add(f"At most {MAX_OPEN_AGENTS} recent Open Agents calls per Pi transcript.")

    nodes, links, events = [], [], []
    parent_started = timestamp(node["startedAt"])
    for call_id in list(calls)[-MAX_OPEN_AGENTS:]:
        if len(calls[call_id]) != 1 or len(results[call_id]) > 1:
            limits.add("Ambiguous Open Agents tool-call IDs were omitted.")
            continue
        order, started, args = calls[call_id][0]
        if not owns_call(started):
            continue
        matching = results[call_id] and results[call_id][0]
        if matching and matching[0] <= order:
            limits.add("Open Agents tool results with inconsistent ordering were omitted.")
            continue
        name = label(args.get("agent")) or "Open Agents subagent"
        result = matching[2] if matching else {}
        details = record(result.get("details"))
        ended = matching[1] if matching else None
        pending = (not matching and not uncertain and node["status"] == "running" and order == latest_turn
                   and started is not None and (parent_started is None or started >= parent_started - 5))
        failed = result.get("isError") is True or details.get("isError") is True or (
            type(details.get("exitCode")) is int and details["exitCode"] != 0
        )
        state = "failed" if matching and failed else "completed" if matching else "waiting" if pending else "unknown"
        child = agent(None, f"{node['id']}:{call_id}", kind="subagent", name=name,
                      role="Open Agents", model=model_name(details.get("model")), status=state,
                      project=node["project"], session=node["session"], started_at=started, updated_at=ended or started)
        child.update(source="pi", profile=node["profile"], active=pending,
                     task=display_text(args.get("task"), 240, single_line=True),
                     lastAction="Tool result recorded" if matching else "Tool call has no recorded result",
                     detail="Pi transcript contains the delegated tool call and its result." if matching else
                            "Pi transcript contains a call without a recorded result. Its process state is unavailable.",
                     href=node["href"])
        nodes.append(child)
        links.append({"from": node["id"], "to": child["id"], "kind": "delegation"})
        events.append(event(child, "task", "delegation", "Open Agents delegation", started, sender=node["id"],
                            recipient=child["id"], body=display_text(args.get("task")), thread=node["id"]))
        if matching:
            output = display_text(details.get("output"))
            summary = record(details.get("usage"))
            measured = {
                "input": number(summary.get("input")), "output": number(summary.get("output")),
                "cached": number(summary.get("cacheRead")), "cacheWrite": number(summary.get("cacheWrite")),
                "contextTokens": number(summary.get("contextTokens")), "durationMs": number(details.get("elapsedMs")),
            }
            if any(value is not None for value in measured.values()):
                child["completedMetrics"] = measured
                child["jobId"] = node.get("jobId")
            reason = output if failed else None
            if failed:
                child["issue"] = {"reason":reason or "Open Agents returned a failed tool result.",
                                  "at":utc(ended), "lastActivityAt":utc(ended)}
            events.append(event(child, "result", "error" if failed else "result",
                                "Open Agents error" if failed else "Open Agents result", ended, sender=child["id"],
                                recipient=node["id"], body=output, reason=reason, thread=node["id"]))
    return nodes, links, events


def read_lamp(webui_state, now, limits):
    try:
        payload = bounded_json(webui_state, ("extensions", "pi-lamp", "status.json"), MAX_PI_STATUS_BYTES)
        jobs = payload.get("jobs")
        if not isinstance(jobs, list):
            raise ValueError("Unsupported Pi Lamp schema")
    except (OSError, ValueError, RecursionError):
        return {}, False, []
    fresh = within_age(payload.get("generated_at"), now)
    if len(jobs) > MAX_PI_JOBS:
        limits.add(f"At most {MAX_PI_JOBS} Pi Lamp cache entries.")
    cached = {}
    for job in jobs[:MAX_PI_JOBS]:
        job = record(job)
        if key := job_key(job.get("job_id")):
            cached[key] = job
    services = [{"id": "pi-lamp", "name": "Pi Lamp", "status": "active" if fresh else "unknown",
                 "detail": "Recent status cache" if fresh else "Status cache is stale or lacks a valid timestamp"}]
    return cached, fresh, services


def bridge_node(key, job, cached, live, parent, now):
    status, active = native_status(job, live, now)
    watchdog = record(job.get("watchdog"))
    last_turn = next(iter(records(job.get("turns"), 1)), {})
    last_activity = timestamp(watchdog.get("last_activity_at"))
    updated, created = timestamp(job.get("updated_at")), timestamp(job.get("created_at"))
    started = timestamp(last_turn.get("started_at")) if active and status != "queued" else created
    updated = max((at for at in (updated, last_activity) if at is not None), default=None)
    session = label(job.get("pi_session_id"))
    pi_web = record(cached.get("pi_web"))
    node = agent(None, key, kind="pi", name="Pi Bridge", role="Pi Bridge",
                 status=status, project=label(pi_web.get("project_name")), session=session, started_at=started or created,
                 updated_at=updated, detail="Native Pi Bridge job record")
    node.update(source="pi", profile=parent.get("profile"), active=active, jobId=key,
                task=display_text(job.get("task"), 240, single_line=True),
                href=pi_web_link(pi_web, session),
                lastAction={"queued": "Waiting in the Pi Bridge queue", "running": "Working on Pi task",
                            "stalled": "Pi watchdog requested attention", "unknown": "Current activity unconfirmed",
                            "completed": "Pi job completed", "cancelled": "Pi job cancelled",
                            "failed": "Pi job failed", "interrupted": "Pi job interrupted"}.get(status))
    if active and status == "running" and last_turn.get("kind") == "feedback":
        node["lastAction"] = "Working on feedback"
    if status in {"stalled", "failed", "interrupted"}:
        when = timestamp(watchdog.get("needs_attention_at")) if status == "stalled" else updated
        reason = display_text(watchdog.get("diagnosis") if status == "stalled" else job.get("error"), 320)
        node["issue"] = {"reason": reason or node["lastAction"], "at": utc(when or updated),
                         "lastActivityAt": utc(last_activity or updated)}
    return node


def pi_web_link(pi_web, session):
    value = pi_web.get("url")
    if (pi_web.get("available") is not True or not session or not isinstance(value, str)
            or len(value) > 1024 or value != value.strip()
            or any(ord(char) < 32 or ord(char) == 127 or char == "\\" for char in value)):
        return None
    try:
        parsed = urlsplit(value)
        address = ipaddress.IPv4Address(parsed.hostname or "")
        if (parsed.scheme != "http" or parsed.netloc != f"{address}:8504" or parsed.path != "/"
                or parsed.fragment or not any(address in network for network in PI_WEB_NETWORKS)):
            return None
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True, max_num_fields=4)
    except ValueError:
        return None
    fields = dict(pairs)
    if set(fields) != set(PI_WEB_FIELDS) or fields["session"] != session:
        return None
    if any(not PI_WEB_VALUE.fullmatch(value) or ".." in value or value != value.strip()
           or any(not label(part) for part in value.split()) or display_text(value) != value for value in fields.values()):
        return None
    return urlunsplit(("http", parsed.netloc, "/", urlencode([(key, fields[key]) for key in PI_WEB_FIELDS]), ""))


def job_intervals(node, job, now):
    windows = []
    for turn in records(job.get("turns"), 200):
        start = timestamp(turn.get("started_at"))
        end = timestamp(turn.get("finished_at"))
        if end is None and node["active"] and node["status"] != "queued":
            end = now
        if start is not None and end is not None and start <= end:
            windows.append((start, end))
    if windows:
        return windows
    start = timestamp(job.get("created_at"))
    end = now if node["active"] and node["status"] != "queued" else timestamp(node["updatedAt"])
    return [(start, end)] if start is not None and end is not None and start <= end else []


def attach_transcripts(jobs, pi_sessions, nodes, links, events, missing, limits, deadline):
    candidates = [(node, job) for node, job in jobs if node["session"]]
    candidates.sort(key=lambda item: (item[0]["active"], item[0]["updatedAt"] or ""), reverse=True)
    if len(candidates) > MAX_TRANSCRIPTS:
        limits.add(f"At most {MAX_TRANSCRIPTS} recent Pi session transcripts, with active jobs first.")
    candidates = candidates[:MAX_TRANSCRIPTS]
    now = time.time()
    owners = defaultdict(list)
    for node, job in jobs:
        if node["session"]:
            owners[node["session"]].append((node["id"], job_intervals(node, job, now)))
    cache = {}

    def load(parts, session):
        key = (parts, session)
        if key not in cache:
            cache[key] = transcript(pi_sessions, parts, session)
        return cache[key]
    discover = None
    unreadable = 0
    for node, job in candidates:
        if time.monotonic() >= deadline:
            limits.add(f"The {READ_SECONDS:g}s read deadline curtailed Pi transcript reads.")
            break
        session_id = node["session"]
        parts = session_parts(job, pi_sessions)
        observed = None
        if parts:
            try:
                observed = load(parts, session_id)
            except (OSError, ValueError, RecursionError):
                pass
        if observed is None:
            if discover is None:
                try:
                    discover = enumerate_sessions(pi_sessions, {item["session"] for item, _ in candidates}, limits, deadline)
                except (OSError, ValueError):
                    discover = {}
            matches = discover.get(session_id, ())
            if len(matches) == 1 and matches[0] != parts:
                try:
                    observed = load(matches[0], session_id)
                except (OSError, ValueError, RecursionError):
                    pass
        if observed is None:
            unreadable += 1
            continue
        entries, clipped, uncertain = observed
        if clipped:
            limits.add(f"Pi session reads show at most the last {MAX_TRANSCRIPT_BYTES // 1024} KiB and {MAX_RECORDS} records.")
        if uncertain:
            missing.add("Incomplete Pi transcript records leave some Open Agents activity unconfirmed.")
        intervals = owners[session_id]
        def owns_call(at):
            if at is None:
                return False
            matches = [key for key, windows in intervals if any(start <= at <= end for start, end in windows)]
            if len(matches) > 1:
                missing.add("Overlapping job intervals left some Open Agents calls unattributed.")
            return matches == [node["id"]]
        children, child_links, child_events = open_agent_runs(node, entries, uncertain, limits, owns_call)
        nodes.extend(children)
        links.extend(child_links)
        events.extend(child_events)
    if unreadable:
        missing.add(f"{unreadable} Pi session transcripts could not be uniquely verified and read.")


def collect_pi_bridge(webui_state, bridge_home, pi_sessions, hermes_nodes):
    now = time.time()
    deadline = time.monotonic() + READ_SECONDS
    nodes, links, events, native_jobs = [], [], [], []
    missing, limits = set(), set()
    cache, fresh, services = read_lamp(webui_state, now, limits)
    root = bridge_home / "jobs"
    try:
        jobs = enumerate_jobs(root, limits, deadline)
    except (OSError, ValueError):
        jobs = []
        missing.add("Native Pi Bridge jobs directory is unavailable.")
    active_cache = {key for key, entry in cache.items() if fresh and entry.get("group") == "active"}
    keys = list(dict.fromkeys([*(key for key in cache if key in active_cache), *jobs, *cache]))
    if len(keys) > MAX_PI_JOBS:
        limits.add(f"At most {MAX_PI_JOBS} native Pi jobs, with fresh active Lamp jobs first.")
    parents = defaultdict(list)
    for node in hermes_nodes:
        if node.get("kind") in {"session", "worker", "executor"} and node.get("session") and node.get("id"):
            parents[node["session"]].append(node)
    unreadable, unconfirmed, origin_missing, unresolved_links = 0, 0, 0, 0
    for key in keys[:MAX_PI_JOBS]:
        if time.monotonic() >= deadline:
            limits.add(f"The {READ_SECONDS:g}s read deadline curtailed native Pi job reads.")
            break
        try:
            job = bounded_json(root, (key, "job.json"), MAX_JOB_BYTES)
            if job.get("job_id") != key:
                raise ValueError("Native Pi job ID mismatch")
        except (OSError, ValueError, RecursionError):
            unreadable += 1
            cached = cache.get(key, {})
            if key in active_cache and cached.get("status") == "running":
                known = {"status": "running", "created_at": cached.get("created_at"), "updated_at": cached.get("updated_at")}
                limited = bridge_node(key, known, cached, True, {}, now)
                limited.update(detail="Fresh Pi Lamp identified this job. Its native record is unavailable.",
                               lastAction="Activity reported by Pi Lamp")
                nodes.append(limited)
            continue
        origin = record(job.get("origin"))
        origin_session = label(origin.get("ui_session_id"))
        matches = parents.get(origin_session, ())
        parent = matches[0] if len(matches) == 1 else {}
        node = bridge_node(key, job, cache.get(key, {}), key in active_cache, parent, now)
        nodes.append(node)
        native_jobs.append((node, job))
        parent_id = parent.get("id")
        if parent_id:
            links.append({"from": parent_id, "to": node["id"], "kind": "delegation"})
        elif node["active"] or origin_session:
            origin_missing += 1
        if job.get("status") in ("running", "queued") and not node["active"]:
            unconfirmed += 1
        if record(cache.get(key, {}).get("pi_web")).get("available") is True and not node["href"]:
            unresolved_links += 1
        events.extend(bridge_events(node, job, parent_id, limits))
    if unreadable:
        missing.add(f"{unreadable} Pi job records were unreadable, above {MAX_JOB_BYTES // 1024} KiB, or had mismatching IDs.")
    if unconfirmed:
        missing.add(f"{unconfirmed} running or queued job records lacked fresh activity evidence.")
    if origin_missing:
        missing.add(f"{origin_missing} Pi jobs lacked a unique, visible Hermes origin session.")
    if unresolved_links:
        missing.add(f"{unresolved_links} Pi Web links did not meet the exact native session and URL checks.")
    if not fresh:
        missing.add("Fresh Pi Lamp activity and Pi Web metadata were unavailable.")
    if any(node["active"] and node["status"] != "queued" and not node["session"] for node, _ in native_jobs):
        missing.add("Some active Pi jobs have no native Pi session ID for Open Agents lookup.")
    attach_transcripts(native_jobs, pi_sessions, nodes, links, events, missing, limits, deadline)
    status = "partial" if missing or limits else "available"
    if not native_jobs and not services and (unreadable or "Native Pi Bridge jobs directory is unavailable." in missing):
        status = "unavailable"
    info = source("pi", "Pi Bridge and Open Agents", status,
                  f"{len(native_jobs)} native Pi jobs and {len(nodes) - len(native_jobs)} observed Open Agents calls.")
    info.update(missing=sorted(missing), limits=sorted(limits),
                impact="Job activity, origin links, or Open Agents history may be incomplete." if status != "available" else None,
                recommendation="Check read access to the native Pi Bridge job and Pi session stores." if unreadable else None)
    return nodes, links, events, services, info
