import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from pi_integration import collect_pi_bridge, pi_web_link
from telemetry import utc


class PiIntegrationTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.webui, self.bridge, self.sessions = (self.root / part for part in ("webui", "bridge", "sessions"))
        self.epoch = time.time()
        self.root_session = {"id": "hermes-node", "kind": "session", "session": "ui-verified", "profile": "team"}

    def write(self, path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    def cache(self, jobs, age=0):
        self.write(self.webui / "extensions" / "pi-lamp" / "status.json", {
            "generated_at": utc(self.epoch - age), "jobs": jobs,
        })

    def job(self, key, **overrides):
        path = self.bridge / "jobs" / key / "job.json"
        self.write(path, {"job_id": key, "status": "running", "created_at": self.epoch - 20,
                          "updated_at": self.epoch - 10, "task": "Inspect regression",
                          "origin": {"ui_session_id": "ui-verified"},
                          "watchdog": {"last_activity_at": self.epoch - 2}, **overrides})
        return path

    def transcript(self, session, rows, *, path=None, header_id=None):
        path = path or self.sessions / "project" / f"2026-11-24_{session}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        values = [{"type": "session", "id": header_id or session, "cwd": "/home/private"}, *rows]
        path.write_text("".join(json.dumps(value) + "\n" for value in values), encoding="utf-8")
        return path

    def call(self, call_id, name="researcher", **args):
        return {"type": "message", "timestamp": utc(self.epoch - 5), "message": {"role": "assistant",
                "model": "qwen-local", "content": [{"type": "toolCall", "id": call_id, "name": "subagent",
                "arguments": {"agent": name, "task": "Review tests", "cwd": "/home/private", **args}}]}}

    def result(self, call_id, **details):
        return {"type": "message", "timestamp": utc(self.epoch - 4), "message": {"role": "toolResult",
                "toolName": "subagent", "toolCallId": call_id, "isError": False,
                "details": {"model": "qwen-local", "output": "Found a signedness issue", **details}}}

    def collect(self, *hermes):
        return collect_pi_bridge(self.webui, self.bridge, self.sessions, list(hermes) or [self.root_session])

    def test_direct_native_route_and_completed_open_agents_call(self):
        transcript = self.transcript("pi-1", [
            {"type": "custom", "customType": "active_agent", "data": {"name": "selection-only"}},
            {"type": "custom", "customType": "open-agents-state", "data": {"name": "also-selection"}},
            self.call("call-1"), self.result("call-1", stderr="private stderr", tools=["private raw tool"]),
        ])
        self.cache([{"job_id": "pb-job", "group": "recent", "pi_web": {"project_name": "hcc"}}])
        self.job("pb-job", status="completed", pi_session_id="pi-1", pi_session_file=str(transcript),
                 task="Inspect regression password=do-not-display", final_result="Patched the regression",
                 origin={"ui_session_id": "ui-verified", "chat_id": "private-chat"},
                 feedbacks=[{"at": self.epoch - 12, "text": "Check native data too"}],
                 delivery={"attempted": True, "ok": True, "at": self.epoch - 3})
        nodes, links, events, services, source = self.collect()
        pi, child = nodes
        self.assertEqual(("pi", "subagent"), (pi["kind"], child["kind"]))
        self.assertEqual(("completed", "completed"), (pi["status"], child["status"]))
        self.assertEqual((False, False), (pi["active"], child["active"]))
        self.assertEqual(("pb-job", "team", "hcc", "pi-1"), (pi["jobId"], pi["profile"], pi["project"], pi["session"]))
        self.assertEqual([("hermes-node", pi["id"]), (pi["id"], child["id"])],
                         [(edge["from"], edge["to"]) for edge in links])
        self.assertEqual([("hermes-node", pi["id"]), (None, pi["id"]), (pi["id"], "hermes-node"),
                          (pi["id"], child["id"]), (child["id"], pi["id"])],
                         [(item["from"], item["to"]) for item in events])
        self.assertEqual({pi["id"]}, {item["thread"] for item in events})
        self.assertEqual({"communication"}, {item["channel"] for item in events})
        self.assertIn("signedness", events[-1]["body"])
        self.assertEqual("available", source["status"])
        self.assertEqual("active", services[0]["status"])
        response = json.dumps((nodes, links, events))
        for secret in ("do-not-display", "private stderr", "private raw tool", "private-chat", "selection-only", "/home/private"):
            self.assertNotIn(secret, response)

    def test_recent_active_call_and_stale_or_abandoned_unmatched_calls(self):
        self.cache([{"job_id": "pb-stale", "group": "active"}], age=200)
        self.job("pb-live", pi_session_id="pi-active")
        self.transcript("pi-active", [self.call("old", "old-call"), self.call("pending", "pending-call")])
        stale = self.transcript("pi-stale", [self.call("stale", "stale-call")])
        self.job("pb-stale", pi_session_id="pi-stale", pi_session_file=str(stale),
                 watchdog={"last_activity_at": self.epoch - 500}, updated_at=self.epoch - 400)
        nodes, _, _, _, source = self.collect()
        child = {node["name"]: node for node in nodes if node["kind"] == "subagent"}
        self.assertEqual({"old-call", "pending-call"}, set(child))
        self.assertTrue(child["pending-call"]["active"])
        self.assertEqual("waiting", child["pending-call"]["status"])
        self.assertFalse(child["old-call"]["active"])
        self.assertEqual("unknown", child["old-call"]["status"])
        self.assertEqual("partial", source["status"])
        self.assertTrue(any("fresh activity evidence" in reason for reason in source["missing"]))

    def test_reused_pi_session_attributes_calls_to_one_native_turn(self):
        old_call = {**self.call("first", "first-run"), "timestamp": utc(self.epoch - 55)}
        old_result = {**self.result("first", usage={"input": 42}), "timestamp": utc(self.epoch - 50)}
        transcript = self.transcript("pi-reused", [old_call, old_result, self.call("second", "second-run")])
        self.cache([{"job_id": "pb-new", "group": "active", "status": "running"}])
        self.job("pb-old", status="completed", pi_session_id="pi-reused", pi_session_file=str(transcript),
                 created_at=self.epoch-70, updated_at=self.epoch-50,
                 turns=[{"started_at":utc(self.epoch-70),"finished_at":utc(self.epoch-50)}])
        self.job("pb-new", pi_session_id="pi-reused", pi_session_file=str(transcript),
                 turns=[{"started_at":utc(self.epoch-20),"finished_at":None}])
        nodes, links, _, _, _ = self.collect()
        indexed = {node["id"]:node for node in nodes}
        assigned = [(indexed[link["from"]].get("jobId"), indexed[link["to"]]["name"]) for link in links
                    if indexed[link["to"]]["kind"]=="subagent"]
        self.assertCountEqual([("pb-old","first-run"), ("pb-new","second-run")], assigned)
        metered = [node for node in nodes if node.get("completedMetrics")]
        self.assertEqual([("pb-old",42)], [(node["jobId"],node["completedMetrics"]["input"]) for node in metered])

    def test_unique_origin_required_and_failed_delivery_is_technical(self):
        self.job("pb-job", status="failed", error="explicit Pi error", final_result="Saved local result",
                 delivery={"attempted": True, "ok": False, "at": self.epoch - 5,
                           "reason": "destination route was unavailable"})
        self.job("pb-chat", origin={"chat_id": "ui-verified"})
        duplicate = {**self.root_session, "id": "other-profile-node", "profile": "other"}
        _, links, events, _, source = self.collect(self.root_session, duplicate)
        self.assertEqual([], links)
        self.assertFalse(any(item["from"] == "hermes-node" or item["to"] == "hermes-node" for item in events))
        outcome = next(item for item in events if item["kind"] == "result")
        self.assertIsNone(outcome["to"])
        self.assertEqual("Saved local result", outcome["body"])
        technical = next(item for item in events if item["channel"] == "technical")
        self.assertEqual("destination route was unavailable", technical["reason"])
        self.assertTrue(any("lacked a unique" in reason for reason in source["missing"]))

    def test_transcript_path_containment_and_identity(self):
        outsider = self.transcript("pi-out", [self.call("external")], path=self.root / "external.jsonl")
        invalid = self.transcript("pi-mismatch", [self.call("mismatch")], header_id="pi-other")
        self.job("pb-out", pi_session_id="pi-out", pi_session_file=str(outsider))
        self.job("pb-mismatch", pi_session_id="pi-mismatch", pi_session_file=str(invalid))
        nodes, _, _, _, source = self.collect()
        self.assertEqual(["pi", "pi"], [node["kind"] for node in nodes])
        self.assertTrue(any("2 Pi session transcripts" in reason for reason in source["missing"]))

    def test_session_symlinks_and_invalid_native_job_fields_are_ignored(self):
        file = self.transcript("pi-link", [self.call("external")], path=self.root / "external.jsonl")
        path = self.sessions / "project" / "2026-11-24_pi-link.jsonl"
        path.parent.mkdir(parents=True)
        try:
            os.symlink(file, path)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks are unavailable")
        self.job("pb-job", pi_session_id="pi-link", pi_session_file=str(path), status={"unexpected": True}, task=42)
        nodes, _, events, _, source = self.collect()
        self.assertEqual(["pi"], [node["kind"] for node in nodes])
        self.assertEqual("unknown", nodes[0]["status"])
        self.assertEqual([], events)
        self.assertTrue(any("1 Pi session transcripts" in reason for reason in source["missing"]))

    def test_transcript_byte_limit_preserves_verified_recent_calls(self):
        transcript = self.transcript("pi-limit", [self.call("old", task="early " * 1000), self.call("new")])
        self.job("pb-limit", pi_session_id="pi-limit", pi_session_file=str(transcript))
        with patch("pi_integration.MAX_TRANSCRIPT_BYTES", 600):
            nodes, _, events, _, source = self.collect()
        self.assertEqual(["pi", "subagent"], [node["kind"] for node in nodes])
        self.assertTrue(nodes[-1]["active"])
        self.assertNotIn("early", json.dumps(events))
        self.assertTrue(any("Pi session reads" in reason for reason in source["limits"]))

    def test_pi_web_link_requires_native_session_and_exact_safe_url(self):
        url = "http://192.168.10.4:8504/?machine=lab&project=HCC%20Release&session=pi-1&workspace=default"
        self.assertIn("project=HCC+Release", pi_web_link({"available": True, "url": url}, "pi-1"))
        samples = (url.replace("192.168.10.4", "example.com"), url.replace("192.168.10.4", "127.0.0.1"),
                   url.replace(":8504/", ":8504/foo"), url.replace(":8504", ":8505"), url + "#x",
                   url.replace("&workspace=default", "&session=pi-1"), url.replace("lab", "sk-12345678900"),
                   url.replace("lab", "%2Fhome%2Fprivate"), url + "&token=secret", url.replace("pi-1", "pi-2"))
        for candidate in samples:
            with self.subTest(candidate=candidate):
                self.assertIsNone(pi_web_link({"available": True, "url": candidate}, "pi-1"))
        self.assertIsNone(pi_web_link({"available": False, "url": url}, "pi-1"))
        self.assertIsNone(pi_web_link({"available": True, "url": url}, None))

    def test_watchdog_attention_and_recovered_status(self):
        self.cache([{"job_id": "pb-stall", "group": "active"}, {"job_id": "pb-recovery", "group": "active"}])
        attention = {"needs_attention_at": self.epoch - 16, "last_activity_at": self.epoch - 70,
                     "diagnosis": "Pi stopped emitting progress"}
        self.job("pb-stall", watchdog={**attention, "state": "suspected_stall"})
        self.job("pb-recovery", watchdog={**attention, "state": "recovered"})
        nodes, _, events, _, _ = self.collect()
        runs = {node["jobId"]: node for node in nodes}
        self.assertEqual("stalled", runs["pb-stall"]["status"])
        self.assertEqual("Pi stopped emitting progress", runs["pb-stall"]["issue"]["reason"])
        self.assertEqual(utc(self.epoch - 70), runs["pb-stall"]["issue"]["lastActivityAt"])
        self.assertEqual("running", runs["pb-recovery"]["status"])
        self.assertNotIn("issue", runs["pb-recovery"])
        watchdog = next(item for item in events if item["title"] == "Pi watchdog requested attention")
        self.assertEqual("technical", watchdog["channel"])


if __name__ == "__main__":
    unittest.main()
