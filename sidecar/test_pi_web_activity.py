import base64
import hashlib
import json
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from pi_web_activity import MAX_MESSAGE, PiWebActivity, _EventStream


AT = "2026-11-25T12:00:00.000Z"
NEXT_AT = "2026-11-25T12:00:01.000Z"


def machine(working):
    return {"type": "machine.status", "status": {"machine": {"working": working}}}


def activity(key, action="agent running", *, at=AT, phase="active", **extra):
    return {"type": "activity.update", "activity": {
        "sessionId": key, "phase": phase, "label": action, "at": at, **extra,
    }}


def status(key, *, streaming=False, compacting=False, bash=False, pending=0):
    return {"type": "status.update", "status": {
        "sessionId": key, "isStreaming": streaming, "isCompacting": compacting,
        "isBashRunning": bash, "pendingMessageCount": pending,
    }}


class PiWebActivityTest(unittest.TestCase):
    def setUp(self):
        self.monitor = PiWebActivity("/fake/sessiond.sock")
        self.addCleanup(self.monitor.close)

    def consume(self, *events):
        for event in events:
            self.monitor._consume(json.dumps(event).encode())

    def test_initial_busy_has_no_session_identity_and_startup_is_ignored(self):
        startup = {**activity("opening-session"), "type": "session.startup"}
        self.consume(status("pre-snapshot", streaming=True), machine(True), startup,
                     activity("startup-marker", startup=True), activity("opening-label", "opening session"),
                     activity("private/path"), status("idle", pending=True))
        records, info = self.monitor.snapshot()
        self.assertEqual({}, records)
        self.assertEqual("pi-web-activity", info["id"])
        self.assertEqual(("partial", True, True), (info["status"], info["runtimeAvailable"], info["working"]))

    def test_native_idle_rollup_omits_working_flag(self):
        for flags in ({}, {"unread": True}):
            self.consume({"type": "machine.status", "status": {"machine": flags}})
            records, info = self.monitor.snapshot()
            self.assertEqual({}, records)
            self.assertEqual(("available", True, False), (info["status"], info["runtimeAvailable"], info["working"]))
        self.consume(activity("after-idle"), machine(True))
        self.assertIsNotNone(self.monitor.snapshot()[0]["after-idle"]["startedAt"])

    def test_exact_activity_and_clocks_require_observed_session_events(self):
        self.consume(machine(True), activity("already-running", body="Private prompt", cwd="/private/path"),
                     status("compacting", compacting=True), activity("new-turn", "idle", phase="idle"),
                     activity("new-turn"), activity("new-turn", "receiving response", at=NEXT_AT))
        records, _ = self.monitor.snapshot()
        self.assertEqual({"already-running", "compacting", "new-turn"}, set(records))
        self.assertEqual({"status": "running", "startedAt": None, "updatedAt": "2026-11-25T12:00:00+00:00"},
                         records["already-running"])
        self.assertIsNone(records["compacting"]["updatedAt"])
        self.assertEqual("2026-11-25T12:00:00+00:00", records["new-turn"]["startedAt"])
        self.assertEqual("2026-11-25T12:00:01+00:00", records["new-turn"]["updatedAt"])
        self.assertNotIn("Private prompt", json.dumps(self.monitor.snapshot()))
        self.assertNotIn("/private/path", json.dumps(self.monitor.snapshot()))
        records["new-turn"]["startedAt"] = None
        self.assertIsNotNone(self.monitor.snapshot()[0]["new-turn"]["startedAt"])

    def test_terminal_flags_update_exact_session_and_machine_keeps_queue(self):
        self.consume(machine(False), machine(True), activity("bash"), status("pending", pending=1),
                     status("ended", bash=True), activity("ended", "error", phase="error"))
        records, _ = self.monitor.snapshot()
        self.assertEqual({"bash", "pending"}, set(records))
        self.assertIsNotNone(records["bash"]["startedAt"])
        self.assertEqual("queued", records["pending"]["status"])
        self.consume(machine(False))
        self.assertEqual({"pending"}, set(self.monitor.snapshot()[0]))
        self.consume(status("pending"))
        self.assertEqual({}, self.monitor.snapshot()[0])

    def test_active_heartbeat_renews_exact_identity_and_expiry_discards_old_start(self):
        with patch("pi_web_activity.time.monotonic", return_value=10) as monotonic:
            self.consume(machine(False), machine(True), activity("expired"), activity("retained"))
            self.assertIsNotNone(self.monitor.snapshot()[0]["expired"]["startedAt"])
            monotonic.return_value = 60
            self.consume(status("retained", streaming=True))
            monotonic.return_value = 90
            records, info = self.monitor.snapshot()
            self.assertEqual({"retained"}, set(records))
            self.assertTrue(info["working"])
            self.consume(activity("expired", at=NEXT_AT))
            records, _ = self.monitor.snapshot()
            self.assertEqual({"expired", "retained"}, set(records))
            self.assertIsNone(records["expired"]["startedAt"])
            self.assertIsNotNone(records["retained"]["startedAt"])

    def test_reconnect_clears_confirmed_activity_and_starts_with_empty_busy_state(self):
        observations = []

        def connection():
            observations.append(self.monitor.snapshot())
            self.consume(machine(True))
            if len(observations) == 1:
                self.consume(status("old-session", streaming=True))
                raise ConnectionError
            observations.append(self.monitor.snapshot())
            self.monitor.close()

        with patch.object(self.monitor, "_listen", side_effect=connection), patch("pi_web_activity.RECONNECT_DELAY", 0):
            self.monitor._run()
        self.assertEqual([{}, {}, {}], [records for records, _ in observations])
        self.assertEqual([False, False, True], [info["runtimeAvailable"] for _, info in observations])
        self.assertEqual([None, None, True], [info["working"] for _, info in observations])
        self.assertFalse(self.monitor.snapshot()[1]["runtimeAvailable"])


