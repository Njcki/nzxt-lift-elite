"""Named profile store and the CLI end to end (against the emulated mouse)."""

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
from nzxt_lift_elite.errors import ValidationError
from nzxt_lift_elite.profile import Profile
from nzxt_lift_elite.protocol import captured_read_reply
from nzxt_lift_elite.store import ProfileStore, slugify


def _profile(i: int) -> Profile:
    return Profile.from_reply(captured_read_reply(i), expected_index=i,
                              buttons={"left": 0xA5, "middle": 0xA6, "right": 0xA7,
                                       "forward": 0x80, "back": 0xA9, "dpi": 0xAA})


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ProfileStore(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_named_round_trip(self):
        path = self.store.save_named("Gioco FPS", _profile(2))
        self.assertEqual(path.name, "gioco-fps.json")
        cfg = self.store.load_named("Gioco FPS")
        self.assertIsNone(cfg.index)  # named profiles are slot-independent
        self.assertEqual(cfg.name, "Gioco FPS")
        self.assertEqual(cfg.dpi_stages, (800, 1200, 1600, 3200, 6400))
        self.assertEqual(cfg.buttons["forward"], 0x80)
        applied = cfg.apply_to(_profile(4), target_index=4)
        self.assertEqual(applied.index, 4)
        self.assertEqual(applied.color, _profile(4).color)  # the slot keeps its LED colour
        self.assertIsNone(cfg.indicator)
        self.assertEqual(applied.polling_hz, 2000)
        self.assertEqual([n.name for n in self.store.list_named()], ["Gioco FPS"])
        with self.assertRaises(ValidationError):
            self.store.save_named("Gioco FPS", _profile(1), overwrite=False)
        self.store.delete_named("gioco fps")
        self.assertEqual(self.store.list_named(), [])

    def test_slots_and_backups(self):
        self.store.remember_slot(_profile(1))
        self.assertEqual(self.store.load_slot(1).index, 1)
        self.assertIsNone(self.store.load_slot(3))
        b = self.store.backup([_profile(0), _profile(1)])
        doc = json.loads(b.read_text())
        self.assertEqual([p["profile"] for p in doc["profiles"]], [0, 1])

    def test_slugify(self):
        self.assertEqual(slugify("Città / Lavoro"), "citta-lavoro")
        with self.assertRaises(ValidationError):
            slugify("///")


class CliMockTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--mock", *argv])
        return code, out.getvalue(), err.getvalue()

    def test_show_and_json(self):
        code, out, _ = self.run_cli("show")
        self.assertEqual(code, 0)
        self.assertIn("Profilo 1", out)
        self.assertIn("Profilo 5", out)
        self.assertNotIn("Profilo 0", out)
        self.assertIn("Laterale avanti", out)
        self.assertIn("Profilo attivo (sperimentale, comando 0x44): 1", out)
        # --profile 3 is user-facing (= internal slot 2), which has the distinctive DPI list.
        code, out, _ = self.run_cli("show", "--json", "--profile", "3")
        doc = json.loads(out)
        self.assertEqual(doc["profiles"][0]["profile"], 2)  # JSON keeps the internal slot 0..4
        self.assertEqual(doc["profiles"][0]["dpi_stages"], [800, 1200, 1600, 3200, 6400])
        self.assertEqual(doc["profiles"][0]["buttons"]["dpi"], "dpi-cycle")

    def test_set_remap_save_load(self):
        code, out, _ = self.run_cli("set", "--profile", "2", "--polling", "500", "--dpi-stage", "3=1650")
        self.assertEqual(code, 0, out)
        self.assertIn("verificata", out)
        self.assertIn("Profilo 2", out)
        code, out, _ = self.run_cli("remap", "--profile", "2", "--button", "forward", "--action", "volume-up")
        self.assertEqual(code, 0, out)
        self.assertIn("volume-up", out)
        code, out, _ = self.run_cli("save", "Ufficio", "--profile", "1")
        self.assertEqual(code, 0)
        code, out, _ = self.run_cli("profiles")
        self.assertIn("Ufficio", out)
        code, out, _ = self.run_cli("load", "Ufficio", "--profile", "4")
        self.assertEqual(code, 0, out)
        self.assertTrue(list((Path(self.tmp.name) / "nzxt-lift-elite" / "backups").glob("*.json")))

    def test_bad_input(self):
        code, _, err = self.run_cli("set", "--profile", "1", "--dpi-stage", "2=1234")
        self.assertEqual(code, 1)
        self.assertIn("multiple of 50", err)
        code, _, err = self.run_cli("remap", "--profile", "1", "--button", "dpi", "--action", "key:0xf5")
        self.assertEqual(code, 1)
        code, _, err = self.run_cli("set", "--profile", "1")
        self.assertEqual(code, 1)
        code, _, err = self.run_cli("show", "--profile", "0")
        self.assertEqual(code, 1)
        self.assertIn("1..5", err)
        code, _, err = self.run_cli("show", "--profile", "6")
        self.assertEqual(code, 1)
        self.assertIn("1..5", err)

    def test_set_color_and_restore_led_colors(self):
        from nzxt_lift_elite.device import LiftElite
        from nzxt_lift_elite.mock import open_mock
        from nzxt_lift_elite.protocol import FACTORY_LED_COLORS

        code, out, err = self.run_cli("set", "--profile", "1", "--color", "#ffffff")
        self.assertEqual(code, 0, err)
        self.assertIn("verificata", out)
        self.assertIn("#ffffff", out)
        # A fresh --mock starts from factory captures, so restore reports all five.
        code, out, err = self.run_cli("restore-led-colors")
        self.assertEqual(code, 0, err)
        self.assertIn("Profilo 1", out)
        self.assertIn("Profilo 5", out)
        self.assertIn("1f00", out)
        self.assertIn("riletti", out)
        from nzxt_lift_elite import cli as liftcli
        help_text = liftcli._build_parser().format_help()
        self.assertIn("restore-led-colors", help_text)
        self.assertIn("--color", help_text)

    def test_debug_dump_is_read_only_and_shows_0x44(self):
        code, out, err = self.run_cli("debug-dump")
        self.assertEqual(code, 0, err)
        self.assertIn("sola lettura", out)
        self.assertIn("## 44 get-active-profile", out)
        self.assertIn("OUT 4e 02 81 00 44", out)
        self.assertIn("interpretazione sperimentale: slot interno 0 = profilo utente 1", out)
        self.assertIn("## 03 uid", out)
        self.assertIn("## b0 status", out)
        self.assertIn("## 92 profile 0", out)
        self.assertIn("## 51 button p0/left", out)
        self.assertNotIn("45", out.split("OUT")[0])  # no 0x45 section header
        self.assertNotRegex(out, r"OUT 4e 02 8. 00 45")
        self.assertNotRegex(out, r"OUT 4e 02 8. 00 f1")
        self.assertIn("tutti di sola lettura", out)
        help_text = cli._build_parser().format_help()
        self.assertIn("debug-dump", help_text)

    def test_no_firmware_command_in_cli(self):
        parser = cli._build_parser()
        text = parser.format_help()
        for word in ("flash", "update", "bootloader"):
            self.assertNotIn(word, text.lower())


if __name__ == "__main__":
    unittest.main()
