import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from kanban import MAX_ATTEMPTS, collect_kanban


TASK_SCHEMA = """
    CREATE TABLE tasks (
        id TEXT PRIMARY KEY, title TEXT, body TEXT, assignee TEXT, status TEXT,
        created_at INTEGER, started_at INTEGER, completed_at INTEGER,
        project_id TEXT, tenant TEXT, session_id TEXT, workspace_path TEXT
    );
    CREATE TABLE task_links (parent_id TEXT, child_id TEXT, PRIMARY KEY (parent_id, child_id));
    CREATE TABLE task_runs (id INTEGER PRIMARY KEY, task_id TEXT, status TEXT,
                            started_at INTEGER, ended_at INTEGER, summary TEXT, metadata TEXT);
    CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT, created_at INTEGER, payload TEXT);
"""


class KanbanTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)

    def collect(self):
        return collect_kanban(self.home, time.monotonic() + 2)

    def test_native_paths_references_and_read_only_wal(self):
        default = self.home / "kanban.db"
        named = self.home / "kanban" / "boards" / "release" / "kanban.db"
        named.parent.mkdir(parents=True)
        with sqlite3.connect(default) as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript(TASK_SCHEMA)
            db.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
                "t_child", "Проверить отчёт", "Описание задачи", "builder", "review", 100, 120,
                None, "project-id", "namespace", "session-id", "private-workspace-path",
            ))
            db.execute("INSERT INTO tasks (id,title,status) VALUES ('t_parent','Parent','archived')")
            db.execute("INSERT INTO task_links VALUES ('t_parent','t_child')")
            db.execute("INSERT INTO task_runs VALUES (1,'t_child','failed',120,130,'private-run-summary','{}')")
            db.execute("INSERT INTO task_runs VALUES (2,'t_child','done',140,150,'private-run-summary','{}')")
            db.execute("INSERT INTO task_events VALUES (1,'t_child',160,'private-event-payload')")
        db.close()
        with sqlite3.connect(named) as db:
            db.executescript(TASK_SCHEMA)
            db.execute("INSERT INTO tasks (id,title,body,status,session_id) VALUES (?,?,?,?,?)", (
                "t_child", "Release glpat-fake-token-for-test", "API key: fixture-token", "scheduled", "https://invalid.test",
            ))
        db.close()
        before = {path: path.read_bytes() for path in self.home.rglob("*") if path.is_file()}

        tasks, source = self.collect()

        self.assertEqual("available", source["status"])
        index = {task["id"]: task for task in tasks}
        self.assertEqual({"default:t_child", "default:t_parent", "release:t_child"}, set(index))
        task = index["default:t_child"]
        self.assertEqual("Проверить отчёт", task["title"])
        self.assertNotIn("description", task)
        self.assertEqual("review", task["status"])
        self.assertEqual(["default:t_parent"], task["dependsOn"])
        self.assertEqual(["done", "failed"], [attempt["status"] for attempt in task["attempts"]])
        self.assertEqual("1970-01-01T00:02:40+00:00", task["updatedAt"])
        self.assertEqual("/session/session-id", task["sessionHref"])
        self.assertEqual("scheduled", index["release:t_child"]["status"])
        self.assertEqual("[redacted]", index["release:t_child"]["title"])
        self.assertNotIn("description", index["release:t_child"])
        self.assertNotIn("sessionHref", index["release:t_child"])
        self.assertNotIn("private-", json.dumps(tasks))
        self.assertEqual(before, {path: path.read_bytes() for path in self.home.rglob("*") if path.is_file()})

    def test_corrupt_board_isolated_and_older_schema_supported(self):
        default = self.home / "kanban.db"
        with sqlite3.connect(default) as db:
            db.executescript("""
                CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT, status TEXT);
                INSERT INTO tasks VALUES ('t_old','Older schema','ready');
                CREATE TABLE task_runs (id INTEGER PRIMARY KEY, task_id TEXT, status TEXT);
            """)
            db.executemany("INSERT INTO task_runs (task_id,status) VALUES ('t_old',?)", [("done",)] * (MAX_ATTEMPTS + 1))
        db.close()
        broken = self.home / "kanban" / "boards" / "broken" / "kanban.db"
        broken.parent.mkdir(parents=True)
        broken.write_bytes(b"broken data")

        tasks, source = self.collect()

        self.assertEqual("partial", source["status"])
        self.assertEqual(["default:t_old"], [task["id"] for task in tasks])
        self.assertIsNone(tasks[0]["dependsOn"])
        self.assertEqual(MAX_ATTEMPTS, len(tasks[0]["attempts"]))
        self.assertEqual(b"broken data", broken.read_bytes())

    def test_missing_and_empty_board_differ(self):
        self.assertEqual("unavailable", self.collect()[1]["status"])
        self.assertEqual([], list(self.home.iterdir()))
        with sqlite3.connect(self.home / "kanban.db") as db:
            db.executescript(TASK_SCHEMA)
        db.close()
        tasks, source = self.collect()
        self.assertEqual([], tasks)
        self.assertEqual("available", source["status"])
