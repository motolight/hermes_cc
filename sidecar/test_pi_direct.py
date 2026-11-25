import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pi_direct import bridge_ownership, collect_pi_direct
from telemetry import utc


class StandalonePiTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.sessions = Path(temporary.name) / "sessions"
        self.epoch = 1795636800

    def transcript(self, key, rows, *, project="private-workspace", header_id=None, mtime=None):
        path = self.sessions / project / f"2026-11-25T12-00-00.000Z_{key}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        header = {"type": "session", "version": 3, "id": header_id or key,
                  "cwd": "/private/workspace", "timestamp": utc(self.epoch - 600)}
        path.write_text("".join(json.dumps(row) + "\n" for row in [header, *rows]), encoding="utf-8")
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def call(self, key, *, at=None, tool="subagent"):
        return {"type": "message", "timestamp": utc(at if at is not None else self.epoch - 10),
                "message": {"role": "assistant", "model": "qwen-local", "content": [{
                    "type": "toolCall", "name": tool, "id": key,
                    "arguments": {"agent": "reviewer", "task": "Private task at /a/private/path", "cwd": "/private/workspace"},
                }]}}

    def result(self, key, *, failed=False):
        return {"type": "message", "timestamp": utc(self.epoch - 8),
                "message": {"role": "toolResult", "toolName": "subagent", "toolCallId": key,
                            "isError": failed, "details": {"output": "Private result from private-workspace",
                                                        "model": "local/other", "usage": {"input": 42}}}}

    def test_exact_chains_hide_all_free_form_text_and_never_imply_parent_activity(self):
        self.transcript("native-session", [
            {"type": "message", "message": {"role": "user", "content": "Private user message"}},
            self.call("matched"), self.result("matched"), self.call("failed"), self.result("failed", failed=True),
            self.call("pending"), self.call("other", tool="not-subagent"), self.result("unmatched-result"),
        ])
        nodes, links, events, info = collect_pi_direct(self.sessions, set())
        parent, completed, failed, pending = nodes
        self.assertEqual(("pi-direct", "partial"), (info["id"], info["status"]))
        self.assertEqual(("pi", "unknown", "native-session", "qwen-local", None),
                         (parent["kind"], parent["status"], parent["session"], parent["model"], parent["project"]))
        self.assertEqual(["completed", "failed", "unknown"], [node["status"] for node in nodes[1:]])
        self.assertTrue(all(not node["active"] for node in nodes))
        self.assertEqual(utc(self.epoch - 600), parent["startedAt"])
        self.assertEqual("local/other", completed["model"])
        self.assertEqual(42, completed["completedMetrics"]["input"])
        self.assertEqual("Open Agents returned a failed tool result.", failed["issue"]["reason"])
        self.assertEqual([(parent["id"], child["id"]) for child in nodes[1:]],
                         [(link["from"], link["to"]) for link in links])
        self.assertEqual(["delegation", "result", "delegation", "error", "delegation"], [event["kind"] for event in events])
        self.assertEqual((parent["id"], pending["id"]), (events[-1]["from"], events[-1]["to"]))
        payload = json.dumps((nodes, links, events, info))
        for value in ("Private task", "Private result", "Private user message", "private-workspace", "/private/"):
            self.assertNotIn(value, payload)

    def test_bridge_ownership_ambiguous_ids_mismatched_headers_and_non_tasks(self):
        self.transcript("bridge-owned", [self.call("bridge"), self.result("bridge")])
        self.transcript("ambiguous", [self.call("first")])
        self.transcript("ambiguous", [self.call("second")], project="other")
        self.transcript("header-mismatch", [self.call("different")], header_id="native-other")
        self.transcript("selection-only", [{"type": "custom", "customType": "active_agent", "data": {"name": "ignored"}}])
        self.transcript("duplicate-calls", [self.call("duplicate"), self.call("duplicate"), self.result("duplicate")])
        nodes, links, events, info = collect_pi_direct(self.sessions, {"bridge-owned"}, {
            "bridge-owned": {"status": "running"}, "ambiguous": {"status": "running"},
        })
        self.assertEqual(([], [], []), (nodes, links, events))
        self.assertTrue(any("Ambiguous standalone Pi session IDs" in item for item in info["missing"]))
        self.assertTrue(any("1 standalone Pi transcripts" in item for item in info["missing"]))
        self.assertTrue(any("Ambiguous Open Agents" in item for item in info["limits"]))

    def test_live_metadata_and_exact_active_window(self):
        self.transcript("waiting-current", [self.call("started-at-boundary")])
        self.transcript("waiting-earlier", [self.call("started-before-boundary", at=self.epoch - 11)])
        self.transcript("no-window", [self.call("no-turn-boundary")])
        self.transcript("live-empty", [])
        malformed = self.transcript("live-malformed", [self.call("malformed-record")])
        with malformed.open("ab") as stream:
            stream.write(b'{"incomplete":')
        started = utc(self.epoch - 10)
        active = {"status": "running", "startedAt": started, "updatedAt": utc(self.epoch), "model": "live-model"}
        nodes, _, _, _ = collect_pi_direct(self.sessions, set(), {
            "waiting-current": active, "waiting-earlier": active, "live-malformed": active,
            "no-window": {"status": "running"}, "live-empty": {"status": "running"},
            "no-transcript": {"status": "queued"}, "invalid/session": active,
        })
        parents = {node["session"]: node for node in nodes if node["kind"] == "pi"}
        self.assertEqual({"waiting-current", "waiting-earlier", "no-window", "live-empty", "no-transcript", "live-malformed"}, set(parents))
        self.assertTrue(all(parent["active"] for parent in parents.values()))
        self.assertEqual((started, "live-model"), (parents["waiting-current"]["startedAt"], parents["waiting-current"]["model"]))
        self.assertEqual("queued", parents["no-transcript"]["status"])
        children = {node["session"]: node for node in nodes if node["kind"] == "subagent"}
        self.assertEqual({"waiting-current": "waiting", "waiting-earlier": "unknown", "no-window": "unknown", "live-malformed": "unknown"},
                         {key: node["status"] for key, node in children.items()})
        self.assertEqual(["waiting-current"], [key for key, node in children.items() if node["active"]])

    def test_pi_web_reuse_of_a_completed_bridge_session_keeps_only_verified_live_parent(self):
        self.transcript("reused", [self.call("bridge-call"), self.result("bridge-call")])
        live = {"reused": {"status": "running"}}
        nodes, links, events, _ = collect_pi_direct(self.sessions, {"reused"}, live, set())
        self.assertEqual([("reused", True)], [(node["session"], node["active"]) for node in nodes])
        self.assertEqual(([], []), (links, events))
        nodes, _, _, _ = collect_pi_direct(self.sessions, {"reused"}, live, {"reused"})
        self.assertEqual([], nodes)

    def test_independent_new_turn_in_reused_session_counts_only_new_calls(self):
        self.transcript("reused", [self.call("bridge-call", at=self.epoch - 20), self.result("bridge-call"), self.call("fresh")])
        live = {"reused": {"status": "running", "startedAt": utc(self.epoch - 11)}}
        jobs = [{"kind": "pi", "session": "reused", "status": "completed", "updatedAt": utc(self.epoch - 12)}]
        sessions, active, ended = bridge_ownership(jobs)
        nodes, links, events, _ = collect_pi_direct(self.sessions, sessions, live, active, ended)
        self.assertEqual(["pi", "subagent"], [node["kind"] for node in nodes])
        self.assertEqual("waiting", nodes[1]["status"])
        self.assertEqual(1, len(links))
        self.assertEqual(["delegation"], [event["kind"] for event in events])
        jobs.append({**jobs[0], "status": "unknown"})
        sessions, active, ended = bridge_ownership(jobs)
        self.assertEqual({}, ended)
        nodes, links, _, _ = collect_pi_direct(self.sessions, sessions, live, active, ended)
        self.assertEqual(["pi"], [node["kind"] for node in nodes])
        self.assertEqual([], links)

    def test_limits_prioritize_verified_live_and_then_newest_transcript(self):
        for offset, key in enumerate(("live-old", "history-old", "history-new")):
            self.transcript(key, [self.call("call"), self.result("call")], mtime=self.epoch + offset)
        with patch("pi_direct.MAX_DIRECT_TRANSCRIPTS", 2):
            nodes, _, _, info = collect_pi_direct(self.sessions, set(), {"live-old": {"status": "running"}})
        self.assertEqual(["live-old", "history-new"], [node["session"] for node in nodes if node["kind"] == "pi"])
        self.assertTrue(any("At most 2 recent standalone Pi transcripts" in item for item in info["limits"]))

    def test_deeper_directories_are_not_discovered(self):
        self.transcript("nested", [self.call("private")], project="private/deeper")
        nodes, links, events, info = collect_pi_direct(self.sessions, set(), {})
        self.assertEqual(([], [], []), (nodes, links, events))
        self.assertEqual("available", info["status"])

    def test_symlinks_are_not_discovered(self):
        outside = self.transcript("linked", [self.call("private")], project="private/deeper")
        try:
            (self.sessions / "linked-directory").symlink_to(outside.parent, target_is_directory=True)
            (self.sessions / "private" / outside.name).symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks are unavailable")
        nodes, links, events, info = collect_pi_direct(self.sessions, set(), {})
        self.assertEqual(([], [], []), (nodes, links, events))
        self.assertEqual("available", info["status"])


if __name__ == "__main__":
    unittest.main()
