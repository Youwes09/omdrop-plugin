"""Without Omarchy's helpers, omdrop uses the freedesktop tools every desktop has.

Toasts go through notify-send, with a click waited for in its own unit; web
links open with xdg-open; the terminal is $TERMINAL.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OMDROP = ROOT / "bin" / "omdrop"


def path_without_omarchy():
    """PATH minus any directory holding Omarchy's helpers, as on another desktop."""
    return ":".join(d for d in os.environ["PATH"].split(":")
                    if d and not any(Path(d).glob("omarchy-*")))


class FallbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "log"
        # Each records what it was asked to do.
        for name in ("notify-send", "systemd-run", "xdg-open", "wl-copy"):
            path = self.bin / name
            path.write_text(f'#!/bin/sh\nprintf "{name}|%s\\n" "$*" >> "$LOG"\n')
            path.chmod(0o755)
        self.env = dict(os.environ, PATH=f"{self.bin}:{path_without_omarchy()}", LOG=str(self.log),
                        XDG_CONFIG_HOME=str(self.root / "config"), XDG_DATA_HOME=str(self.root / "data"))
        self.env.pop("TERMINAL", None)

    def run_omdrop(self, *args):
        return subprocess.run([OMDROP, *args], env=self.env, capture_output=True, text=True)

    def logged(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_a_web_link_opens_with_xdg_open_in_its_own_unit(self):
        link = self.root / "link.url"
        link.write_text("[InternetShortcut]\nURL=https://example.com/\n")

        result = self.run_omdrop("open-link", str(link))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.logged(), ["systemd-run|--user --quiet --collect xdg-open https://example.com/"])

    def test_a_refused_link_is_announced_with_notify_send(self):
        link = self.root / "link.url"
        link.write_text("[InternetShortcut]\nURL=file:///etc/passwd\n")

        self.run_omdrop("open-link", str(link))

        logged = self.logged()
        self.assertEqual(len(logged), 1, logged)
        self.assertTrue(logged[0].startswith("notify-send|-a Omdrop Can't open link.url "), logged)

    def test_an_arrival_waits_for_its_click_in_its_own_unit(self):
        note = self.root / "Downloads" / "note.txt"
        note.parent.mkdir()
        note.write_text("hi\n")

        self.run_omdrop("handle", str(note))

        # The waiter's script spans lines, so its unit is everything from its systemd-run on.
        log = self.log.read_text()
        unit = log[log.index("systemd-run|--user --quiet --collect --expand-environment=no -- bash -c"):].strip()
        self.assertIn("--action=default=Open --wait", unit)
        self.assertIn("-a Omdrop -t 8000 note.txt", unit)
        self.assertTrue(unit.endswith(f"act open {note}"), unit)
