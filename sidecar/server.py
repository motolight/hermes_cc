#!/usr/bin/env python3
"""Local Hermes WebUI sidecar. Run with python3 -B sidecar/server.py."""

import hmac
import json
import os
import sqlite3
import subprocess
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from http import HTTPStatus
from http.client import HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from history import BackgroundCollector, HistoryStore, RANGES
from kanban import collect_kanban, read_kanban_task
from pi_direct import bridge_ownership, collect_pi_direct
from pi_integration import collect_pi_bridge
from pi_roles import read_pi_roles
from pi_web_activity import PiWebActivity
from topology import classify, pi_topology
from telemetry import (
    DB_TIME_LIMIT, MetricsOrigin, bounded_read, collect_gpu, collect_hermes, collect_metrics,
    collect_pi, collect_systemd, parse_metrics_origin, source, utc,
)

HOST = "127.0.0.1"
PORT = 17882
SNAPSHOT_TTL = 5.0
BACKGROUND_INTERVAL = 30.0
HISTORY_SAMPLES = 120
MAX_THREADS = 12
MAX_OUTPUT_AGENTS = 480
MAX_OUTPUT_EVENTS = 250
MAX_OUTPUT_USAGE = 800
# WebUI limits proxied response bodies to 512 KiB.
MAX_SNAPSHOT_BYTES = 500 * 1024
AUTH_HEADER = "X-Hermes-Sidecar-Token"


@dataclass(frozen=True)
class Config:
    token_file: Path
    hermes_home: Path
    webui_state: Path
    vllm_origin: MetricsOrigin | None
    systemd_scope: str
    gpu_ssh_user: str | None = None
    history_dir: Path | None = None
    pi_bridge_home: Path | None = None
    pi_sessions: Path | None = None
    pi_web_socket: Path | None = None

    @classmethod
    def from_environment(cls):
        home = Path.home() / ".hermes"
        hermes_home = Path(os.getenv("HCC_HERMES_HOME") or home).expanduser()
        webui_state = Path(os.getenv("HERMES_WEBUI_STATE_DIR") or home / "webui").expanduser()
        token = Path(os.getenv("HERMES_EXT_SIDECAR_TOKEN_FILE")
                     or webui_state / "sidecar-auth" / "hermes-command-center.token").expanduser()
        origin = os.getenv("HCC_VLLM_ORIGIN")
        if origin is None:
            origin = "http://127.0.0.1:" + os.getenv("HCC_VLLM_PORT", "8000")
        scope = os.getenv("HCC_SYSTEMD_SCOPE", "both")
        return cls(token, hermes_home, webui_state, parse_metrics_origin(origin),
                   scope if scope in {"user", "system", "both"} else "both", os.getenv("HCC_GPU_SSH_USER"),
                   webui_state / ".hermes-command-center" / "telemetry",
                   Path(os.getenv("PI_BRIDGE_HOME") or Path.home() / ".local/state/pi-bridge").expanduser(),
                   Path(os.getenv("PI_CODING_AGENT_DIR") or Path.home() / ".pi/agent").expanduser() / "sessions",
                   Path.home() / ".pi-web/sessiond.sock")


def collected(function, fallback):
    try:
        return function()
    except (OSError, ValueError, HTTPException, sqlite3.Error, subprocess.SubprocessError):
        return fallback