class FakeConnection:
    def __init__(self, frames):
        self.frames = frames
        self.outgoing = []
        self.incoming = b""

    def sendall(self, payload):
        self.outgoing.append(payload)
        if len(self.outgoing) == 1:
            self.incoming = upgrade_response(payload) + self.frames

    def recv(self, size):
        chunk, self.incoming = self.incoming[:size], self.incoming[size:]
        return chunk


def upgrade_response(request):
    key = request.split(b"Sec-WebSocket-Key: ", 1)[1].split(b"\r\n", 1)[0]
    accept = base64.b64encode(hashlib.sha1(key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest())
    return (b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            b"Sec-WebSocket-Accept: " + accept + b"\r\n\r\n")


class EventStreamTest(unittest.TestCase):
    def test_fixed_upgrade_and_fragmented_message_with_ping(self):
        connection = FakeConnection(b"\x01\x02{\"\x89\x01!\x80\x03a\"}")
        stream = _EventStream(connection, threading.Event())
        stream.upgrade()
        self.assertEqual(b'{"a"}', stream.message())
        request, pong = connection.outgoing
        self.assertTrue(request.startswith(b"GET /events HTTP/1.1\r\n"))
        self.assertTrue(request.endswith(b"\r\n\r\n"))
        self.assertEqual(b"\x8a\x81", pong[:2])
        self.assertEqual(ord("!"), pong[2] ^ pong[6])

    def test_oversized_message_rejected_before_payload(self):
        frames = b"\x81\x7f" + (MAX_MESSAGE + 1).to_bytes(8, "big")
        stream = _EventStream(FakeConnection(frames), threading.Event())
        stream.upgrade()
        with self.assertRaises(ValueError):
            stream.message()


@unittest.skipUnless(sys.platform == "linux" and hasattr(socket, "SO_PEERCRED"), "Linux peer credentials required")
class UnixEventStreamTest(unittest.TestCase):
    def test_owned_socket_with_live_websocket_clears_on_disconnect(self):
        with tempfile.TemporaryDirectory(prefix="hccpi-") as directory, socket.socket(socket.AF_UNIX) as listener:
            path = Path(directory) / "sessiond.sock"
            listener.bind(str(path))
            listener.listen()
            listener.settimeout(2)
            monitor = PiWebActivity(path)
            self.addCleanup(monitor.close)
            monitor.start()
            connection, _ = listener.accept()
            with connection:
                connection.settimeout(2)
                request = b""
                while b"\r\n\r\n" not in request:
                    incoming = connection.recv(4096)
                    self.assertTrue(incoming)
                    request += incoming
                self.assertTrue(request.startswith(b"GET /events HTTP/1.1\r\n"))
                payload = json.dumps(machine(True)).encode()
                connection.sendall(upgrade_response(request) + bytes((0x81, len(payload))) + payload)
                self.wait_for(lambda: monitor.snapshot()[1]["runtimeAvailable"])
                self.assertEqual({}, monitor.snapshot()[0])
                payload = json.dumps(activity("exact-session"), separators=(",", ":")).encode()
                connection.sendall(b"\x81\x7e" + len(payload).to_bytes(2, "big") + payload)
                self.wait_for(lambda: "exact-session" in monitor.snapshot()[0])
            self.wait_for(lambda: not monitor.snapshot()[1]["runtimeAvailable"])
            self.assertEqual({}, monitor.snapshot()[0])

    def wait_for(self, predicate):
        deadline = time.monotonic() + 2
        while not predicate() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(predicate())


if __name__ == "__main__":
    unittest.main()
