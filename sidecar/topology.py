"""Build the live inventory from verified handles and retain past runs separately."""

from collections import defaultdict

from telemetry import timestamp


LIVE_STATES = frozenset({"active", "running", "working", "generating", "tool", "in_progress", "queued", "stalled"})
ISSUE_STATES = frozenset({"stalled", "blocked", "error", "failed", "interrupted"})
ISSUE_WINDOW_SECONDS = 6 * 3600
HISTORICAL_KINDS = frozenset({"session", "worker", "executor", "delegation", "subagent", "pi"})


def profile_node(name):
    return {
        "id": "profile:" + name, "kind": "profile", "name": name, "role": "Hermes",
        "profile": name, "active": False, "status": "unknown", "model": None,
        "task": None, "project": None, "team": None, "session": None,
        "startedAt": None, "updatedAt": None, "detail": None, "href": None,
    }


def classify(nodes, edges, events, tasks, epoch):
    members = {}
    profiles = {}
    issues = []
    current_tasks = defaultdict(list)
    for task in tasks:
        if task.get("assignee") and task.get("status") in {"running", "blocked", "review"}:
            current_tasks[task["assignee"]].append(task)
    for raw in nodes:
        node = {**raw}
        if node.get("kind") == "profile":
            profiles[node["id"]] = node
            continue
        profile = node.get("profile")
        if profile:
            profile_id = "profile:" + profile
            profiles.setdefault(profile_id, profile_node(profile))
        else:
            profile_id = None
        live = node.get("active", node.get("status") in LIVE_STATES) is True
        age = timestamp(node.get("updatedAt") or node.get("startedAt"))
        recent = age is not None and 0 <= epoch - age <= ISSUE_WINDOW_SECONDS
        if (live or recent) and node.get("status") in ISSUE_STATES:
            issue = node.get("issue") or {"reason": node.get("detail") or "The source reported this state.",
                                          "at": node.get("updatedAt"), "lastActivityAt": node.get("updatedAt")}
            node["issue"] = issue
            issues.append(node)
        node["active"] = live
        node["profileId"] = profile_id
        if live and node.get("kind") in {"session", "worker", "delegation"}:
            node["runKind"] = node["kind"]
            node["kind"] = "executor"
        if live and not node.get("task"):
            task = next((task for task in current_tasks[profile] if task.get("session") and task["session"] == node.get("session")), None)
            if task:
                node["task"] = task["title"]
        members[node["id"]] = node
    live_nodes = {key: node for key, node in members.items() if node["active"]}
    for node in live_nodes.values():
        if not node["profileId"]:
            continue
        profile = profiles[node["profileId"]]
        if profile.get("updatedAt") and (profile["updatedAt"] >= (node.get("updatedAt") or "")):
            continue
        profile.update(active=True, status="working" if node["status"] not in {"queued", "waiting"} else "waiting",
                       updatedAt=node.get("updatedAt"), model=node.get("model"), task=node.get("task"),
                       lastAction=node.get("lastAction"))
    active = {**profiles, **live_nodes}
    relationships = {}
    for edge in edges:
        a, b = edge.get("from"), edge.get("to")
        member = members.get(a)
        if b in active and a not in active and member and member.get("kind") in {"session", "worker"}:
            if member.get("profileId") and edge.get("kind") in {"delegation", "execution"}:
                edge = {**edge, "originFrom": a, "from": member["profileId"]}
                a = edge["from"]
        if a in active and b in active and a != b:
            relationships[(a, b, edge["kind"])] = edge
    child_ids = {b for (a, b, kind) in relationships if kind not in {"lineage", "message"}}
    for key, node in live_nodes.items():
        if node["profileId"] and key not in child_ids:
            a = node["profileId"]
            relationships[(a, key, "execution")] = {"from": a, "to": key, "kind": "execution"}
    # A running Pi execution has independent truth. An unknown Hermes origin is
    # deliberately left unconnected instead of attaching it to a default profile.
    for event in events:
        if event.get("channel") != "communication":
            continue
        for key in ("from", "to"):
            endpoint = event.get(key)
            member = members.get(endpoint)
            if member and endpoint not in active and member.get("kind") in {"session", "worker"} and member.get("profileId"):
                event["origin" + key.capitalize()] = endpoint
                event[key] = member["profileId"]
    past = [node for node in members.values() if not node["active"] and node.get("kind") in HISTORICAL_KINDS]
    return [*profiles.values(), *live_nodes.values()], past, sorted(issues, key=lambda issue: issue.get("updatedAt") or "", reverse=True), list(relationships.values())
