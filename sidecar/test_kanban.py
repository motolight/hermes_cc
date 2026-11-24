import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from kanban import (BODY_LIMIT_BYTES, MAX_ATTEMPTS, MAX_DETAIL_COMMENTS, MAX_DETAIL_EVENTS,
                    MAX_TASK_DETAIL_BYTES, NOTE_LIMIT_BYTES, collect_kanban, read_kanban_task)


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

    def detail(self, key):
        return read_kanban_task(self.home, key, time.monotonic() + 2)

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

    def test_detail_uses_native_attempts_events_and_link_evidence(self):
        path = self.home / "kanban.db"
        with sqlite3.connect(path) as db:
            db.executescript(TASK_SCHEMA + """
                ALTER TABLE tasks ADD COLUMN completion_contract TEXT;
                ALTER TABLE tasks ADD COLUMN result TEXT;
                ALTER TABLE tasks ADD COLUMN current_run_id INTEGER;
                ALTER TABLE tasks ADD COLUMN block_kind TEXT;
                ALTER TABLE tasks ADD COLUMN last_heartbeat_at INTEGER;
                ALTER TABLE task_events ADD COLUMN kind TEXT;
                ALTER TABLE task_events ADD COLUMN run_id INTEGER;
                ALTER TABLE task_runs ADD COLUMN profile TEXT;
                ALTER TABLE task_runs ADD COLUMN last_heartbeat_at INTEGER;
                ALTER TABLE task_runs ADD COLUMN outcome TEXT;
                ALTER TABLE task_runs ADD COLUMN error TEXT;
                CREATE TABLE task_comments (id INTEGER PRIMARY KEY, task_id TEXT, author TEXT,
                                            body TEXT, created_at INTEGER);
                INSERT INTO tasks (id,title,status) VALUES ('t_parent','Parent','archived'),
                                                            ('t_descendant','Descendant','todo');
                INSERT INTO task_links VALUES ('t_parent','t_main'), ('t_main','t_descendant');
            """)
            description = "Задача по сборке.\n\n## Критерии\n\n- [ ] Проверить сборку.\n" + "Детали " * 500
            contract = "https://github.com/org/repo/pull/44"
            db.execute("""INSERT INTO tasks (id,title,body,status,assignee,session_id,completion_contract,
                                             result,current_run_id,created_at,last_heartbeat_at,workspace_path)
                          VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                       ("t_main", "Сборка", description, "running", "assigned", "origin-session", contract,
                        None, 2, 100, 171, "/private/workspace"))
            db.execute("""INSERT INTO task_runs (id,task_id,status,started_at,ended_at,summary,metadata,
                                                 profile,last_heartbeat_at,outcome,error)
                          VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                       (1, "t_main", "done", 120, 141, "Previous run result", json.dumps({
                           "published_pr": "https://github.com/org/repo/pull/43",
                           "pi_job_id": "pb-unverified", "prompt": "DO-NOT-SEND",
                       }), "previous", 139, "completed", "Authorization: fixture-private"))
            db.execute("""INSERT INTO task_runs (id,task_id,status,started_at,profile,last_heartbeat_at)
                          VALUES (2,'t_main','running',150,'actual-worker',170)""")
            db.executemany("INSERT INTO task_events (id,task_id,kind,run_id,created_at,payload) VALUES (?,?,?,?,?,?)", [
                (1, "t_main", "pr_acceptance", 1, 140, json.dumps({
                    "pr_url": "https://github.com/org/repo/pull/43", "ok": True, "private": "DO-NOT-SEND",
                })),
                (2, "t_main", "heartbeat", 2, 170, json.dumps({"note": "Inspecting the build"})),
                (3, "t_main", "edited", None, 173, json.dumps({"fields": ["title"], "value": "DO-NOT-SEND"})),
                (4, "t_descendant", "dependency_wait", None, 172, json.dumps({"reason": "Waiting for the build"})),
                (5, "t_main", "owner_admitted", 2, 151, json.dumps({
                    "session_id": "native-worker-session", "db": "/private/workspace", "request_id": "DO-NOT-SEND",
                })),
            ])
            db.executemany("INSERT INTO task_comments VALUES (?,?,?,?,?)", [
                (1, "t_main", "reviewer", "Long comments retain\nparagraph breaks.", 160),
                (2, "t_main", "reviewer", "System prompt: DO-NOT-SEND", 174),
            ])
        db.close()
        before = path.read_bytes()

        task = self.detail("default:t_main")

        self.assertEqual(task["description"], description)
        self.assertEqual(task["criteria"], contract)
        self.assertEqual(task["assignee"], "assigned")
        self.assertEqual(task["executor"], "actual-worker")
        self.assertEqual(task["activity"]["latestEvent"]["summary"], "Inspecting the build")
        self.assertEqual(task["activity"]["heartbeatAt"], "1970-01-01T00:02:50+00:00")
        self.assertEqual(task["updatedAt"], "1970-01-01T00:02:54+00:00")
        self.assertEqual(task["dependsOn"], [{"id": "default:t_parent", "title": "Parent", "status": "archived", "available": True}])
        self.assertEqual(task["dependents"][0]["id"], "default:t_descendant")
        self.assertEqual(self.detail("default:t_descendant")["blockedReason"], "Waiting for the build")
        self.assertEqual(task["comments"][0]["body"], "[redacted]")
        self.assertEqual(task["comments"][1]["body"], "Long comments retain\nparagraph breaks.")
        self.assertEqual(task["events"][0]["details"]["changed_fields"], ["title"])
        self.assertEqual(task["attempts"][1]["error"], "[redacted]")
        self.assertIsNone(task["result"])
        self.assertEqual(task["attempts"][1]["summary"], "Previous run result")
        self.assertEqual([item["evidence"] for item in task["links"]],
                         ["originatingSession", "workerSession", "completionAcceptance", "requiredByTask"])
        self.assertEqual(task["links"][1]["runId"], 2)
        for private in ("/private/workspace", "DO-NOT-SEND", "pb-unverified", "fixture-private"):
            self.assertNotIn(private, json.dumps(task))
        self.assertEqual(before, path.read_bytes())

    def test_detail_scopes_boards_and_marks_limits_without_partial_text(self):
        archived = self.home / "kanban" / "boards" / "retired"
        archived.mkdir(parents=True)
        with sqlite3.connect(archived / "kanban.db") as db:
            db.executescript(TASK_SCHEMA)
            db.execute("INSERT INTO tasks (id,title,status) VALUES ('t_main','Archived board','done')")
        db.close()
        (archived / "board.json").write_text('{"archived":true}', encoding="utf-8")
        with sqlite3.connect(self.home / "kanban.db") as db:
            db.executescript(TASK_SCHEMA + """
                CREATE TABLE task_comments (id INTEGER PRIMARY KEY, task_id TEXT, author TEXT,
                                            body TEXT, created_at INTEGER);
                INSERT INTO task_links VALUES ('t_missing','t_main');
            """)
            db.execute("INSERT INTO tasks (id,title,body,status) VALUES ('t_main','Current board',?,'ready')",
                       ("x" * BODY_LIMIT_BYTES + " token: FIXTURE",))
            db.executemany("INSERT INTO task_comments VALUES (?, 't_main', 'human', ?, ?)",
                           [(n, str(n), n) for n in range(1, MAX_DETAIL_COMMENTS + 2)])
        db.close()

        task = self.detail("default:t_main")

        self.assertEqual(task["title"], "Current board")
        self.assertEqual(task["limits"], ["body", "comments"])
        self.assertTrue(task["description"].startswith("[omitted:"))
        self.assertEqual(len(task["comments"]), MAX_DETAIL_COMMENTS)
        self.assertFalse(task["dependsOn"][0]["available"])
        self.assertIsNone(self.detail("retired:t_main"))
        self.assertIsNone(self.detail("default:../../state.db"))
        self.assertEqual([card["id"] for card in self.collect()[0]], ["default:t_main"])

    def test_dense_history_fits_response_budget_and_preserves_full_description(self):
        path = self.home / "kanban.db"
        description = "a" * BODY_LIMIT_BYTES
        with sqlite3.connect(path) as db:
            db.executescript(TASK_SCHEMA + """
                ALTER TABLE task_events ADD COLUMN kind TEXT;
                CREATE TABLE task_comments (id INTEGER PRIMARY KEY, task_id TEXT, author TEXT,
                                            body TEXT, created_at INTEGER);
            """)
            db.execute("INSERT INTO tasks (id,title,body,status) VALUES ('t_main','Main',?,'ready')", (description,))
            db.executemany("INSERT INTO task_comments VALUES (?, 't_main', 'human', ?, ?)",
                           [(n, "a" * NOTE_LIMIT_BYTES, n) for n in range(1, MAX_DETAIL_COMMENTS + 1)])
            db.executemany("INSERT INTO task_events VALUES (?, 't_main', ?, ?, 'edited')",
                           [(n, n, json.dumps({"summary": "a" * 22000})) for n in range(1, MAX_DETAIL_EVENTS + 1)])
        db.close()

        task = self.detail("default:t_main")

        self.assertEqual(task["description"], description)
        self.assertIn("events", task["limits"])
        self.assertEqual(task["events"][0]["id"], MAX_DETAIL_EVENTS)
        self.assertLessEqual(len(json.dumps(task, ensure_ascii=False, separators=(",", ":")).encode()), MAX_TASK_DETAIL_BYTES)
