"""Test the auth boundary and pinned collector behavior with local fixtures."""

import http.client
import json
import os
import sqlite3
from subprocess import CompletedProcess
import tempfile
import threading
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from server import AUTH_HEADER, MAX_SNAPSHOT_BYTES, Config, Server, Snapshot, SnapshotTooLarge, encode_snapshot, json_body
from telemetry import GPU_QUERY, MetricsOrigin, collect_gpu, collect_hermes, collect_metrics, collect_pi, model_name, parse_metrics, parse_metrics_origin


class SidecarTest(unittest.TestCase):
    def test_gpu_ssh_reuses_fixed_query_and_strict_trust(self):
        origin = parse_metrics_origin("http://10.4.6.12:8000")
        csv_output = "\n".join(f"{i}, NVIDIA RTX 3090, 18, 7500, 24576, 61, 67.8" for i in range(4))
        with patch("telemetry.subprocess.run", return_value=CompletedProcess([], 0, csv_output)) as run:
            devices, status = collect_gpu(origin, "hermes")
            argv = run.call_args.args[0]
            self.assertEqual(["-l", "hermes", "10.4.6.12", " ".join(GPU_QUERY)], argv[-4:])
            self.assertIn("StrictHostKeyChecking=yes", argv)
            self.assertIn("UpdateHostKeys=no", argv)
            self.assertIn("IdentityAgent=none", argv)
            self.assertEqual(["0", "1", "2", "3"], [gpu["id"] for gpu in devices])
            self.assertEqual("available", status["status"])
            with self.assertRaises(ValueError):
                collect_gpu(origin, "-oBadOption")
            with self.assertRaises(ValueError):
                collect_gpu(parse_metrics_origin("http://127.0.0.1:8000"), "hermes")
            run.assert_called_once()

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_http_auth_and_read_only_routes(self):
        token = self.root / "token"
        config = Config(token, self.root, self.root, None, "user")
        server = Server(("127.0.0.1", 0), config)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 3)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        def request(path, method="GET", header=None):
            with closing(http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)) as client:
                client.request(method, path, headers={AUTH_HEADER: header} if header else {})
                response = client.getresponse()
                return response.status, response.read().decode()

        with patch.object(server.snapshot, "load", return_value=b'{"agents":[]}') as load:
            status, body = request("/health")
            self.assertEqual(200, status)
            self.assertEqual({"status": "ok", "runtime": {"sidecar": "hermes-command-center"}, "pid": os.getpid()},
                             json.loads(body))
            self.assertEqual(503, request("/snapshot")[0])
            token.write_text("test-only-token\n")
            self.assertEqual(401, request("/snapshot")[0])
            self.assertEqual(401, request("/snapshot", header="incorrect")[0])
            self.assertEqual(404, request("/snapshot?path=secret", header="test-only-token")[0])
            self.assertEqual(405, request("/snapshot", method="POST", header="test-only-token")[0])
            load.assert_not_called()
            self.assertEqual((200, '{"agents":[]}'), request("/snapshot", header="test-only-token"))
            token.write_text("rotated-test-token")
            self.assertEqual(401, request("/snapshot", header="test-only-token")[0])
            self.assertEqual(200, request("/snapshot", header="rotated-test-token")[0])
            self.assertEqual(2, load.call_count)
            load.side_effect = SnapshotTooLarge
            self.assertEqual((503, '{"error":"snapshot_exceeds_size_budget"}'),
                             request("/snapshot", header="rotated-test-token"))

    def test_wal_cross_profile_metadata_and_usage_dedup(self):
        default = self.root / "state.db"
        profile_home = self.root / "profiles" / "other"
        profile_home.mkdir(parents=True)
        with closing(sqlite3.connect(default)) as db:
            self.make_fixture(db, "parent-run")
            db.commit()
        with closing(sqlite3.connect(profile_home / "state.db")) as db:
            self.make_fixture(db, "named-run")
            db.commit()
            # The default DB is now checkpointed; the named profile holds a
            # live WAL with a newer row. Both must be visible.
            db.execute("UPDATE sessions SET input_tokens = 43 WHERE id = 'named-run'")
            db.execute("UPDATE session_model_usage SET input_tokens = 43 WHERE session_id = 'named-run'")
            db.commit()
            before = default.read_bytes()
            agents, links, events, usage, health = collect_hermes(self.root)
            self.assertEqual("available", health["status"])
            self.assertEqual({"default", "other"}, {agent["profile"] for agent in agents})
            self.assertEqual({"running"}, {agent["status"] for agent in agents if agent["kind"] == "session"})
            self.assertTrue(all(agent["project"] == "safe-repo" for agent in agents))
            self.assertEqual(["delegation"] * 4 + ["execution"] * 2 + ["lineage"] * 2,
                             sorted(link["kind"] for link in links))
            self.assertEqual([42, 43], sorted(entry["input"] for entry in usage))
            self.assertEqual([None, None], [entry["ttftMs"] for entry in usage])
            self.assertEqual(4, len([event for event in events if event["kind"] == "message"]))
            self.assertEqual(["Tool result · local_test", "Tool result · local_test"],
                             [event["title"] for event in events if event["kind"] == "tool"])
            outgoing = [event for event in events if event["title"] == "Hermes message"]
            self.assertTrue(all(event["from"] and not event["to"] for event in outgoing))
            serialized = json.dumps([agents, links, events, usage])
            for omitted in ("private_config_value", "opaque_job_payload", "private-path", "sk-test", "private_message_text"):
                self.assertNotIn(omitted, serialized)
            self.assertEqual(before, default.read_bytes())
            self.assertFalse(default.with_name("state.db-wal").exists())
            self.assertFalse(default.with_name("state.db-shm").exists())

    def make_fixture(self, db, identity):
        db.executescript("""
            PRAGMA journal_mode = WAL;
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY, source TEXT, model TEXT, started_at REAL,
                git_repo_root TEXT, input_tokens INTEGER, output_tokens INTEGER,
                cache_read_tokens INTEGER, reasoning_tokens INTEGER, api_call_count INTEGER,
                model_config TEXT, parent_session_id TEXT
            );
            CREATE TABLE session_model_usage (
                session_id TEXT, model TEXT, task TEXT, last_seen REAL,
                billing_base_url TEXT, input_tokens INTEGER, output_tokens INTEGER,
                cache_read_tokens INTEGER, reasoning_tokens INTEGER, api_call_count INTEGER
            );
            CREATE TABLE runtime_epoch(singleton INTEGER, epoch INTEGER);
            INSERT INTO runtime_epoch VALUES (1, 9);
            CREATE TABLE session_admissions (target_session_id TEXT, status TEXT, owner_epoch INTEGER, payload_json TEXT);
            CREATE TABLE async_delegations (
                delegation_id TEXT, state TEXT, dispatched_at REAL, updated_at REAL,
                parent_session_id TEXT, task_json TEXT
            );
            CREATE TABLE resumable_subagents (
                subagent_id TEXT, state TEXT, created_at REAL, updated_at REAL, record_json TEXT
            );
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY, session_id TEXT, timestamp REAL, role TEXT, content TEXT, active INTEGER, tool_name TEXT
            );
            CREATE INDEX idx_messages_session ON messages(session_id, timestamp);
        """)
        db.execute(
            "INSERT INTO sessions VALUES (?, 'web', 'org/model', 1700000000, "
            "'/private-path/safe-repo', 42, 12, 3, 0, 1, 'private_config_value', NULL)", (identity,),
        )
        db.execute(
            "INSERT INTO sessions VALUES (?, 'delegate', 'org/model', 1700000001, "
            "'/private-path/safe-repo', 0, 0, 0, 0, 0, 'private_config_value', ?)",
            (identity + "-child", identity),
        )
        db.execute(
            "INSERT INTO session_model_usage VALUES (?, 'org/model', '', 1700000005, 'sk-test', 42, 12, 3, 0, 1)",
            (identity,),
        )
        db.execute("INSERT INTO session_admissions VALUES (?, 'started', 9, 'opaque_job_payload')", (identity,))
        db.execute("INSERT INTO async_delegations VALUES ('delegation-a', 'running', 1700000001, 1700000002, ?, 'opaque_job_payload')",
                   (identity,))
        db.execute(
            "INSERT INTO resumable_subagents VALUES ('subagent-a', 'running', 1700000001, 1700000002, ?)",
            (json.dumps({"session_id": identity + "-child", "unit_id": "delegation-a", "goal": "opaque_job_payload"}),),
        )
        db.executemany("INSERT INTO messages VALUES (?, ?, ?, ?, 'private_message_text', ?, ?)", [
            (1, identity, 1700000001, "user", 1, None),
            (2, identity, 1700000002, "assistant", 1, None),
            (3, identity, 1700000003, "assistant", 0, None),
            (4, identity, 1700000004, "tool", 1, "local_test"),
        ])

    def test_metrics_preserve_unknowns_and_counter_resets(self):
        metrics = parse_metrics(
            'vllm:num_requests_running{model_name="one"} 0\n'
            'vllm:num_requests_waiting{model_name="one"} 2\n'
            'vllm:kv_cache_usage_perc{engine="0"} 0.5\n'
            'vllm:generation_tokens_total{engine="0"} 100\n'
            'vllm:time_to_first_token_seconds_sum{engine="0"} 2.0\n'
            'vllm:time_to_first_token_seconds_count{engine="0"} 5\n'
            'unknown_metric{secrets="safe-unexposed-fixture"} 1\n'
        )
        self.assertEqual(0, metrics["running"])
        self.assertEqual(2, metrics["waiting"])
        self.assertEqual(0.5, metrics["kvCache"])
        config = Config(self.root, self.root, self.root, None, "user")
        snapshot = Snapshot(config)
        self.addCleanup(snapshot.close)
        with patch("server.time.monotonic", side_effect=[10, 15, 20]):
            first, _ = snapshot.observed_metrics(metrics)
            self.assertIsNone(first["throughput"])
            self.assertIsNone(first["ttftMs"])
            self.assertEqual(50, first["kvCache"])
            increment = {
                **metrics, "generation": {'{engine="0"}': 125},
                "ttftSum": {'{engine="0"}': 2.5}, "ttftCount": {'{engine="0"}': 7},
            }
            second, _ = snapshot.observed_metrics(increment)
            self.assertEqual(5, second["throughput"])
            self.assertEqual(250, second["ttftMs"])
            reset, _ = snapshot.observed_metrics(metrics)
            self.assertIsNone(reset["throughput"])
            self.assertIsNone(reset["ttftMs"])

    def test_metrics_origin_requires_private_or_loopback_literals(self):
        allowed = {
            "http://127.0.0.1:8000": MetricsOrigin("127.0.0.1", 8000),
            "http://192.168.50.10:8000": MetricsOrigin("192.168.50.10", 8000),
            "https://172.16.4.1:8443": MetricsOrigin("172.16.4.1", 8443, True),
            "https://[::1]:8443": MetricsOrigin("::1", 8443, True),
        }
        for value, origin in allowed.items():
            self.assertEqual(origin, parse_metrics_origin(value))
        for value in (
            "http://localhost:8000", "http://8.8.8.8:8000", "http://169.254.169.254:8000",
            "http://127.0.0.1", "http://127.0.0.1:0", "http://127.0.0.1:65536",
            "http://user@127.0.0.1:8000", "http://127.0.0.1:8000/", "http://127.0.0.1:8000?",
            "http://127.0.0.1:8000#", "file://127.0.0.1:8000", "http://[::ffff:127.0.0.1]:8000",
        ):
            self.assertIsNone(parse_metrics_origin(value), value)
        with patch("telemetry.http.client.HTTPConnection") as connect:
            connection = connect.return_value
            connection.getresponse.return_value.status = 302
            with self.assertRaises(ValueError):
                collect_metrics(allowed["http://192.168.50.10:8000"])
            connect.assert_called_once()
            self.assertEqual(("GET", "/metrics"), connection.request.call_args.args)
            connection.close.assert_called_once()

    def test_obvious_credential_prefixes_are_not_model_labels(self):
        for candidate in ("glpat-example", "proxy:glpat-example", "vendor/AIza-example", "vendor/sk-example"):
            self.assertIsNone(model_name(candidate))
        self.assertEqual("vendor/model:demo", model_name("vendor/model:demo"))

    def test_snapshot_includes_kanban_and_reuses_cached_body(self):
        config = Config(self.root, self.root, self.root, None, "user")
        snapshot = Snapshot(config)
        self.addCleanup(snapshot.close)
        tasks = [{"id": "task_a", "title": "Fixture task"}]
        kanban_status = {"id": "kanban", "label": "Hermes Kanban", "status": "available", "detail": "Test fixture"}
        with (
            patch("server.collect_kanban", return_value=(tasks, kanban_status)) as kanban,
            patch("server.collect_gpu", side_effect=FileNotFoundError),
            patch("server.collect_systemd", side_effect=FileNotFoundError),
        ):
            body = snapshot.load()
            self.assertEqual(body, snapshot.load())
            kanban.assert_called_once()
        data = json.loads(body)
        self.assertEqual(tasks, data["tasks"])
        self.assertIn(kanban_status, data["sources"])
        self.assertEqual([], data["gpus"])
        self.assertIsNone(data["metrics"]["throughput"])

    def test_snapshot_budget_preserves_latest_telemetry_and_marks_clipped_sources(self):
        at = "2026-10-01T01:00:00Z"
        agents = [
            {"id": f"session:{index}", "name": "WebUI", "role": "Hermes", "kind": "session",
             "model": "vendor/known-model", "project": "fixture", "session": f"20261001_005900_{index:04}",
             "profile": "named-profile", "startedAt": at, "updatedAt": at, "detail": "Persisted Hermes session metadata"}
            for index in range(480)
        ]
        tasks = [
            {"id": f"task_{index}", "title": "Проверка очереди · " + "分析結果🧪" * 24}
            for index in range(180)
        ]
        gpus = [{
            "id": str(index), "temperature": 45.5, "utilization": 60,
            "history": [{"at": f"2026-10-01T00:{minute // 2:02d}:{minute % 2:02d}Z",
                         "temperature": 45.5, "utilization": 60, "memoryUsed": 4012, "power": 67.23}
                        for minute in range(120)],
        } for index in range(4)]
        usage = [
            {"at": at, "agent": f"session:{index % 480}", "project": "fixture", "model": "vendor/known-model",
             "task": "conversation", "session": f"20261001_005900_{index % 480:04}",
             "input": 18393, "output": 2803, "requests": 11, "cached": 613, "reasoning": 122,
             "ttftMs": None, "tokensPerSecond": None, "durationMs": None, "contextTokens": None, "errors": None,
             "retries": None, "orchestration": None}
            for index in range(800)
        ]
        snapshot = {
            "at": at, "agents": agents, "tasks": tasks, "gpus": gpus,
            "events": [], "usage": usage, "links": [{"from": "session:0", "to": "session:479"}],
            "services": [{"id": "hermes-gateway", "status": "active"}],
            "metrics": {"running": 3, "waiting": 0},
            "sources": [{"id": key, "label": key, "status": "available", "detail": "Observed."}
                        for key in ("hermes", "kanban", "gpu", "vllm")],
        }
        self.assertGreater(len(json_body(snapshot)), 512 * 1024)
        body = encode_snapshot(snapshot)
        self.assertLessEqual(len(body), MAX_SNAPSHOT_BYTES)
        data = json.loads(body)
        self.assertEqual(tasks[0], data["tasks"][0])
        self.assertLess(len(data["tasks"]), len(tasks))
        self.assertEqual(snapshot["metrics"], data["metrics"])
        self.assertEqual(snapshot["services"], data["services"])
        self.assertEqual(4, len(data["gpus"]))
        self.assertEqual(gpus[0]["temperature"], data["gpus"][0]["temperature"])
        self.assertEqual(gpus[0]["history"][-1], data["gpus"][0]["history"][-1])
        self.assertEqual([], data["links"])
        self.assertEqual(
            {"hermes": "partial", "kanban": "partial", "gpu": "partial", "vllm": "available"},
            {entry["id"]: entry["status"] for entry in data["sources"]},
        )
        impossible = {**snapshot, "services": [{"id": "broken-source", "name": "x" * MAX_SNAPSHOT_BYTES}]}
        with self.assertRaises(SnapshotTooLarge):
            encode_snapshot(impossible)

    def test_pi_allowlist_only(self):
        status = self.root / "extensions" / "pi-lamp" / "status.json"
        status.parent.mkdir(parents=True)
        status.write_text(json.dumps({
            "generated_at": time.time(),
            "jobs": [{
                "job_id": "pb-example", "created_at": time.time() - 20, "finished_at": time.time() - 1,
                "exit_code": 1, "error": "opaque_exception_private_data", "hermes": {"cwd": "/private-path"},
            }],
        }))
        agents, events, services, health = collect_pi(self.root)
        self.assertEqual("failed", agents[0]["status"])
        self.assertEqual("available", health["status"])
        serialized = json.dumps([agents, events, services])
        self.assertNotIn("opaque_exception_private_data", serialized)
        self.assertNotIn("private-path", serialized)
        self.assertNotIn("pb-example", serialized)


if __name__ == "__main__":
    unittest.main()
