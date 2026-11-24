import json
import os
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import history
from history import BackgroundCollector, HistoryStore


def row(count, *, session="p1:s", scope="route", key="a", requests=None, output=None):
    return {"agent": session, "session": "shared-display-id", "counterScope": scope, "counterKey": key,
            "input": count, "output": output, "requests": requests, "at": "2018-06-05T00:00:00Z"}


def sum_field(report, field):
    return sum(point[field] for point in report["usage"] if point[field] is not None)


class HistoryTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name) / "history"
        self.store = HistoryStore(self.directory)
        self.at = 1735689600

    def record(self, offset, *rows, gpus=(), metrics=None):
        return self.store.record(self.at + offset, rows, gpus, metrics)

    def read(self, offset=3600, window="1h"):
        return self.store.read(window, self.at + offset)

    def test_short_new_session_is_counted_with_proven_birth(self):
        self.record(0, row(100))
        self.record(30, row(100), {**row(30,session="short"), "counterCreatedAt":self.at+5},
                    {**row(500,session="unseen"), "counterCreatedAt":self.at-100})
        self.assertEqual(30, sum_field(self.read(), "input"))

    def test_first_baseline_is_undated_and_delta_is_observation_time(self):
        self.record(0)
        self.record(10, row(100, requests=1), row(50, session="p2:s"))
        self.record(20, row(105, requests=2), row(53, session="p2:s"), row(105, requests=2))
        self.assertFalse(self.record(20, row(300)))
        self.assertFalse(self.record(19, row(400)))
        self.store = HistoryStore(self.directory)
        self.record(35, row(106, requests=2), row(53, session="p2:s"), row(800, key="new-route"))
        report = self.read()
        self.assertEqual(report["baseline"], {"input": 150, "output": None, "cached": None,
                                              "reasoning": None, "requests": 1})
        self.assertEqual(report["baselineAt"], history.iso(self.at + 10))
        self.assertEqual(report["usage"], [{"at": history.iso(self.at + 35), "input": 9, "output": None,
                                            "cached": None, "reasoning": None, "requests": 1}])
        self.assertEqual(report["startedAt"], history.iso(self.at))
        self.assertEqual(report["lastAt"], history.iso(self.at + 35))
        self.assertNotIn("shared-display-id", self.store.path.read_bytes().decode("latin1"))

    def test_scope_transitions_and_independent_counter_resets(self):
        self.record(0, row(100, scope="session", requests=8))
        self.record(10, row(120, key="r1", requests=5), row(30, key="r2", requests=5),
                    row(150, scope="session", requests=10))
        self.record(20, row(3, key="r1", requests=6), row(31, key="r2", requests=5))
        self.record(30, row(4, key="r1", requests=6), row(33, key="r2", requests=5))
        self.record(40, row(500, scope="session", requests=50))
        self.record(50, row(503, scope="session", key="changed-model", requests=51))
        report = self.read()
        self.assertEqual(sum_field(report, "input"), 7)
        self.assertEqual(sum_field(report, "requests"), 2)
        self.assertEqual(report["baseline"]["input"], 100)
        self.assertEqual(report["coverage"]["scopeChanges"], 2)
        self.assertEqual(report["coverage"]["counterResets"], 1)

    def test_disagreeing_duplicates_and_missing_fields(self):
        self.record(0, row(100, output=4))
        self.record(10, row(200), row(250))
        self.record(20, row(105))
        self.record(30, row(107, output=8))
        report = self.read()
        self.assertEqual(report["coverage"]["conflictingSeries"], 1)
        self.assertEqual(sum_field(report, "input"), 7)
        self.assertEqual(sum_field(report, "output"), 4)
        self.assertIsNone(report["usage"][0]["cached"])

    def test_service_outage_and_individual_counter_gap_have_separate_totals(self):
        self.record(0, row(100, output=2), row(50, session="p2:s"))
        self.record(60, row(101))
        self.record(120, row(102))
        self.record(240, row(103, output=10), row(80, session="p2:s"))
        self.store = HistoryStore(self.directory)
        self.record(1000, row(500, output=40))
        report = self.read()
        self.assertEqual(sum_field(report, "input"), 3)
        self.assertEqual(report["unplaced"]["input"], 427)
        self.assertEqual(report["unplaced"]["output"], 38)
        self.assertIsNone(report["unplaced"]["cached"])

    def test_latest_real_sample_all_gpu_and_engine_metrics_and_unavailable_gaps(self):
        gpus = [{"id": str(i), "utilization": i * 4, "memoryUsed": 999, "temperature": 50, "power": 220}
                for i in range(4)]
        metrics = {"throughput": 34.5, "ttftMs": 130, "kvCache": 22, "running": 1, "waiting": 2}
        self.record(10, gpus=gpus, metrics={"throughput": 0})
        self.record(30, gpus=gpus, metrics=metrics)
        self.record(100)
        report = self.read()
        self.assertEqual(set(report["gpus"]), {"0", "1", "2", "3"})
        self.assertEqual(report["gpus"]["2"], [
            {"at": history.iso(self.at + 30), **{k: gpus[2][k] for k in history.GPU_FIELDS}},
            {"at": history.iso(self.at + 100), **dict.fromkeys(history.GPU_FIELDS)}])
        self.assertEqual(report["engine"], [
            {"at": history.iso(self.at + 30), **metrics},
            {"at": history.iso(self.at + 100), **dict.fromkeys(history.ENGINE_FIELDS)}])
        for window in history.RANGES:
            self.assertEqual(self.read(100, window)["range"], window)

    def test_filters_at_actual_observation_and_honors_retention(self):
        self.record(0, row(10))
        self.record(10, row(12))
        self.record(30, row(14))
        self.assertEqual(sum_field(self.read(3615), "input"), 2)
        far = 33 * 86400
        self.record(far)
        with closing(sqlite3.connect(self.store.path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM increments").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM measurements").fetchone()[0], 1)
        self.assertEqual(self.read(far, "30d")["baseline"]["input"], 10)

    def test_capacity_limit_repeated_rows_do_not_add_to_baseline(self):
        with patch.object(history, "MAX_TRACKED", 1):
            self.record(0, row(10))
            self.record(10, row(12), row(900, session="p2:s"))
            self.record(20, row(13), row(905, session="p2:s"))
        report = self.read()
        self.assertEqual(sum_field(report, "input"), 3)
        self.assertEqual(report["baseline"]["input"], 10)
        self.assertEqual(report["coverage"]["limitedObservations"], 2)

    def test_disk_pressure_shortens_retention_and_keeps_checkpoints(self):
        self.record(0, row(10))
        for i in range(1, 31):
            self.record(i * 60, row(10 + i))
        with closing(sqlite3.connect(self.store.path)) as db:
            used = db.execute("PRAGMA page_count").fetchone()[0]
        with patch.object(history, "DB_PAGES", used):
            self.record(3600, row(41))
        report = self.read(3600)
        self.assertIsNotNone(report["coverage"]["sizePrunedBefore"])
        self.assertEqual(report["baseline"]["input"], 10)
        self.assertGreater(report["unplaced"]["input"], 0)
        self.assertLess(sum_field(report, "input"), 30)

    def test_monthly_response_bounded_under_gpu_churn(self):
        for i in range(121):
            gpus = [{"id": str(i * 16 + j), **dict.fromkeys(history.GPU_FIELDS, 1234.56789)}
                    for j in range(16)]
            self.record(i * 21600, gpus=gpus, metrics=dict.fromkeys(history.ENGINE_FIELDS, 7.2))
        report = self.read(30 * 86400, "30d")
        self.assertEqual(len(report["gpus"]), 16)
        self.assertEqual(len(report["engine"]), 120)
        self.assertGreater(report["coverage"]["omittedGpuSeries"], 0)
        self.assertLess(len(history.packed(report).encode("utf-8")), 500 * 1024)

    def test_failed_measurement_rolls_back_usage_and_can_retry(self):
        self.record(0, row(7))
        with self.assertRaises(AttributeError):
            self.record(10, row(10), gpus=[None])
        self.record(10, row(10))
        self.assertEqual(sum_field(self.read(), "input"), 3)
        self.assertEqual(json.dumps(self.read(), allow_nan=False)[0], "{")

    @unittest.skipUnless(os.name == "posix", "uses POSIX ownership and file permissions")
    def test_private_storage_and_symlinks(self):
        self.assertEqual(self.directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)
        self.directory.chmod(0o755)
        with self.assertRaises(PermissionError):
            HistoryStore(self.directory)
        self.directory.chmod(0o700)
        link = self.directory.parent / "linked"
        link.symlink_to(self.directory, target_is_directory=True)
        with self.assertRaises(PermissionError):
            HistoryStore(link)
        journal = Path(str(self.store.path) + "-journal")
        journal.symlink_to(self.store.path)
        with self.assertRaises(PermissionError):
            HistoryStore(self.directory)

    def test_background_collects_without_requests_retries_and_closes(self):
        finished = threading.Event()
        calls = []

        def collect():
            calls.append(None)
            if len(calls) == 1:
                raise OSError("private path")
            self.record(len(calls), row(len(calls)))
            if len(calls) >= 3:
                finished.set()

        worker = BackgroundCollector(collect, 0.01)
        worker.start()
        try:
            self.assertTrue(finished.wait(3))
        finally:
            worker.close()
        self.assertFalse(worker.thread.is_alive())
        self.assertIsNone(worker.last_error)
        self.assertGreaterEqual(sum_field(self.read(), "input"), 1)


if __name__ == "__main__":
    unittest.main()
