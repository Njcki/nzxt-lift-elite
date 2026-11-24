"""«Reset everything» / `liftctl reset-defaults`: known factory defaults only,
through the normal verified writes plus 0x45; the active profile comes back."""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from nzxt_lift_elite import cli
from nzxt_lift_elite.mock import MouseEmulator, open_mock
from nzxt_lift_elite.protocol import (
    CMD_SET_ACTIVE_PROFILE,
    DANGEROUS_COMMANDS,
    FACTORY_ACTIONS,
    FACTORY_LED_COLORS,
    SAFE_COMMANDS,
)


class ResetDefaultsDeviceTest(unittest.TestCase):
    def setUp(self):
        self.emu = MouseEmulator()
        self.emu.active_profile = 2
        self.emu.buttons[(1, 1)] = 0x7F
        self.emu.buttons[(3, 4)] = 0x80
        self.dev, self.fake = open_mock("wired", emulator=self.emu)
        self.addCleanup(self.fake.close)
        self.addCleanup(self.dev.close)
        # other LED colours + changed DPI/polling, via the normal path
        for i in range(5):
            p = self.dev.read_profile(i, with_buttons=False)
            self.dev.apply_profile(p.with_changes(color=bytes.fromhex("ffff"), dpi=(500, 900, 1700, 3300, 6500)))
        self.before = [self.dev.read_profile(i, with_buttons=False) for i in range(5)]
        self.emu.log.clear()

    def test_reset(self):
        backups: list = []
        seen: list = []
        result = self.dev.reset_defaults(backup=lambda ps: backups.append(list(ps)) or "B",
                                         progress=lambda i, p: seen.append(i))
        # buttons and LED colours: factory on every profile
        for i in range(5):
            for b in range(6):
                self.assertEqual(self.emu.buttons[(i, b)], FACTORY_ACTIONS[b], (i, b))
            p = self.dev.read_profile(i, with_buttons=False)
            self.assertEqual(p.color, FACTORY_LED_COLORS[i])
            # firmware default profile table (FUN_0003403c + template at 0x3bcfb)
            self.assertEqual(p.dpi_stages, (400, 800, 1600, 3200, 6400))
            self.assertNotEqual(self.before[i].dpi_stages, p.dpi_stages)
            self.assertEqual(p.stage, i + 1)
            self.assertEqual(p.polling_hz, 1000)
            self.assertEqual(p.liftoff, 1)
            self.assertTrue(p.motion_sync)
            self.assertEqual(p.stage_count, 5)
            self.assertEqual(p.raw[20], 0x0F)  # AFK timeout: factory 15 min
        # active profile restored (profile 3), only the needed slots activated
        self.assertEqual(self.emu.active_profile, 2)
        self.assertEqual(result.active, 2)
        self.assertEqual(result.activated, [1, 3])
        self.assertEqual(seen, [0, 1, 2, 3, 4])
        # backup of all five, before the first write
        self.assertEqual(len(backups), 1)
        self.assertEqual([p.index for p in backups[0]], [0, 1, 2, 3, 4])
        self.assertEqual(result.backup, "B")
        # only known safe commands plus 0x45: no firmware command, no reset routine
        sent = {p[4] for p in self.emu.log}
        self.assertTrue(sent <= SAFE_COMMANDS | {CMD_SET_ACTIVE_PROFILE}, sorted(hex(c) for c in sent))
        self.assertFalse(sent & DANGEROUS_COMMANDS)
        self.assertEqual(self.emu.unsafe, [])
        acts = [p[5] for p in self.emu.log if p[4] == CMD_SET_ACTIVE_PROFILE]
        self.assertEqual(acts, [1, 3, 2])
        # every 0x45 is followed by a 0x93 of that same profile (sensor re-apply)
        writes = [(w[4], w[5]) for w in self.emu.writes]
        for i, w in enumerate(writes):
            if w[0] == CMD_SET_ACTIVE_PROFILE:
                self.assertEqual(writes[i + 1], (0x93, w[1]), writes)
        self.assertEqual(writes[-2:], [(0x45, 2), (0x93, 2)])  # final re-activation refreshed

    def test_backup_before_first_write(self):
        order = []
        self.dev.reset_defaults(backup=lambda ps: order.append(len(self.emu.writes)))
        self.assertEqual(order, [0])

    def test_failure_still_restores_active(self):
        self.emu.ignore_writes = True  # writes do not stick: verification fails
        with self.assertRaises(Exception):
            self.dev.reset_defaults()
        self.emu.ignore_writes = False
        self.assertEqual(self.emu.active_profile, 2)

    def test_factory_defaults_already(self):
        self.dev.reset_defaults()
        self.emu.log.clear()
        result = self.dev.reset_defaults()
        self.assertEqual(result.activated, [])
        self.assertEqual(self.emu.writes, [])  # nothing to change, nothing written


