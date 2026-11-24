"""HCC-owned local telemetry. All attributed usage is dated by observation."""

import hashlib
import json
import math
import os
import sqlite3
import stat
import threading
import time
from contextlib import closing
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path


COUNTERS = ("input", "output", "cached", "reasoning", "requests")
GPU_FIELDS = ("utilization", "memoryUsed", "temperature", "power")
ENGINE_FIELDS = ("throughput", "ttftMs", "kvCache", "running", "waiting")
# Range duration and display bucket duration, in seconds.
RANGES = {"1h": (3600, 60), "24h": (86400, 900), "7d": (604800, 7200), "30d": (2592000, 21600)}
RETENTION_DAYS = 31
SAMPLE_SECONDS = 60
MAX_GAP_SECONDS = 180
MAX_TRACKED = 8192
MAX_INPUT_ROWS = 4096
MAX_GPUS = 16
DB_PAGES = 16384  # 64 MiB with 4 KiB pages. Rollback journal is transient.
DB_FILENAME = "history.sqlite3"


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat() if epoch is not None else None


def packed(value):
    return json.dumps(value, allow_nan=False, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(packed(value).encode("utf-8")).digest()[:20]


def counter(value):
    return value if type(value) is int and 0 <= value <= 10**15 else None


def gauge(value):
    return value if type(value) in (int, float) and 0 <= value <= 10**15 and math.isfinite(value) else None


def empty_totals():
    return dict.fromkeys(COUNTERS)


def add(target, field, value):
    if value is not None:
        target[field] = (target[field] or 0) + value


def safe_path(path, directory=False):
    info = path.lstat()
    valid = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode) and info.st_nlink == 1
    if not valid or (os.name == "posix" and (info.st_uid != os.geteuid() or info.st_mode & 0o077)):
        raise PermissionError("HCC history requires private, owner-only storage")