def json_body(snapshot):
    return json.dumps(snapshot, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


class SnapshotTooLarge(ValueError):
    """Even the fixed telemetry envelope exceeds the proxy budget."""


def encode_snapshot(snapshot):
    body = json_body(snapshot)
    if len(body) <= MAX_SNAPSHOT_BYTES:
        return body

    pi_ids = {node["id"] for node in (*snapshot["agents"], *snapshot.get("runs", ()))
              if node.get("source") == "pi" or node.get("kind") == "pi"}
    pi_events = [event for event in snapshot["events"] if event["from"] in pi_ids or event["to"] in pi_ids]
    hermes_events = [event for event in snapshot["events"] if event["from"] not in pi_ids and event["to"] not in pi_ids]
    pi_nodes = [node for node in snapshot["agents"] if node["id"] in pi_ids]
    profiles = [node for node in snapshot["agents"] if node.get("kind") == "profile"]
    hermes_nodes = [node for node in snapshot["agents"] if node["id"] not in pi_ids and node.get("kind") != "profile"]

    def limited_body(share):
        affected = set()

        def retain(records, source_id, *, newest_last=False):
            count = len(records) * share // 1024
            if count < len(records):
                affected.add(source_id)
            return records[len(records) - count:] if newest_last else records[:count]

        # Give each source a share of the byte budget. Keep live gauges,
        # services and source states in every response.
        agents = profiles + retain(pi_nodes, "pi") + retain(hermes_nodes, "hermes")
        ids = {node["id"] for node in agents}
        topology_ids = ids | {node["id"] for node in snapshot.get("piAgents", ())}
        events = sorted(retain(pi_events, "pi") + retain(hermes_events, "hermes"),
                        key=lambda event: event["at"] or "", reverse=True)
        clipped = {
            **snapshot,
            "agents": agents,
            "runs": retain(snapshot.get("runs", []), "hermes"),
            "issues": retain(snapshot.get("issues", []), "hermes"),
            "events": events,
            "links": [edge for edge in snapshot["links"] if edge["from"] in ids and edge["to"] in ids],
            "piLinks": [edge for edge in snapshot.get("piLinks", ())
                        if edge["from"] in topology_ids and edge["to"] in topology_ids],
            "tasks": retain(snapshot["tasks"], "kanban"),
            "usage": retain(snapshot["usage"], "hermes"),
            "gpus": [{**gpu, "history": retain(gpu.get("history", []), "gpu", newest_last=True)} for gpu in snapshot["gpus"]],
        }
        clipped["sources"] = [
            {**entry, "status": "partial", "detail": entry["detail"] + " Older records were limited by the proxy size budget."}
            if entry["id"] in affected and entry["status"] != "unavailable" else entry
            for entry in snapshot["sources"]
        ]
        return json_body(clipped)

    # Retained records grow with the share. Keep the largest fitting
    # candidate, with a metadata-only envelope as the final fallback.
    best = limited_body(0)
    if len(best) > MAX_SNAPSHOT_BYTES:
        raise SnapshotTooLarge
    low, high = 1, 1023
    while low <= high:
        share = (low + high) // 2
        attempt = limited_body(share)
        if len(attempt) <= MAX_SNAPSHOT_BYTES:
            best, low = attempt, share + 1
        else:
            high = share - 1
    return best


class Snapshot:
    def __init__(self, config):
        self.config = config
        self.pool = ThreadPoolExecutor(6, thread_name_prefix="hcc-collector")
        self.refresh_lock = threading.Lock()
        self.expires = 0.0
        self.body = b""
        self.gpu_history = {}
        self.counters = {}
        self.source_success = {}
        self.history = None
        self.history_error = False
        self.pi_web = PiWebActivity(config.pi_web_socket) if config.pi_web_socket is not None else None
        if config.history_dir is not None:
            try:
                self.history = HistoryStore(config.history_dir)
            except (OSError, ValueError, sqlite3.Error):
                self.history_error = True

    def close(self):
        if self.pi_web is not None:
            self.pi_web.close()
        self.pool.shutdown(wait=True)

    def load(self):
        # A single refresh populates the cache for simultaneous requests.
        with self.refresh_lock:
            if time.monotonic() >= self.expires:
                snapshot = self.refresh()
                if self.history is not None:
                    try:
                        self.history.record(time.time(), snapshot["usage"], snapshot["gpus"], snapshot["metrics"])
                        self.history_error = False
                    except (OSError, ValueError, sqlite3.Error):
                        self.history_error = True
                if self.config.history_dir is not None:
                    status = "unavailable" if self.history_error or self.history is None else "available"
                    snapshot["sources"].append({
                        **source("history", "HCC telemetry history", status,
                                 "Persistent collection is active." if status == "available" else "The private telemetry store could not be written."),
                        "lastSuccessAt": snapshot["at"] if status == "available" else self.source_success.get("history"),
                        "missing": [] if status == "available" else ["New observed deltas and historical engine/GPU samples"],
                        "limits": ["31 days", "64 MiB"],
                        "recommendation": None if status == "available" else "Inspect the HCC telemetry directory and the sidecar write exception.",
                    })
                    if status == "available":
                        self.source_success["history"] = snapshot["at"]
                snapshot["usage"] = [
                    {key: value for key, value in row.items() if key not in {"counterKey", "counterCreatedAt"}}
                    for row in snapshot["usage"] if any(row.get(key) for key in ("input", "output", "cached", "reasoning", "requests"))
                ]
                self.body = encode_snapshot(snapshot)
                self.expires = time.monotonic() + SNAPSHOT_TTL
            return self.body

    def with_gpu_history(self, gpus, at):
        if not gpus:
            return
        # Keep history only for devices still enumerated by the driver.
        current = {}
        for gpu in gpus:
            history = self.gpu_history.get(gpu["id"], deque(maxlen=HISTORY_SAMPLES))
            history.append({"at": at, **{key: gpu[key] for key in ("utilization", "memoryUsed", "temperature", "power")}})
            current[gpu["id"]] = history
            gpu["history"] = list(history)
        self.gpu_history = current

    def metric_counter_delta(self, kind, current, now):
        previous_sample = self.counters.pop(kind, None)
        if not current:
            return None
        self.counters[kind] = (current, now)
        if previous_sample is None:
            return None
        previous, at = previous_sample
        elapsed = now - at
        if (0 < elapsed <= BACKGROUND_INTERVAL * 2 and previous.keys() == current.keys()
                and all(current[key] >= previous[key] for key in current)):
            return sum(current[key] - previous[key] for key in current), elapsed
        return None

    def observed_metrics(self, metrics):
        data = {key: None for key in ("running", "waiting", "kvCache", "throughput", "ttftMs")}
        if metrics is None:
            self.counters.clear()
            return data, {**source("vllm", "vLLM", "unavailable", "The configured Prometheus endpoint could not be read."),
                          "missing": list(data),
                          "recommendation": "Verify the configured vLLM origin and that its read-only /metrics endpoint is reachable."}
        now = time.monotonic()
        generation = self.metric_counter_delta("generation", metrics.get("generation"), now)
        if generation:
            data["throughput"] = round(generation[0] / generation[1], 2)
        ttft_sum = metrics.get("ttftSum", {})
        ttft_count = metrics.get("ttftCount", {})
        paired = ttft_sum.keys() == ttft_count.keys()
        duration = self.metric_counter_delta("ttftSum", ttft_sum if paired else None, now)
        requests = self.metric_counter_delta("ttftCount", ttft_count if paired else None, now)
        if duration and requests and requests[0] > 0 and duration[1] == requests[1]:
            data["ttftMs"] = round(1000 * duration[0] / requests[0], 2)
        for key in ("running", "waiting", "kvCache"):
            data[key] = metrics.get(key)
        if data["kvCache"] is not None:
            data["kvCache"] *= 100
        present = sum(value is not None for value in data.values())
        recognized = present or self.counters
        state = "available" if present == len(data) else "partial" if recognized else "unavailable"
        detail = ("Throughput and first-token latency summarize activity between consecutive metric scrapes."
                  if recognized else "No recognized metrics were reported.")
        info = source("vllm", "vLLM", state, detail)
        info.update(missing=[key for key, value in data.items() if value is None],
                    limits=["Counter resets or a missing interval leave throughput unavailable.",
                            "For TTFT an interval needs at least one newly observed histogram sample.",
                            "Prometheus exposes engine aggregates without request or session identifiers."],
                    impact="Throughput and TTFT describe an engine interval. Per-active-request attribution is unavailable.")
        return data, info

    def refresh(self):
        config = self.config
        hermes = self.pool.submit(
            collected, partial(collect_hermes, config.hermes_home),
            ([], [], [], [], source("hermes", "Hermes profile state", "unavailable", "Profile state could not be read.")),
        )
        kanban = self.pool.submit(
            collected, partial(collect_kanban, config.hermes_home, time.monotonic() + DB_TIME_LIMIT),
            ([], source("kanban", "Hermes Kanban", "unavailable", "Kanban state could not be read.")),
        )
        gpu_label = "NVIDIA GPUs on vLLM host" if config.gpu_ssh_user else "NVIDIA GPUs"
        gpu = self.pool.submit(
            collected, partial(collect_gpu, config.vllm_origin, config.gpu_ssh_user),
            ([], source("gpu", gpu_label, "unavailable", "GPU telemetry could not be read.")),
        )
        systemd = self.pool.submit(
            collected, partial(collect_systemd, config.systemd_scope, config.hermes_home),
            ([], source("systemd", "Local services", "unavailable", "Systemd status could not be read.")),
        )
        pi_unavailable = source("pi", "Pi Bridge and Open Agents", "unavailable", "Pi source files could not be read.")
        pi = None if config.pi_bridge_home is not None and config.pi_sessions is not None else self.pool.submit(
            collected, partial(collect_pi, config.webui_state), ([], [], [], pi_unavailable))
        vllm = self.pool.submit(collected, partial(collect_metrics, config.vllm_origin), None) if config.vllm_origin else None
        agents, links, events, usage, hermes_source = hermes.result()
        if pi is None:
            native_pi = self.pool.submit(
                collected, partial(collect_pi_bridge, config.webui_state, config.pi_bridge_home, config.pi_sessions, agents),
                ([], [], [], [], pi_unavailable),
            )
            roles = self.pool.submit(collected, partial(read_pi_roles, config.pi_sessions.parent),
                                     ([], source("pi-roles", "Pi Open Agents roles", "unavailable", "Role inventory unavailable.")))
        tasks, kanban_source = kanban.result()
        gpus, gpu_source = gpu.result()
        services, systemd_source = systemd.result()
        metrics, vllm_source = self.observed_metrics(vllm.result() if vllm else None)
        pi_links, pi_roles, pi_diagnostics, pi_web_source = [], [], [], {}
        if pi is None:
            pi_agents, pi_links, pi_events, pi_services, pi_source = native_pi.result()
            pi_roles, roles_source = roles.result()
            pi_diagnostics.append(roles_source)
            live_sessions = None
            if self.pi_web is not None:
                live_sessions, pi_web_source = self.pi_web.snapshot()
                pi_diagnostics.append(pi_web_source)
                if not pi_web_source.get("runtimeAvailable"):
                    live_sessions = None
            bridge_sessions, active_bridge, ended_bridge = bridge_ownership(pi_agents)
            direct_nodes, direct_links, direct_events, direct_source = collect_pi_direct(
                config.pi_sessions, bridge_sessions, live_sessions, active_bridge, ended_bridge)
            pi_agents.extend(direct_nodes)
            pi_links.extend(direct_links)
            pi_events.extend(direct_events)
            pi_diagnostics.append(direct_source)
        else:
            pi_agents, pi_events, pi_services, pi_source = pi.result()
        links.extend(pi_links)
        epoch = time.time()
        now = utc(epoch)
        self.with_gpu_history(gpus, now)
        # Keep most recent Hermes records bounded across all profiles. Pi nodes
        # are already bounded and form their own independent source.
        agents.sort(key=lambda entry: entry["updatedAt"] or "", reverse=True)
        events.sort(key=lambda entry: entry["at"] or "", reverse=True)
        usage.sort(key=lambda entry: entry["at"] or "", reverse=True)
        if len(agents) > MAX_OUTPUT_AGENTS or len(events) > MAX_OUTPUT_EVENTS or len(usage) > MAX_OUTPUT_USAGE:
            hermes_source["status"] = "partial"
            hermes_source["detail"] = "Recent records shown. Some profile data is unavailable or exceeds collection limits."
        events = pi_events + events
        communications = sorted([item for item in events if item.get("channel") == "communication"], key=lambda item: item["at"] or "", reverse=True)
        technical = sorted([item for item in events if item.get("channel") != "communication"], key=lambda item: item["at"] or "", reverse=True)
        gateways = {service["gatewayProfile"] for service in services
                    if service.get("gatewayProfile") and service["status"] == "active"}
        hermes_nodes = agents
        agents, runs, issues, links = classify(pi_agents + hermes_nodes, links, communications + technical, tasks, epoch, gateways)
        pi_inventory, topology_links, recent_pi, recent_delegation = pi_topology(
            pi_agents, pi_links, hermes_nodes, agents, services, pi_roles, pi_web_source)
        # Usage retains its session ID for accounting when its graph node is
        # outside the visible agent/event window.
        sources = [hermes_source, kanban_source, pi_source, *pi_diagnostics, gpu_source, vllm_source, systemd_source]
        for entry in sources:
            if entry["status"] in {"available", "partial"}:
                self.source_success[entry["id"]] = now
            entry.setdefault("lastSuccessAt", self.source_success.get(entry["id"]))
            if entry["status"] != "available":
                if entry["id"] == "gpu":
                    entry.setdefault("missing", ["Some or all per-device utilization, VRAM, temperature and power"])
                    entry.setdefault("recommendation", "Verify nvidia-smi and, for a remote host, the existing SSH key and known_hosts.")
                elif entry["id"] == "systemd":
                    entry.setdefault("missing", ["System or user service state for unreported units"])
                    entry.setdefault("recommendation", "Verify the sidecar user can read service state through systemctl.")
                elif entry["id"] == "kanban":
                    entry.setdefault("missing", ["Some boards, cards or run records from native SQLite"])
                    entry.setdefault("limits", ["Board and per-snapshot task caps apply; each rich detail has its own section limits."])
                    entry.setdefault("recommendation", "Check the native board store if expected tasks are missing.")
                entry.setdefault("impact", "Sections that rely on this source may be incomplete.")
        return {
            "at": now, "agents": agents,
            "runs": sorted(runs, key=lambda node: node.get("updatedAt") or "", reverse=True)[:MAX_OUTPUT_AGENTS], "issues": issues[:40],
            "links": links, "events": (communications + technical)[:MAX_OUTPUT_EVENTS],
            "piAgents": pi_inventory, "piLinks": topology_links, "recentPi": recent_pi, "recentPiDelegation": recent_delegation,
            "tasks": tasks, "usage": usage[:MAX_OUTPUT_USAGE], "gpus": gpus,
            "services": services + pi_services, "metrics": metrics,
            "sources": sources,
        }


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = MAX_THREADS

    def __init__(self, address, config):
        self.config = config
        self.snapshot = Snapshot(config)
        self.background = None
        self.slots = threading.BoundedSemaphore(MAX_THREADS)
        super().__init__(address, Handler)
        if self.snapshot.pi_web is not None:
            self.snapshot.pi_web.start()
        if config.history_dir is not None:
            self.background = BackgroundCollector(self.snapshot.load, interval=BACKGROUND_INTERVAL)
            self.background.start()

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(2.0)
        return request, address

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()

    def handle_error(self, request, address):
        # Request lines and stack traces may contain confidential local data.
        pass

    def server_close(self):
        super().server_close()
        if self.background is not None:
            self.background.close()
        self.snapshot.close()


class Handler(BaseHTTPRequestHandler):
    server_version = "HermesCommandCenter"
    sys_version = ""

    def log_message(self, _format, *_args):
        pass

    def reply(self, status, body, *, allow_get=False):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if allow_get:
            self.send_header("Allow", "GET")
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
        self.close_connection = True

    def send_error(self, code, message=None, explain=None):
        # The stdlib default reflects malformed request values as HTML.
        self.reply(code, b'{"error":"invalid_request"}')

    def authorization(self):
        try:
            token = bounded_read(self.server.config.token_file, 512).strip()
            if not token or any(byte < 33 or byte > 126 for byte in token):
                return HTTPStatus.SERVICE_UNAVAILABLE
        except (OSError, ValueError):
            return HTTPStatus.SERVICE_UNAVAILABLE
        headers = self.headers.get_all(AUTH_HEADER, [])
        if len(headers) != 1 or len(headers[0]) > 512:
            return HTTPStatus.UNAUTHORIZED
        if not hmac.compare_digest(token, headers[0].encode("latin-1")):
            return HTTPStatus.UNAUTHORIZED
        return HTTPStatus.OK

    def do_GET(self):
        if self.path == "/health":
            self.reply(HTTPStatus.OK, json_body({"status": "ok", "runtime": {"sidecar": "hermes-command-center"},
                                                "pid": os.getpid()}))
        elif not (self.path == "/snapshot" or self.path.startswith(("/history/", "/kanban/task?"))):
            self.reply(HTTPStatus.NOT_FOUND, b'{"error":"not_found"}')
        else:
            auth = self.authorization()
            if auth == HTTPStatus.SERVICE_UNAVAILABLE:
                self.reply(auth, b'{"error":"authentication_unavailable"}')
            elif auth == HTTPStatus.UNAUTHORIZED:
                self.reply(auth, b'{"error":"unauthorized"}')
            else:
                try:
                    status, body = self.read_data()
                except SnapshotTooLarge:
                    self.reply(HTTPStatus.SERVICE_UNAVAILABLE, b'{"error":"snapshot_exceeds_size_budget"}')
                except (OSError, ValueError, sqlite3.Error):
                    self.reply(HTTPStatus.SERVICE_UNAVAILABLE, b'{"error":"source_unavailable"}')
                else:
                    self.reply(status, body)

    def read_data(self):
        if self.path == "/snapshot":
            return HTTPStatus.OK, self.server.snapshot.load()
        if self.path.startswith("/history/"):
            selected = self.path.removeprefix("/history/")
            if selected not in RANGES:
                return HTTPStatus.NOT_FOUND, b'{"error":"not_found"}'
            history = self.server.snapshot.history
            if history is None:
                return HTTPStatus.SERVICE_UNAVAILABLE, b'{"error":"history_unavailable"}'
            result = history.read(selected)
        else:
            if len(self.path) > 512:
                return HTTPStatus.BAD_REQUEST, b'{"error":"invalid_task_id"}'
            try:
                query = parse_qs(urlsplit(self.path).query, strict_parsing=True, max_num_fields=1, errors="strict")
                (key,) = query["id"]
            except (KeyError, ValueError):
                return HTTPStatus.BAD_REQUEST, b'{"error":"invalid_task_id"}'
            result = read_kanban_task(self.server.config.hermes_home, key, time.monotonic() + DB_TIME_LIMIT)
            if result is None:
                return HTTPStatus.NOT_FOUND, b'{"error":"not_found"}'
        body = json_body(result)
        if len(body) > MAX_SNAPSHOT_BYTES:
            raise SnapshotTooLarge
        return HTTPStatus.OK, body

    def refuse_method(self):
        self.reply(HTTPStatus.METHOD_NOT_ALLOWED, b'{"error":"method_not_allowed"}', allow_get=True)

    do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_CONNECT = do_TRACE = refuse_method


def main():
    with Server((HOST, PORT), Config.from_environment()) as server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
