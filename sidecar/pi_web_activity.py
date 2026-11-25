"""Observe Pi Web's local session daemon without opening or changing sessions."""

import base64
import hashlib
import json
import os
import socket
import stat
import struct
import sys
import threading
import time
from pathlib import Path

from telemetry import label, source, timestamp, utc


MAX_HEADER = 8 * 1024
MAX_MESSAGE = 256 * 1024
MAX_SESSIONS = 256
IO_TIMEOUT = 0.25
READ_DEADLINE = 2.0
RECONNECT_DELAY = 1.0
ACTIVITY_TTL = 75.0
WEBSOCKET_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
START_ACTIVITY = "agent running"
RUNNING_ACTIVITY = {START_ACTIVITY, "message started", "receiving response", "running tool", "running bash"}


class _EventStream:
    def __init__(self, connection, stopped):
        self.connection = connection
        self.stopped = stopped
        self.buffer = bytearray()

    def _receive(self, deadline):
        while not self.stopped.is_set():
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError
            try:
                data = self.connection.recv(4096)
            except socket.timeout:
                continue
            if not data:
                raise ConnectionError
            self.buffer.extend(data)
            return
        raise ConnectionError

    def _take(self, size, deadline):
        while len(self.buffer) < size:
            self._receive(deadline)
        data = bytes(self.buffer[:size])
        del self.buffer[:size]
        return data

    def upgrade(self):
        key = base64.b64encode(os.urandom(16))
        request = (b"GET /events HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n"
                   b"Connection: Upgrade\r\nSec-WebSocket-Version: 13\r\n"
                   b"Sec-WebSocket-Key: " + key + b"\r\n\r\n")
        self.connection.sendall(request)
        deadline = time.monotonic() + READ_DEADLINE
        end = self.buffer.find(b"\r\n\r\n")
        while end < 0 and len(self.buffer) < MAX_HEADER:
            self._receive(deadline)
            end = self.buffer.find(b"\r\n\r\n")
        if end < 0 or end + 4 > MAX_HEADER:
            raise ValueError("Invalid WebSocket upgrade header")
        response = self._take(end + 4, deadline)[:-4].split(b"\r\n")
        if response[0].split()[:2] != [b"HTTP/1.1", b"101"]:
            raise ValueError("WebSocket upgrade rejected")
        headers = {}
        for line in response[1:]:
            name, separator, value = line.partition(b":")
            name = name.lower()
            if not separator or name in headers:
                raise ValueError("Invalid WebSocket upgrade header")
            headers[name] = value.strip()
        connection_tokens = headers.get(b"connection", b"").lower().split(b",")
        expected = base64.b64encode(hashlib.sha1(key + WEBSOCKET_GUID, usedforsecurity=False).digest())
        if (b"upgrade" not in {token.strip() for token in connection_tokens}
                or headers.get(b"upgrade", b"").lower() != b"websocket"
                or headers.get(b"sec-websocket-accept") != expected
                or b"sec-websocket-extensions" in headers or b"sec-websocket-protocol" in headers):
            raise ValueError("Invalid WebSocket upgrade")

    def _frame(self, message_deadline):
        first = self._take(1, message_deadline)[0]
        deadline = message_deadline or time.monotonic() + READ_DEADLINE
        size = self._take(1, deadline)[0]
        opcode, final = first & 0x0F, bool(first & 0x80)
        if first & 0x70 or size & 0x80 or opcode not in (0, 1, 8, 9, 10):
            raise ValueError("Invalid WebSocket frame")
        if opcode >= 8 and (not final or size > 125):
            raise ValueError("Invalid WebSocket control frame")
        if size in (126, 127):
            width = 2 if size == 126 else 8
            size = int.from_bytes(self._take(width, deadline), "big")
            if size < (126 if width == 2 else 65536):
                raise ValueError("Invalid WebSocket frame size")
        if size > MAX_MESSAGE:
            raise ValueError("WebSocket frame exceeds limit")
        return opcode, final, self._take(size, deadline)

    def message(self):
        fragments = None
        deadline = None
        while not self.stopped.is_set():
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError
            opcode, final, payload = self._frame(deadline)
            if opcode == 8:
                raise ConnectionError
            if opcode == 9:
                mask = os.urandom(4)
                masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
                self.connection.sendall(bytes((0x8A, 0x80 | len(payload))) + mask + masked)
            elif opcode == 1 and fragments is None:
                if final:
                    return payload
                fragments = bytearray(payload)
                deadline = time.monotonic() + READ_DEADLINE
            elif opcode == 0 and fragments is not None:
                if len(fragments) + len(payload) > MAX_MESSAGE:
                    raise ValueError("WebSocket message exceeds limit")
                fragments.extend(payload)
                if final:
                    return fragments
            elif opcode != 10:
                raise ValueError("Invalid WebSocket continuation")
        raise ConnectionError