class FirmwareTableTest(unittest.TestCase):
    """FACTORY_SETTINGS must match the firmware template (when the image is present locally)."""

    def test_matches_firmware_image(self):
        from pathlib import Path

        from nzxt_lift_elite.device import FACTORY_SETTINGS

        img = Path(__file__).resolve().parents[1] / "fw" / "Mouse_App_v1.01.0005_78384_8157779.bin"
        for slot, f in enumerate(FACTORY_SETTINGS):
            self.assertEqual(f, {"polling_hz": 1000, "liftoff": 1, "dpi": (400, 800, 1600, 3200, 6400),
                                 "stage_count": 5, "stage": slot + 1, "motion_sync": True, "afk_minutes": 15})
        if not img.is_file():
            self.skipTest("firmware image not in the checkout (not distributed)")
        d = img.read_bytes()
        base = 0x29000
        tpl = d[0x3bcfb - base: 0x3bcfb - base + 19]
        self.assertEqual(tpl.hex(), "0502900120034006800c0019e803010f000001")
        self.assertEqual(tpl[0], f["stage_count"])  # struct +1: stage count
        self.assertEqual(tpl[15], f["afk_minutes"])  # struct +0x10: AFK minutes
        self.assertEqual(d[0x3bd2c - base: 0x3bd2c - base + 10].hex(), "1f0000f8e00740fd1ff8")


class ResetDefaultsCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.tmp.name, "HOME": self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch.object(cli.sys.stdin, "isatty", lambda: False):
            code = cli.main(["--mock", *argv])
        return code, out.getvalue(), err.getvalue()

    def test_refuses_without_yes(self):
        code, out, err = self.run_cli("reset-defaults")
        self.assertNotEqual(code, 0)
        self.assertIn("--yes", err)
        self.assertFalse(list(Path(self.tmp.name).rglob("*-reset-*.json")))

    def test_yes(self):
        code, out, err = self.run_cli("reset-defaults", "--yes")
        self.assertEqual(code, 0, err)
        self.assertIn("Profilo 5  LED Viola, stadio 5 attivo (6400 DPI), 1000 Hz", out)
        self.assertIn("Profilo attivo ripristinato: 1", out)
        self.assertIn("tabella dei profili predefiniti del firmware", out)
        backups = list(Path(self.tmp.name).rglob("*-reset-slot0-1-2-3-4.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(len(json.loads(backups[0].read_text())["profiles"]), 5)

    def test_english(self):
        from nzxt_lift_elite import i18n

        i18n.set_language("en")
        self.addCleanup(i18n.set_language, "it")
        code, out, err = self.run_cli("reset-defaults", "--yes")
        self.assertEqual(code, 0, err)
        self.assertIn("Profile 3  LED Green, stage 3 active (1600 DPI), 1000 Hz, lift-off 1 mm, motion sync on, factory buttons  [verified]", out)
        self.assertIn("default profile table", out)


if __name__ == "__main__":
    unittest.main()
