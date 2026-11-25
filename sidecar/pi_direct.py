"""Observe standalone Pi sessions through bounded native transcript reads."""

import os
import stat
import time
from collections import Counter, defaultdict
from itertools import islice

from pi_integration import (MAX_RECORDS, MAX_SESSION_ENTRIES, MAX_TRANSCRIPT_BYTES, READ_SECONDS,
                            native_file, no_link, open_agent_runs, read_json, record, transcript)
from telemetry import agent, label, model_name, source, timestamp
from topology import PI_TERMINAL

MAX_DIRECT_TRANSCRIPTS = 32
MAX_DIRECT_LIVE = 32


def discover(root, limits, missing, deadline):
    if not no_link(root, directory=True):
        raise ValueError("Unsafe Pi sessions directory")
    ranked = []
    count = 0
    with os.scandir(root) as projects:
        for project in projects:
            count += 1
            if count > MAX_SESSION_ENTRIES or time.monotonic() >= deadline:
                break
            try:
                if not no_link(root / project.name, directory=True):
                    continue
                with os.scandir(project.path) as sessions:
                    for entry in sessions:
                        count += 1
                        if count > MAX_SESSION_ENTRIES or time.monotonic() >= deadline:
                            break
                        if not entry.name.endswith(".jsonl"):
                            continue
                        key = label(entry.name[:-6].rsplit("_", 1)[-1])
                        if not key:
                            continue
                        try:
                            info = entry.stat(follow_symlinks=False)
                        except OSError:
                            missing.add("Some standalone Pi session entries could not be inspected.")
                            continue
                        if stat.S_ISREG(info.st_mode) and not (
                            getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
                        ):
                            ranked.append((info.st_mtime_ns, key, (project.name, entry.name)))
            except OSError:
                missing.add("Some standalone Pi session entries could not be inspected.")
    if count > MAX_SESSION_ENTRIES or time.monotonic() >= deadline:
        limits.add(f"Standalone Pi discovery stopped at {MAX_SESSION_ENTRIES} entries or the read deadline.")
    return ranked


def parent_node(key, live, session_created):
    status = live.get("status", "unknown")
    started = timestamp(live.get("startedAt")) if live else session_created
    node = agent(None, f"standalone:{key}", kind="pi", name="Pi", role="Standalone Pi",
                 model=model_name(live.get("model")), status=status, session=key,
                 started_at=started, updated_at=timestamp(live.get("updatedAt")),
                 detail="Activity reported by Pi Web." if live else "Standalone Pi transcript observed.")
    node.update(source="pi", active=bool(live), lastAction="Pi Web reported activity" if live else "Transcript observed")
    return node


def read_direct(root, parts, key, live, limits, missing, minimum_at=None):
    with native_file(root, *parts) as stream:
        header_line = stream.readline(4096)
    if not header_line.endswith(b"\n"):
        raise ValueError("Invalid Pi session header")
    header = read_json(header_line)
    if header.get("type") != "session" or header.get("id") != key:
        raise ValueError("Pi session ID mismatch")
    entries, clipped, uncertain = transcript(root, parts, key)
    if clipped:
        limits.add(f"Standalone Pi transcripts show at most the last {MAX_TRANSCRIPT_BYTES // 1024} KiB and {MAX_RECORDS} records.")
    if uncertain:
        missing.add("Incomplete standalone Pi transcript records leave some Open Agents activity unconfirmed.")
    node = parent_node(key, live, timestamp(header.get("timestamp")))
    active_since = timestamp(live.get("startedAt")) if live.get("status") == "running" else None
    def owns_call(at):
        return minimum_at is None or (at is not None and at >= minimum_at)

    children, links, events = open_agent_runs(node, entries, uncertain or active_since is None, limits, owns_call)
    node["model"] = model_name(live.get("model")) or node["model"]
    for child in children:
        child["task"] = None
        if child.get("issue"):
            child["issue"]["reason"] = "Open Agents returned a failed tool result."
        if child["active"]:
            started = timestamp(child["startedAt"])
            if started is None or started < active_since:
                child.update(active=False, status="unknown")
    for entry in events:
        entry.update(body=None, reason=None)
    if not live:
        node["updatedAt"] = max((child["updatedAt"] for child in children if child["updatedAt"]), default=None)
    return ([node, *children] if live or children else []), links, events