class PiWebActivity:
    def __init__(self, socket_path):
        self._path = Path(socket_path) if socket_path else None
        self._supported = (sys.platform == "linux" and hasattr(socket, "AF_UNIX")
                           and hasattr(socket, "SO_PEERCRED"))
        self._stopped = threading.Event()
        self._lock = threading.Lock()
        self._worker = None
        self._sessions = {}
        self._idle_sessions = set()
        self._idle_baseline = False
        self._working = None
        self._limited = False

    def start(self):
        with self._lock:
            if self._path is None or not self._supported or self._stopped.is_set() or self._worker is not None:
                return
            self._worker = threading.Thread(target=self._run, name="pi-web-activity", daemon=True)
            self._worker.start()

    def close(self):
        self._stopped.set()
        self._reset()
        if self._worker is not None:
            self._worker.join(timeout=IO_TIMEOUT * 4)

    def snapshot(self):
        with self._lock:
            self._expire_sessions()
            sessions = {key: {field: state[field] for field in ("status", "startedAt", "updatedAt")}
                        for key, state in self._sessions.items()}
            working = self._working
            idle_baseline = self._idle_baseline
            limited = self._limited
        available = working is not None
        if not available:
            if self._path is None:
                detail = "Pi Web activity observation is disabled."
            elif not self._supported:
                detail = "Pi Web activity observation requires a local Linux session daemon."
            else:
                detail = "Pi Web has not reported a current machine state."
            status = "unavailable"
        elif limited or (working and (not idle_baseline or not sessions)):
            status = "partial"
            detail = ("Pi Web exceeded the session activity collection limit." if limited else
                      "Pi Web reports work in progress. Session identities require live activity events.")
        else:
            status = "available"
            detail = f"Pi Web reported activity for {len(sessions)} observed sessions."
        diagnostic = source("pi-web-activity", "Pi Web activity", status, detail)
        diagnostic.update(runtimeAvailable=available, working=working)
        return sessions, diagnostic

    def _reset(self):
        with self._lock:
            self._working = None
            self._limited = False
            self._sessions.clear()
            self._idle_sessions.clear()
            self._idle_baseline = False

    def _run(self):
        while not self._stopped.is_set():
            try:
                self._listen()
            except (OSError, ValueError, RecursionError):
                pass
            finally:
                self._reset()
            self._stopped.wait(RECONNECT_DELAY)

    def _listen(self):
        if not self._path.is_absolute():
            raise ValueError("Invalid Pi Web socket path")
        uid = os.getuid()
        info = self._path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != uid:
            raise ValueError("Untrusted Pi Web socket")
        if self._stopped.is_set():
            return
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(IO_TIMEOUT)
            connection.connect(os.fspath(self._path))
            credentials = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("iII"))
            _, peer_uid, _ = struct.unpack("iII", credentials)
            if peer_uid != uid:
                raise ValueError("Untrusted Pi Web socket peer")
            if self._stopped.is_set():
                return
            stream = _EventStream(connection, self._stopped)
            stream.upgrade()
            while not self._stopped.is_set():
                self._consume(stream.message())

    def _consume(self, payload):
        message = json.loads(payload.decode("utf-8"))
        if not isinstance(message, dict):
            return
        kind = message.get("type")
        with self._lock:
            if self._stopped.is_set():
                return
            self._expire_sessions()
            if kind == "machine.status":
                self._machine_status(message.get("status"))
            elif self._working is not None and kind == "activity.update":
                self._activity_update(message.get("activity"))
            elif self._working is not None and kind == "status.update":
                self._status_update(message.get("status"))

    def _machine_status(self, status):
        machine = status.get("machine") if isinstance(status, dict) else None
        # Pi Web omits inactive flags from its machine rollup.
        working = machine.get("working", False) if isinstance(machine, dict) else None
        if isinstance(working, bool):
            self._working = working
            if not working:
                self._idle_baseline = True
                self._sessions = {key: state for key, state in self._sessions.items() if state["status"] == "queued"}

    def _set_idle(self, key):
        self._sessions.pop(key, None)
        if len(self._idle_sessions) < MAX_SESSIONS:
            self._idle_sessions.add(key)

    def _expire_sessions(self):
        cutoff = time.monotonic() - ACTIVITY_TTL
        expired = [key for key, state in self._sessions.items() if state["observed"] <= cutoff]
        for key in expired:
            self._sessions.pop(key)
            self._idle_sessions.discard(key)
        if expired:
            # A missed terminal update breaks the idle baseline. The next repeat
            # of an active label can confirm work but cannot recover its start.
            self._idle_baseline = False

    def _activity_update(self, activity):
        if not isinstance(activity, dict) or activity.get("startup") is True:
            return
        key = label(activity.get("sessionId"))
        if key is None:
            return
        phase, action = activity.get("phase"), activity.get("label")
        if phase == "idle":
            self._set_idle(key)
        elif phase == "error":
            self._sessions.pop(key, None)
        elif phase == "active" and isinstance(action, str) and action in RUNNING_ACTIVITY:
            raw_time = activity.get("at")
            at = utc(timestamp(raw_time)) if isinstance(raw_time, str) and raw_time.endswith("Z") else None
            previous = self._sessions.get(key, {})
            # Heartbeats repeat the current activity label during an existing turn.
            observed_idle = key in self._idle_sessions or self._idle_baseline
            turn_start = action == START_ACTIVITY and observed_idle and previous.get("status") != "running"
            self._set_session(key, "running", at, at if turn_start else None)

    def _status_update(self, status):
        if not isinstance(status, dict):
            return
        key = label(status.get("sessionId"))
        if key is None:
            return
        flags = [status.get(field) for field in ("isStreaming", "isCompacting", "isBashRunning")]
        pending = status.get("pendingMessageCount")
        if any(flag is True for flag in flags):
            self._set_session(key, "running")
        elif type(pending) is int and pending > 0:
            self._set_session(key, "queued")
        elif all(flag is False for flag in flags) and type(pending) is int and pending == 0:
            self._set_idle(key)

    def _set_session(self, key, status, at=None, started=None):
        if status == "running":
            self._idle_sessions.discard(key)
        previous = self._sessions.get(key)
        if previous is None and len(self._sessions) >= MAX_SESSIONS:
            self._limited = True
            return
        if previous is None or previous["status"] != status:
            previous = {}
        self._sessions[key] = {
            "status": status,
            "startedAt": started or previous.get("startedAt"),
            "updatedAt": at or previous.get("updatedAt"),
            "observed": time.monotonic(),
        }