class HistoryStore:
    def __init__(self, directory):
        directory = Path(directory).absolute()
        if directory.parent != directory.parent.resolve():
            raise PermissionError("HCC history refuses linked parent directories")
        safe_path(directory.parent, directory=True)
        directory.mkdir(mode=0o700, exist_ok=True)
        safe_path(directory, directory=True)
        self.path = directory / DB_FILENAME
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
        safe_path(self.path)
        for suffix in ("-journal", "-shm", "-wal"):
            auxiliary = Path(str(self.path) + suffix)
            try:
                safe_path(auxiliary)
            except FileNotFoundError:
                pass
        with closing(sqlite3.connect(self.path, timeout=2)) as db:
            db.execute("PRAGMA temp_store=MEMORY")
            db.execute("PRAGMA page_size=4096")
            if db.execute("PRAGMA page_size").fetchone()[0] != 4096:
                raise ValueError("Unexpected HCC history page size")
            db.execute("PRAGMA journal_mode=DELETE")
            self._bound(db)
            db.executescript("""
                CREATE TABLE IF NOT EXISTS metadata (
                    id INTEGER PRIMARY KEY CHECK (id = 1), state TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS checkpoint (
                    session BLOB NOT NULL, series BLOB NOT NULL, counters TEXT NOT NULL,
                    missing_since TEXT NOT NULL, last_present REAL,
                    PRIMARY KEY (session, series)
                ) WITHOUT ROWID;
                CREATE TABLE IF NOT EXISTS measurements (
                    minute INTEGER PRIMARY KEY, observed REAL NOT NULL, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS increments (
                    observed REAL PRIMARY KEY, input INTEGER, output INTEGER, cached INTEGER,
                    reasoning INTEGER, requests INTEGER
                ) WITHOUT ROWID;
            """)

    @staticmethod
    def _bound(db):
        pages = db.execute(f"PRAGMA max_page_count={DB_PAGES}").fetchone()[0]
        if pages > DB_PAGES:
            raise ValueError("HCC history has exceeded its size budget")

    @staticmethod
    def _state(db):
        row = db.execute("SELECT state FROM metadata WHERE id=1").fetchone()
        if row:
            return json.loads(row[0])
        return {"startedAt": None, "lastAt": None, "baselineAt": None, "baseline": empty_totals(),
                "unplaced": empty_totals(), "counterResets": 0, "scopeChanges": 0,
                "limitedObservations": 0, "conflictingSeries": 0, "sizePrunedBefore": None}

    @staticmethod
    def _prune(db, state, at):
        cutoff = at - RETENTION_DAYS * 86400
        occupied = db.execute("PRAGMA page_count").fetchone()[0] - db.execute("PRAGMA freelist_count").fetchone()[0]
        if occupied >= DB_PAGES * 0.90:
            # Free the oldest tenth before reaching SQLite's absolute limit. The
            # database reuses the freed pages, without a temporary VACUUM copy.
            oldest = db.execute("SELECT MIN(minute) FROM measurements").fetchone()[0]
            if oldest is not None:
                size_cutoff = oldest * SAMPLE_SECONDS + max(SAMPLE_SECONDS, (at - oldest * SAMPLE_SECONDS) / 10)
                if size_cutoff > cutoff:
                    cutoff = size_cutoff
                    state["sizePrunedBefore"] = cutoff
        db.execute("DELETE FROM measurements WHERE minute < ?", (int(cutoff // SAMPLE_SECONDS),))
        db.execute("DELETE FROM increments WHERE observed < ?", (cutoff,))
        db.execute("DELETE FROM checkpoint WHERE last_present < ?", (cutoff,))

    @staticmethod
    def _rows(usage, state):
        sessions = {}
        for index, row in enumerate(islice(usage, MAX_INPUT_ROWS + 1)):
            if index == MAX_INPUT_ROWS:
                state["limitedObservations"] += 1
                break
            agent = row.get("agent")
            if not isinstance(agent, str) or not 0 < len(agent) <= 256:
                continue
            route = row.get("counterScope") == "route"
            key = digest(row.get("counterKey") or [row.get("model"), row.get("task")]) if route else b""
            values = [counter(row.get(field)) for field in COUNTERS]
            if all(value is None for value in values):
                continue
            group = sessions.setdefault(digest(agent), {})
            birth = gauge(row.get("counterCreatedAt"))
            observation = (values, birth)
            if key in group and group[key] != observation:
                if group[key] is not None:
                    state["conflictingSeries"] += 1
                group[key] = None  # disagreeing copies have no authoritative value
            else:
                group[key] = observation
        for group in sessions.values():
            if len(group) > 1:
                group.pop(b"", None)  # route data overlaps the rolled-up session
        return sessions

    @staticmethod
    def _account(db, state, at, sessions):
        previous = {}
        for session, key, values, missing, last_present in db.execute("SELECT * FROM checkpoint"):
            previous.setdefault(session, {})[key] = (values, missing, last_present)
        tracked = sum(len(group) for group in previous.values())
        increments = empty_totals()
        initial = state["baselineAt"] is None
        capacity_limited = False
        for session in previous.keys() | sessions.keys():
            old = previous.get(session, {})
            new = sessions.get(session, {})
            scope_changed = bool(new and old and ((b"" in new) != (b"" in old)))
            if scope_changed:
                db.execute("DELETE FROM checkpoint WHERE session=?", (session,))
                tracked -= len(old)
                old = {}
                state["scopeChanges"] += 1
            for key in old.keys() | new.keys():
                current = new.get(key)
                former = old.get(key)
                if current is None:
                    if former and former[2] is None:
                        db.execute("UPDATE checkpoint SET last_present=? WHERE session=? AND series=?",
                                   (state["lastAt"], session, key))
                    continue
                current, birth = current
                if former is None:
                    if tracked >= MAX_TRACKED:
                        capacity_limited = True
                        continue
                    tracked += 1
                    if initial:
                        for field, value in zip(COUNTERS, current):
                            add(state["baseline"], field, value)
                    elif not scope_changed and birth is not None and state["lastAt"] < birth <= at:
                        target = state["unplaced"] if at - state["lastAt"] > MAX_GAP_SECONDS else increments
                        for field, value in zip(COUNTERS, current):
                            add(target, field, value)
                    db.execute("INSERT INTO checkpoint VALUES (?,?,?,?,NULL)",
                               (session, key, packed(current), packed([None] * len(COUNTERS))))
                    continue
                values, missing = json.loads(former[0]), json.loads(former[1])
                last_present = former[2] if former[2] is not None else state["lastAt"]
                for i, (field, value) in enumerate(zip(COUNTERS, current)):
                    if value is None:
                        if values[i] is not None and missing[i] is None:
                            missing[i] = last_present
                        continue
                    if values[i] is not None:
                        if value < values[i]:
                            state["counterResets"] += 1
                        else:
                            last_seen = missing[i] if missing[i] is not None else last_present
                            target = state["unplaced"] if at - last_seen > MAX_GAP_SECONDS else increments
                            add(target, field, value - values[i])
                    values[i], missing[i] = value, None
                encoded = packed(values), packed(missing)
                if encoded != former[:2] or former[2] is not None:
                    db.execute("UPDATE checkpoint SET counters=?, missing_since=?, last_present=NULL "
                               "WHERE session=? AND series=?", (*encoded, session, key))
        if initial and any(value is not None for value in state["baseline"].values()):
            state["baselineAt"] = at
        if capacity_limited:
            state["limitedObservations"] += 1
        if any(value is not None for value in increments.values()):
            db.execute("INSERT INTO increments VALUES (?,?,?,?,?,?)", (at, *increments.values()))

    def record(self, observed_epoch, usage_rows, gpus, metrics):
        """Atomically commit one scrape. Replays and backward clock steps are ignored."""
        at = float(observed_epoch)
        if not math.isfinite(at) or not 0 <= at < 253402300799:
            raise ValueError("Invalid observation time")
        with closing(sqlite3.connect(self.path, timeout=2)) as db, db:
            db.execute("PRAGMA temp_store=MEMORY")
            self._bound(db)
            db.execute("BEGIN IMMEDIATE")
            state = self._state(db)
            last = state["lastAt"]
            if last is not None and at <= last:
                return False
            if last is None or int(at // 3600) != int(last // 3600):
                self._prune(db, state, at)
            sessions = self._rows(usage_rows, state)
            self._account(db, state, at, sessions)
            devices = {}
            for index, gpu in enumerate(islice(gpus, MAX_GPUS + 1)):
                if index == MAX_GPUS:
                    state["limitedObservations"] += 1
                    break
                identity = gpu.get("id")
                if isinstance(identity, str) and len(identity) <= 5 and identity.isascii() and identity.isdecimal():
                    devices[identity] = [gauge(gpu.get(field)) for field in GPU_FIELDS]
            payload = [devices, [gauge((metrics or {}).get(field)) for field in ENGINE_FIELDS]]
            db.execute("INSERT OR REPLACE INTO measurements VALUES (?,?,?)",
                       (int(at // SAMPLE_SECONDS), at, packed(payload)))
            if state["startedAt"] is None:
                state["startedAt"] = at
            state["lastAt"] = at
            db.execute("INSERT OR REPLACE INTO metadata VALUES (1,?)", (packed(state),))
        return True

    def read(self, range_name="24h", now=None):
        duration, step = RANGES[range_name]
        end = time.time() if now is None else now
        start = end - duration
        with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
            db.execute("PRAGMA temp_store=MEMORY")
            db.execute("BEGIN")
            state = self._state(db)
            usage = [{
                "at": iso(row[0]), **dict(zip(COUNTERS, row[1:]))
            } for row in db.execute(
                "SELECT MAX(observed), SUM(input), SUM(output), SUM(cached), SUM(reasoning), SUM(requests) "
                "FROM increments WHERE observed > ? AND observed <= ? GROUP BY CAST(observed / ? AS INTEGER) "
                "ORDER BY MAX(observed)", (start, end, step))]
            samples = [(iso(at), *json.loads(payload)) for at, payload in db.execute("""
                SELECT observed, payload FROM measurements WHERE minute IN (
                    SELECT MAX(minute) FROM measurements WHERE minute BETWEEN ? AND ?
                    AND observed > ? AND observed <= ? GROUP BY CAST(minute / ? AS INTEGER)
                ) ORDER BY minute
            """, (int(start // SAMPLE_SECONDS), int(end // SAMPLE_SECONDS), start, end, step // SAMPLE_SECONDS))]
        # Prefer recently seen device IDs even when devices changed over the month.
        identities = dict.fromkeys(device for _, values, _ in reversed(samples) for device in values)
        devices = {device: [] for device in islice(identities, MAX_GPUS)}
        engine = []
        for stamp, values, gauges in samples:
            engine.append({"at": stamp, **dict(zip(ENGINE_FIELDS, gauges))})
            for device, points in devices.items():
                readings = values.get(device, [None] * len(GPU_FIELDS))
                points.append({"at": stamp, **dict(zip(GPU_FIELDS, readings))})
        return {
            "range": range_name, **{field: iso(state[field]) for field in ("startedAt", "lastAt", "baselineAt")},
            "baseline": state["baseline"], "unplaced": state["unplaced"], "usage": usage,
            "gpus": devices, "engine": engine,
            "coverage": {"retentionDays": RETENTION_DAYS, "sampleSeconds": SAMPLE_SECONDS, "bucketSeconds": step,
                         "maxGapSeconds": MAX_GAP_SECONDS,
                         "sizePrunedBefore": iso(state.get("sizePrunedBefore")),
                         "omittedGpuSeries": max(0, len(identities) - MAX_GPUS),
                         **{key: state[key] for key in ("counterResets", "scopeChanges", "limitedObservations",
                                                        "conflictingSeries")}},
        }


class BackgroundCollector:
    """Run one collection at a time. Wait between polls and retry after collection errors."""

    def __init__(self, callback, interval=15):
        if not math.isfinite(interval) or interval <= 0:
            raise ValueError("Invalid HCC collection interval")
        self.callback, self.interval = callback, interval
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self._run, name="hcc-history", daemon=True)
        self.last_error = None

    def start(self):
        self.thread.start()

    def _run(self):
        while not self.stopping.is_set():
            try:
                self.callback()
                self.last_error = None
            except Exception as error:
                # Preserve diagnostics without logging paths, source data or exception text.
                self.last_error = type(error).__name__
            self.stopping.wait(self.interval)

    def close(self):
        self.stopping.set()
        if self.thread.is_alive():
            self.thread.join()