def bridge_ownership(nodes):
    sessions = defaultdict(list)
    for node in nodes:
        if node["kind"] == "pi" and node.get("session"):
            sessions[node["session"]].append(node)
    active = {key for key, jobs in sessions.items() if any(job.get("active") for job in jobs)}
    ended = {}
    for key, jobs in sessions.items():
        dates = [timestamp(job.get("updatedAt")) for job in jobs if job["status"] in PI_TERMINAL and not job.get("active")]
        if len(dates) == len(jobs) and all(date is not None for date in dates):
            ended[key] = max(dates)
    return set(sessions), active, ended


def collect_pi_direct(pi_sessions, bridge_sessions, live_sessions=None, active_bridge=None, ended_bridge=None):
    """Collect standalone Pi sessions and exact Open Agents tool-call relationships."""
    deadline = time.monotonic() + READ_SECONDS
    nodes, links, events = [], [], []
    missing, limits = set(), set()
    if active_bridge is None:
        active_bridge = bridge_sessions
    live = {}
    if len(record(live_sessions)) > MAX_DIRECT_LIVE:
        limits.add(f"At most {MAX_DIRECT_LIVE} independently verified standalone Pi sessions.")
    for key, value in islice(record(live_sessions).items(), MAX_DIRECT_LIVE):
        if label(key) and key not in active_bridge and record(value).get("status") in ("running", "queued"):
            live[key] = value
    resumed = {}
    for key, bound in (ended_bridge or {}).items():
        state = live.get(key, {})
        start = timestamp(state.get("startedAt"))
        if state.get("status") == "running" and start is not None and start > bound:
            resumed[key] = start
    store_available = True
    try:
        candidates = discover(pi_sessions, limits, missing, deadline)
    except (OSError, ValueError):
        store_available = False
        candidates = []
        missing.add("Standalone Pi sessions directory is unavailable.")

    counts = Counter(key for _, key, _ in candidates if key not in bridge_sessions or key in resumed)
    ambiguous = {key for key, count in counts.items() if count > 1}
    if ambiguous:
        missing.add("Ambiguous standalone Pi session IDs were omitted.")
    ranked = [item for item in candidates if counts[item[1]] == 1]
    ranked.sort(key=lambda item: (item[1] in live, item[0], item[1]), reverse=True)
    if len(ranked) > MAX_DIRECT_TRANSCRIPTS:
        limits.add(f"At most {MAX_DIRECT_TRANSCRIPTS} recent standalone Pi transcripts, with verified live sessions first.")

    unreadable, parents, children = 0, 0, 0
    for _, key, parts in ranked[:MAX_DIRECT_TRANSCRIPTS]:
        if time.monotonic() >= deadline:
            limits.add(f"The {READ_SECONDS:g}s read deadline curtailed standalone Pi transcript reads.")
            break
        try:
            observed, child_links, child_events = read_direct(pi_sessions, parts, key, live.get(key, {}), limits, missing,
                                                             minimum_at=resumed.get(key))
        except (OSError, ValueError, RecursionError):
            unreadable += 1
            continue
        if observed:
            parents += 1
            children += len(observed) - 1
            nodes.extend(observed)
            links.extend(child_links)
            events.extend(child_events)
            live.pop(key, None)
    for key, value in live.items():
        if key not in ambiguous:
            nodes.append(parent_node(key, value, None))
            parents += 1
    if unreadable:
        missing.add(f"{unreadable} standalone Pi transcripts could not be verified and read.")
    if live_sessions is None:
        missing.add("Independent live activity evidence for standalone Pi sessions is unavailable.")
    status = "partial" if missing or limits else "available"
    if not nodes and not store_available:
        status = "unavailable"
    info = source("pi-direct", "Standalone Pi and Open Agents", status,
                  f"{parents} standalone Pi sessions and {children} observed Open Agents calls.")
    info.update(missing=sorted(missing), limits=sorted(limits),
                impact="Standalone Pi activity or Open Agents history may be incomplete." if status != "available" else None)
    return nodes, links, events, info
