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

from kanban import collect_kanban
from telemetry import (
    DB_TIME_LIMIT, MetricsOrigin, bounded_read, collect_gpu, collect_hermes, collect_metrics,
    collect_pi, collect_systemd, parse_metrics_origin, source, utc,
)

HOST = "127.0.0.1"
PORT = 17882
SNAPSHOT_TTL = 5.0
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
                   scope if scope in {"user", "system", "both"} else "both", os.getenv("HCC_GPU_SSH_USER"))


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

    pi_ids = {node["id"] for node in snapshot["agents"] if node["role"] == "Pi Bridge"}
    pi_events = [event for event in snapshot["events"] if event["from"] in pi_ids or event["to"] in pi_ids]
    hermes_events = [event for event in snapshot["events"] if event["from"] not in pi_ids and event["to"] not in pi_ids]
    pi_nodes = [node for node in snapshot["agents"] if node["id"] in pi_ids]
    hermes_nodes = [node for node in snapshot["agents"] if node["id"] not in pi_ids]

    def limited_body(share):
        affected = set()

        def retain(records, source_id, *, newest_last=False):
            count = len(records) * share // 1024
            if count < len(records):
                affected.add(source_id)
            return records[len(records) - count:] if newest_last else records[:count]

        # Give each source a share of the byte budget. Keep live gauges,
        # services and source states in every response.
        agents = retain(pi_nodes, "pi") + retain(hermes_nodes, "hermes")
        ids = {node["id"] for node in agents}
        events = sorted(retain(pi_events, "pi") + retain(hermes_events, "hermes"),
                        key=lambda event: event["at"] or "", reverse=True)
        clipped = {
            **snapshot,
            "agents": agents,
            "events": events,
            "links": [edge for edge in snapshot["links"] if edge["from"] in ids and edge["to"] in ids],
            "tasks": retain(snapshot["tasks"], "kanban"),
            "usage": retain(snapshot["usage"], "hermes"),
            "gpus": [{**gpu, "history": retain(gpu["history"], "gpu", newest_last=True)} for gpu in snapshot["gpus"]],
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

    def close(self):
        self.pool.shutdown(wait=True)

    def load(self):
        # A single refresh populates the cache for simultaneous requests.
        with self.refresh_lock:
            if time.monotonic() >= self.expires:
                self.body = encode_snapshot(self.refresh())
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
        if (0 < elapsed <= SNAPSHOT_TTL * 6 and previous.keys() == current.keys()
                and all(current[key] >= previous[key] for key in current)):
            return sum(current[key] - previous[key] for key in current), elapsed
        return None

    def observed_metrics(self, metrics):
        data = {key: None for key in ("running", "waiting", "kvCache", "throughput", "ttftMs")}
        if metrics is None:
            self.counters.clear()
            return data, source("vllm", "vLLM", "unavailable", "Metrics endpoint is unavailable.")
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
        return data, source("vllm", "vLLM", state, detail)

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
            collected, partial(collect_systemd, config.systemd_scope),
            ([], source("systemd", "Local services", "unavailable", "Systemd status could not be read.")),
        )
        pi = self.pool.submit(
            collected, partial(collect_pi, config.webui_state),
            ([], [], [], source("pi", "Pi Bridge and Lamp", "unavailable", "Pi status could not be read.")),
        )
        vllm = self.pool.submit(collected, partial(collect_metrics, config.vllm_origin), None) if config.vllm_origin else None
        agents, links, events, usage, hermes_source = hermes.result()
        tasks, kanban_source = kanban.result()
        pi_agents, pi_events, pi_services, pi_source = pi.result()
        gpus, gpu_source = gpu.result()
        services, systemd_source = systemd.result()
        metrics, vllm_source = self.observed_metrics(vllm.result() if vllm else None)
        now = utc(time.time())
        self.with_gpu_history(gpus, now)
        # Keep most recent Hermes records bounded across all profiles. Pi nodes
        # are already bounded and form their own independent source.
        agents.sort(key=lambda entry: entry["updatedAt"] or "", reverse=True)
        events.sort(key=lambda entry: entry["at"] or "", reverse=True)
        usage.sort(key=lambda entry: entry["at"] or "", reverse=True)
        if len(agents) > MAX_OUTPUT_AGENTS or len(events) > MAX_OUTPUT_EVENTS or len(usage) > MAX_OUTPUT_USAGE:
            hermes_source["status"] = "partial"
            hermes_source["detail"] = "Recent records shown. Some profile data is unavailable or exceeds collection limits."
        agents = pi_agents + agents[:MAX_OUTPUT_AGENTS]
        ids = {entry["id"] for entry in agents}
        links = [edge for edge in links if edge["from"] in ids and edge["to"] in ids]
        events = sorted(pi_events + events[:MAX_OUTPUT_EVENTS], key=lambda entry: entry["at"] or "", reverse=True)
        # Usage retains its session ID for accounting when its graph node is
        # outside the visible agent/event window.
        return {
            "at": now, "agents": agents, "links": links, "events": events[:MAX_OUTPUT_EVENTS],
            "tasks": tasks, "usage": usage[:MAX_OUTPUT_USAGE], "gpus": gpus,
            "services": services + pi_services, "metrics": metrics,
            "sources": [hermes_source, kanban_source, pi_source, gpu_source, vllm_source, systemd_source],
        }


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = MAX_THREADS

    def __init__(self, address, config):
        self.config = config
        self.snapshot = Snapshot(config)
        self.slots = threading.BoundedSemaphore(MAX_THREADS)
        super().__init__(address, Handler)

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
        elif self.path != "/snapshot":
            self.reply(HTTPStatus.NOT_FOUND, b'{"error":"not_found"}')
        else:
            auth = self.authorization()
            if auth == HTTPStatus.SERVICE_UNAVAILABLE:
                self.reply(auth, b'{"error":"authentication_unavailable"}')
            elif auth == HTTPStatus.UNAUTHORIZED:
                self.reply(auth, b'{"error":"unauthorized"}')
            else:
                try:
                    body = self.server.snapshot.load()
                except SnapshotTooLarge:
                    self.reply(HTTPStatus.SERVICE_UNAVAILABLE, b'{"error":"snapshot_exceeds_size_budget"}')
                else:
                    self.reply(HTTPStatus.OK, body)

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
