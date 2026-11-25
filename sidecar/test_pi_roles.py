import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pi_roles import MAX_FRONT_MATTER_BYTES, MAX_SETTINGS_BYTES, read_pi_roles


class PiRolesTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "agent"
        self.root.mkdir()
        self.directory = self.root / "agents"
        self.directory.mkdir()

    def settings(self, packages=None):
        if packages is None:
            packages = ["npm:pi-open-agents@0.1.31"]
        (self.root / "settings.json").write_text(json.dumps({"packages": packages}), encoding="utf-8")

    def definition(self, filename, front_matter, body="system prompt text"):
        path = self.directory / filename
        path.write_text(f"---\n{front_matter}\n---\n{body}", encoding="utf-8")
        return path

    def test_verified_configured_roles_have_no_runtime_activity(self):
        self.settings()
        self.definition("worker.md", "name: worker\nmodel: github_pat_hide_this_value\ndescription: private description")
        self.definition("explorer.md", "name: explorer\nmodel: qwen-local\ntools:\n  - read")
        self.definition("reviewer.md", 'description: "private description"\nname: "reviewer" # comment\nmodel: "openai/gpt-6"')
        self.definition("orchestrator.md", "name: 'orchestrator'\ndescription: |\n  name: nested")
        self.definition("old.md.bak", "name: old")
        self.definition("deep.md.disabled", "name: deep")
        roles, info = read_pi_roles(self.root)

        self.assertEqual("available", info["status"])
        self.assertEqual(["explorer", "orchestrator", "reviewer", "worker"], [role["name"] for role in roles])
        self.assertEqual(["qwen-local", None, "openai/gpt-6", None], [role["model"] for role in roles])
        for role in roles:
            self.assertEqual(f"pi-role:{role['name']}", role["id"])
            self.assertEqual(("pi-role", "Open Agents", "configured", "pi", False),
                             (role["kind"], role["role"], role["status"], role["source"], role["active"]))
            self.assertIsNone(role["startedAt"])
            self.assertIsNone(role["updatedAt"])
        output = json.dumps((roles, info))
        for private in ("private description", "system prompt text", "github_pat_hide_this_value", "nested"):
            self.assertNotIn(private, output)

    def test_requires_package_even_when_definition_exists(self):
        self.definition("worker.md", "name: worker")
        self.settings(["npm:pi-open-agents-lookalike@0.1.31", "npm:pi-open-agents@https://private.example"])
        roles, info = read_pi_roles(self.root)
        self.assertEqual([], roles)
        self.assertEqual("unavailable", info["status"])
        self.assertIn("absent from Pi settings", info["detail"])
        self.assertNotIn("private.example", json.dumps(info))

    def test_package_without_roles_reports_empty_config(self):
        self.settings([{"source": "npm:pi-open-agents@0.1.31"}])
        self.definition("explorer.md.bak", "name: explorer")
        roles, info = read_pi_roles(self.root)
        self.assertEqual([], roles)
        self.assertEqual("available", info["status"])
        self.assertIn("No verified", info["detail"])

    def test_ambiguous_and_unsupported_identity_forms_are_omitted(self):
        self.settings()
        self.definition("worker.md", "name: worker")
        self.definition("shadow.md", "name: WORKER")
        self.definition("explorer.md", "name: explorer\nname: alternate")
        self.definition("reviewer.md", "name: reviewer\n'name': alternate")
        self.definition("unsafe.md", "name: unsafe\n<<: {name: alternate}")
        self.definition("anchor.md", "name: &alias anchor")
        self.definition("hash.md", "name: hash\n  updated")
        self.definition("quoted.md", 'description: "continued\nname: quoted\n# closing"')
        self.definition("valid.md", "name: valid\nmodel: https://private.example")
        roles, info = read_pi_roles(self.root)
        self.assertEqual(["valid"], [role["name"] for role in roles])
        self.assertIsNone(roles[0]["model"])
        self.assertEqual("partial", info["status"])
        self.assertTrue(any("Ambiguous" in reason for reason in info["missing"]))
        self.assertTrue(any("Invalid" in reason for reason in info["missing"]))

    def test_limits_ignore_large_bodies_and_cap_front_matter(self):
        self.settings()
        self.definition("worker.md", "name: worker", "x" * (MAX_FRONT_MATTER_BYTES * 2))
        self.definition("oversized.md", "name: oversized\ndescription: " + "x" * MAX_FRONT_MATTER_BYTES)
        roles, info = read_pi_roles(self.root)
        self.assertEqual(["worker"], [role["name"] for role in roles])
        self.assertEqual("partial", info["status"])
        self.assertTrue(any("front matter" in reason for reason in info["limits"]))

        (self.root / "settings.json").write_text(" " * (MAX_SETTINGS_BYTES + 1), encoding="utf-8")
        roles, info = read_pi_roles(self.root)
        self.assertEqual([], roles)
        self.assertEqual("unavailable", info["status"])
        self.assertTrue(info["limits"])

    def test_hard_linked_definitions_are_omitted(self):
        self.settings()
        outside_role = self.root / "outside.md"
        outside_role.write_text("---\nname: worker\n---\n", encoding="utf-8")
        try:
            os.link(outside_role, self.directory / "worker.md")
        except OSError as error:
            self.skipTest(f"Hard links unavailable: {error.strerror}")
        roles, info = read_pi_roles(self.root)
        self.assertEqual([], roles)
        self.assertEqual("partial", info["status"])

    def test_directory_limit_and_deadline_omit_incomplete_discovery(self):
        self.settings()
        self.definition("worker.md", "name: worker")
        self.definition("unrelated.md.bak", "name: unrelated")
        with patch("pi_roles.MAX_DIRECTORY_ENTRIES", 1):
            roles, info = read_pi_roles(self.root)
        self.assertEqual([], roles)
        self.assertEqual("partial", info["status"])
        self.assertTrue(info["limits"])

        with patch("pi_roles.READ_SECONDS", 0):
            roles, info = read_pi_roles(self.root)
        self.assertEqual([], roles)
        self.assertEqual("partial", info["status"])
        self.assertTrue(info["limits"])

    def test_linked_definitions_and_directories_are_omitted(self):
        self.settings()
        self.definition("worker.md", "name: worker")
        external = self.root / "external"
        external.mkdir()
        outside_role = external / "reviewer.md"
        outside_role.write_text("---\nname: reviewer\n---\n", encoding="utf-8")
        try:
            (self.directory / "reviewer.md").symlink_to(outside_role)
        except OSError as error:
            self.skipTest(f"Symlinks unavailable: {error.strerror}")
        roles, info = read_pi_roles(self.root)
        self.assertEqual(["worker"], [role["name"] for role in roles])
        self.assertEqual("partial", info["status"])

        self.directory.rename(self.root / "original-agents")
        self.directory.symlink_to(external, target_is_directory=True)
        roles, info = read_pi_roles(self.root)
        self.assertEqual([], roles)
        self.assertEqual("unavailable", info["status"])


if __name__ == "__main__":
    unittest.main()
