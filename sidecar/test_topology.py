import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from kanban import _detail_text
from safe_text import display_text
from telemetry import current_linux_owner, utc
from topology import classify


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
