import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from kanban import _detail_text
from safe_text import display_text
from telemetry import current_linux_owner, utc
from topology import classify, pi_topology


class TopologyTest(unittest.TestCase):
    def test_ledger_owner_requires_current_uid_and_start_ticks(self):
        fields = b"731 (worker) name) S " + b"0 " * 18 + b"1234 0"
        with (patch("telemetry.os.geteuid", create=True, return_value=1000),
              patch("telemetry.Path.stat", return_value=SimpleNamespace(st_uid=1000)) as stat,
              patch("telemetry.bounded_read", return_value=fields) as read):
            self.assertTrue(current_linux_owner(731, 1234))
            self.assertFalse(current_linux_owner(731, 1235))
            read.return_value = fields.replace(b") S ", b") Z ")
            self.assertFalse(current_linux_owner(731, 1234))
            read.return_value = fields
            stat.return_value = SimpleNamespace(st_uid=2000)
            self.assertFalse(current_linux_owner(731, 1234))

    def test_archive_excluded_and_native_handoffs_preserved(self):
        now = time.time()
        archive = [
            {"id": f"session:{index}", "kind": "session", "profile": "alpha", "name": "CLI",
             "status": "ended" if index else "failed", "active": False,
             "updatedAt": utc(now - 31 * 86400), "session": f"old-{index}"}
            for index in range(359)
        ]
        active = [
            {"id": "live", "kind": "session", "profile": "alpha", "name": "WebUI", "status": "running",
             "active": True, "updatedAt": utc(now), "session": "active-session"},
            {"id": "pi", "kind": "pi", "profile": "alpha", "name": "Pi job", "status": "running", "active": True},
            {"id": "child", "kind": "subagent", "profile": "alpha", "name": "Current review", "status": "stalled",
             "active": True, "issue": {"reason": "Reported by native watchdog", "at": utc(now)}},
        ]
        edges = [{"from": "session:28", "to": "pi", "kind": "delegation"},
                 {"from": "pi", "to": "child", "kind": "delegation"}]
        events = [{"channel": "communication", "from": "session:28", "to": "pi"}]
        tasks = [{"assignee": "alpha", "title": "Native task", "session": "active-session", "status": "running"}]
        agents, history, issues, links = classify(archive + active, edges, events, tasks, now)
        self.assertEqual(["profile", "executor", "pi", "subagent"], [entry["kind"] for entry in agents])
        self.assertEqual(359, len(history))
        self.assertEqual(["child"], [entry["id"] for entry in issues])
        self.assertEqual("Native task", agents[1]["task"])
        self.assertEqual("profile:alpha", events[0]["from"])
        self.assertEqual("session:28", events[0]["originFrom"])
        self.assertIn({"from": "profile:alpha", "to": "pi", "kind": "delegation", "originFrom": "session:28"}, links)
        self.assertIn(edges[1], links)
        active[1].update(active=False, status="completed")
        agents, history, issues, links = classify(archive + active, edges, [], tasks, now)
        self.assertNotIn("pi", {entry["id"] for entry in agents})
        self.assertIn("pi", {entry["id"] for entry in history})

    def test_closed_profiles_do_not_appear_as_live_gateways(self):
        now = time.time()
        nodes = [{"id": f"session:{profile}", "kind": "session", "profile": profile,
                  "status": "ended", "active": False} for profile in ("default", "closed", "named")]
        agents, runs, _, _ = classify(nodes, [], [], [], now, {"default", "named"})
        self.assertEqual({"default", "named"}, {profile["profile"] for profile in agents})
        self.assertTrue(all(profile["status"] == "ready" and not profile["active"] for profile in agents))
        self.assertIn("session:closed", {run["id"] for run in runs})

    def test_pi_inventory_and_latest_route_keep_active_and_archived_evidence_separate(self):
        now = time.time()
        hermes = {"id": "old-hermes", "kind": "session", "profile": "default", "status": "ended"}
        profile = {"id": "profile:default", "kind": "profile", "profile": "default"}
        roles = [{"id": "pi-role:reviewer", "kind": "pi-role", "active": False, "status": "configured"}]
        nodes = [
            {"id": "last", "kind": "pi", "status": "completed", "updatedAt": utc(now - 20)},
            {"id": "current", "kind": "pi", "status": "running", "updatedAt": utc(now), "active": True},
            {"id": "previous", "kind": "pi", "status": "unknown", "updatedAt": utc(now - 60)},
            {"id": "review", "kind": "subagent", "status": "completed", "updatedAt": utc(now - 40)},
        ]
        edges = [{"from": "old-hermes", "to": "last", "kind": "delegation"},
                 {"from": "previous", "to": "review", "kind": "delegation"}]
        inventory, links, recent, delegated = pi_topology(nodes, edges, [hermes], [profile], [], roles,
                                                          {"runtimeAvailable": True, "working": False})
        self.assertEqual(["pi:runtime", "pi-role:reviewer"], [node["id"] for node in inventory])
        self.assertTrue(all(not node["active"] for node in inventory))
        self.assertEqual("working", inventory[0]["status"])
        self.assertEqual({"integration", "configured", "execution"}, {edge["kind"] for edge in links})
        self.assertEqual(["old-hermes", "last"], [node["id"] for node in recent["nodes"]])
        self.assertEqual("observed", recent["nodes"][0]["status"])
        self.assertEqual(["previous", "review"], [node["id"] for node in delegated["nodes"]])
        self.assertEqual(utc(now - 40), delegated["at"])
        self.assertEqual("historical", delegated["links"][0]["kind"])
        nodes[1]["status"] = "queued"
        inventory, _, _, _ = pi_topology(nodes, edges, [hermes], [profile], [], roles,
                                          {"runtimeAvailable": True, "working": False})
        self.assertEqual("ready", inventory[0]["status"])

    def test_display_text_redacts_known_private_material(self):
        value = display_text("Keep this\n<system_prompt>system content</system_prompt>\n"
                             "APP_API_KEY=opaque-value\nAuthorization: Bearer 1234567890\n"
                             "https://example.test/thing?access_token=private-token\n/tmp/private-file")
        self.assertIn("Keep this", value)
        for private in ("system content", "opaque-value", "1234567890", "private-token", "/tmp/private-file"):
            self.assertNotIn(private, value)

    def test_display_adapters_omit_embedded_sensitive_fields(self):
        samples = (
            '{"system_prompt":"synthetic canary"}',
            'Payload {"developer_message":"synthetic canary"}',
            'Logged request {"cookie":"synthetic canary"}',
            'Response > Set-Cookie: synthetic canary',
            'Notes {"passphrase":"synthetic canary"}',
            r'Nested {\"password\":\"synthetic canary\"}',
            '{"role":"developer","content":"synthetic canary"}',
        )
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertEqual("[redacted]", display_text(sample))
                self.assertEqual("[redacted]", _detail_text(sample))
