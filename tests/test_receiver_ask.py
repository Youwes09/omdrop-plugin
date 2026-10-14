"""With ask = true, a transfer from an unknown sender waits for the person.

The receiver holds the Ask, writes the request beside the sender's preview in
$XDG_RUNTIME_DIR/omdrop/asks, and answers once ID.answer says accept, always
or decline.
"""

import json
import plistlib
import threading
import time
from pathlib import Path

from test_receiver_protocol import ReceiverFixture, dvzip

ICON = b"\x00\x00\x00\x0cjP  \r\n\x87\n" + b"j" * 64


class AskTests(ReceiverFixture):
    config = "ask = true\n"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.asks = Path(cls.tmp.name) / "run" / "omdrop" / "asks"

    def ask(self):
        return plistlib.dumps({
            "SenderComputerName": "Someone's iPhone", "SenderModelName": "iPhone",
            "TransferType": {"files": {}}, "Items": [], "FileIcon": ICON,
            "Files": [{"FileName": "IMG_1.HEIC", "FileType": "public.heic", "FileSize": 2398453}],
        }, fmt=plistlib.FMT_BINARY)

    def answer_with(self, answer, seen):
        """Answer the first request that shows up, recording what it said."""
        def run():
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                for req in self.asks.glob("*.json"):
                    seen.append(json.loads(req.read_text()))
                    seen.append(Path(seen[0]["icon"]).read_bytes() if seen[0]["icon"] else None)
                    req.with_suffix(".answer").write_text(answer + "\n")
                    return
                time.sleep(0.05)
        t = threading.Thread(target=run)
        t.start()
        self.addCleanup(t.join)

    def test_an_accepted_ask_is_answered_and_its_request_cleared(self):
        seen = []
        self.answer_with("accept", seen)
        status, _ = self.post(self.connection(), "/Ask", self.ask())
        self.assertEqual(status, 200)
        request, icon = seen
        self.assertEqual(request["sender"], "Someone's iPhone")
        self.assertEqual(request["files"], [{"name": "IMG_1.HEIC", "type": "public.heic", "size": 2398453}])
        self.assertEqual(request["hashes"], [])       # no proof, so nothing to remember
        self.assertEqual(icon, ICON)
        self.assertEqual(list(self.asks.iterdir()), [])

    def test_a_declined_ask_is_refused(self):
        self.answer_with("decline", [])
        status, _ = self.post(self.connection(), "/Ask", self.ask())
        self.assertEqual(status, 403)
        self.assertEqual(list(self.asks.iterdir()), [])

    def upload(self, conn):
        status, _ = self.post(conn, "/Upload", dvzip([("IMG_1.HEIC", b"photo")]),
                              {"Content-Type": "application/x-dvzip", "TransferID": "IMG_1.HEIC"})
        return status

    def test_an_upload_without_an_accepted_ask_is_refused(self):
        # A sender that skips the Ask must not skip the person.
        self.assertEqual(self.upload(self.connection()), 403)

    def test_an_upload_after_an_accepted_ask_is_stored(self):
        self.answer_with("accept", [])
        conn = self.connection()
        status, _ = self.post(conn, "/Ask", self.ask())
        self.assertEqual(status, 200)
        self.assertEqual(self.upload(conn), 200)

    def test_asks_beyond_the_pending_limit_are_refused_at_once(self):
        statuses = []
        def pending():
            status, _ = self.post(self.connection(), "/Ask", self.ask())
            statuses.append(status)
        waiting = [threading.Thread(target=pending) for _ in range(4)]
        for t in waiting:
            t.start()
        deadline = time.monotonic() + 10
        while len(list(self.asks.glob("*.json"))) < 4 and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual(len(list(self.asks.glob("*.json"))), 4)
        started = time.monotonic()
        status, _ = self.post(self.connection(), "/Ask", self.ask())
        self.assertEqual(status, 403)
        self.assertLess(time.monotonic() - started, 5)   # refused, not queued behind the others
        for req in self.asks.glob("*.json"):
            req.with_suffix(".answer").write_text("decline\n")
        for t in waiting:
            t.join()
        self.assertEqual(statuses, [403] * 4)
