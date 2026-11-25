"""Build the live inventory from verified handles and retain past runs separately."""

from collections import defaultdict

from telemetry import timestamp


LIVE_STATES = frozenset({"active", "running", "working", "generating", "tool", "in_progress", "queued", "stalled"})
ISSUE_STATES = frozenset({"stalled", "blocked", "error", "failed", "interrupted"})
ISSUE_WINDOW_SECONDS = 6 * 3600
HISTORICAL_KINDS = frozenset({"session", "worker", "executor", "delegation", "subagent", "pi"})
PI_TERMINAL = frozenset({"completed", "failed", "cancelled", "interrupted", "ended"})
PI_RUNTIME_ID = "pi:runtime"
PI_ROUTE_FIELDS = ("id", "kind", "name", "role", "status", "profile", "model", "task", "project", "team", "session",
                   "startedAt", "updatedAt", "detail", "lastAction", "href", "source", "jobId")


def profile_node(name):
    return {
        "id": "profile:" + name, "kind": "profile", "name": name, "role": "Hermes",
        "profile": name, "active": False, "status": "unknown", "model": None,
        "task": None, "project": None, "team": None, "session": None,
        "startedAt": None, "updatedAt": None, "detail": None, "href": None,
    }


def classify(nodes, edges, events, tasks, epoch, gateway_profiles=()):
    gateway_profiles = sorted(set(gateway_profiles))
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
    for name in gateway_profiles:
        profile = profiles.setdefault("profile:" + name, profile_node(name))
        profile.update(status="ready", detail="The Hermes Gateway service is active.")
    retained = {"profile:default", *("profile:" + name for name in gateway_profiles),
                *(node["profileId"] for node in live_nodes.values())}
    profiles = {key: profile for key, profile in profiles.items() if key in retained}
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
            if member.get("profileId") in profiles and edge.get("kind") in {"delegation", "execution"}:
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
            if (member and endpoint not in active and member.get("kind") in {"session", "worker"}
                    and member.get("profileId") in profiles):
                event["origin" + key.capitalize()] = endpoint
                event[key] = member["profileId"]
    past = [node for node in members.values() if not node["active"] and node.get("kind") in HISTORICAL_KINDS]
    return [*profiles.values(), *live_nodes.values()], past, sorted(issues, key=lambda issue: issue.get("updatedAt") or "", reverse=True), list(relationships.values())


def pi_route(at, root, children, origins):
    shown = sorted(children, key=lambda node: timestamp(node.get("updatedAt")) or 0, reverse=True)[:8]
    participants = [root, *shown]
    handoffs = [{"from": root["id"], "to": node["id"], "kind": "historical"} for node in shown]
    if len(origins) == 1:
        origin = origins[0]
        participants.insert(0, {**origin, "status": "observed", "detail": "Hermes source session verified by the recorded delegation."})
        handoffs.insert(0, {"from": origin["id"], "to": root["id"], "kind": "historical"})
    return {"at": at, "links": handoffs, "nodes": [
        {**{key: node[key] for key in PI_ROUTE_FIELDS if key in node}, "active": False} for node in participants
    ]}


def pi_topology(pi_nodes, pi_edges, hermes_nodes, agents, services, roles, runtime_source):
    """Keep installed roles, current executors and the last evidenced run distinct."""
    pi_runs = {node["id"]: node for node in pi_nodes if node["kind"] == "pi"}
    pi_services = {"pi-web", "pi-web-sessiond", "pi-bridge"}
    if not (pi_runs or roles or runtime_source.get("runtimeAvailable") or any(service["id"] in pi_services for service in services)):
        return [], [], None, None

    ready = any(service["id"] in {"pi-web-sessiond", "pi-bridge"} and service["status"] == "active" for service in services)
    available = ready or bool(runtime_source.get("runtimeAvailable"))
    local_run = any(node.get("active") and node["status"] == "running" for node in pi_runs.values())
    if local_run:
        state, detail = "working", "A local Pi execution is in progress."
    elif runtime_source.get("working") is True:
        state, detail = "working", "Pi Web reported current workspace activity."
    elif available:
        state, detail = "ready", "A local Pi runtime service was observed."
    else:
        state, detail = "unknown", "Pi is configured or has recorded runs. Runtime readiness is unconfirmed."
    runtime = {"id": PI_RUNTIME_ID, "kind": "pi-runtime", "name": "Pi", "role": "Pi runtime", "active": False,
               "status": state, "detail": detail}
    profiles = {node["profile"]: node for node in agents if node["kind"] == "profile"}
    origins = {node["id"]: node for node in hermes_nodes if node.get("profile")}
    origins_by_run = defaultdict(list)
    for edge in pi_edges:
        if edge["kind"] == "delegation" and edge["from"] in origins and edge["to"] in pi_runs:
            origins_by_run[edge["to"]].append(origins[edge["from"]])

    integrations = sorted({origin["profile"] for matches in origins_by_run.values() for origin in matches
                           if origin["profile"] in profiles})
    links = [{"from": profiles[name]["id"], "to": PI_RUNTIME_ID, "kind": "integration"} for name in integrations]
    links.extend({"from": PI_RUNTIME_ID, "to": role["id"], "kind": "configured"} for role in roles)
    links.extend({"from": PI_RUNTIME_ID, "to": node["id"], "kind": "execution"}
                 for node in pi_runs.values() if node.get("active"))

    children = {node["id"]: node for node in pi_nodes if node["kind"] == "subagent" and not node.get("active")}
    children_by_run = defaultdict(dict)
    for edge in pi_edges:
        if edge["kind"] == "delegation" and edge["from"] in pi_runs and edge["to"] in children:
            children_by_run[edge["from"]][edge["to"]] = children[edge["to"]]
    observed = []
    for node in pi_runs.values():
        children = children_by_run[node["id"]].values()
        if node.get("active") or (node["status"] not in PI_TERMINAL and not children):
            continue
        stamps = [node.get("updatedAt"), *(child.get("updatedAt") for child in children)]
        at = max((stamp for stamp in stamps if timestamp(stamp) is not None), key=timestamp, default=None)
        if at:
            observed.append((at, node, children))
    if not observed:
        return [runtime, *roles], links, None, None
    observed.sort(key=lambda item: timestamp(item[0]), reverse=True)
    at, root, children = observed[0]
    latest = pi_route(at, root, children, origins_by_run[root["id"]])
    delegation = None
    if not children:
        for at, run, run_children in observed[1:]:
            if run_children:
                delegation = pi_route(at, run, run_children, origins_by_run[run["id"]])
                break
    return [runtime, *roles], links, latest, delegation
