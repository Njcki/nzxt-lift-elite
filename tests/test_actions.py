"""Action table: every confirmed code, names, aliases and parsing."""

from __future__ import annotations

import unittest

from nzxt_lift_elite.actions import ACTIONS, BY_CODE, CATEGORIES, action_name, parse_action, search_actions
from nzxt_lift_elite.errors import ValidationError


class ActionTableTest(unittest.TestCase):
    CONFIRMED = {
        # mouse actions (CAM menu order)
        "left-click": 0xA5,
        "middle-click": 0xA6,
        "right-click": 0xA7,
        "mouse-forward": 0xA8,
        "mouse-back": 0xA9,
        "dpi-cycle": 0xAA,
        # keyboard and media, confirmed with CAM captures
        "a": 0x04,
        "right-gui": 0xE7,
        "mute": 0x7F,
        "volume-up": 0x80,
        "volume-down": 0x81,
        "play-pause": 0xE8,
        "stop": 0xE9,
        "previous-track": 0xEA,
        "next-track": 0xEB,
        # HID table, matches CAM's list order
        "ctrl": 0xE0,
        "shift": 0xE1,
        "alt": 0xE2,
        "left-gui": 0xE3,
        "right-ctrl": 0xE4,
        "right-shift": 0xE5,
        "altgr": 0xE6,
        "keypad-hexadecimal": 0xDD,
        "z": 0x1D,
        "1": 0x1E,
        "0": 0x27,
        "f1": 0x3A,
        "f12": 0x45,
        "f13": 0x68,
        "f24": 0x73,
        "keypad-1": 0x59,
        "keypad-0": 0x62,
        "enter": 0x28,
        "space": 0x2C,
    }

    def test_confirmed_codes(self):
        for name, code in self.CONFIRMED.items():
            with self.subTest(name=name):
                self.assertEqual(parse_action(name), code)
                self.assertEqual(action_name(code), name)

    def test_table_is_complete_and_unique(self):
        codes = [a.code for a in ACTIONS]
        self.assertEqual(len(codes), len(set(codes)))
        expected = set(range(0x04, 0xA5)) | set(range(0xA5, 0xAB)) | set(range(0xB0, 0xDE)) | set(range(0xE0, 0xEC))
        self.assertEqual(set(codes), expected)
        for act in ACTIONS:
            self.assertIn(act.category, CATEGORIES)

    def test_key_prefix_and_hex(self):
        self.assertEqual(parse_action("key:0x04"), 0x04)
        self.assertEqual(parse_action("key:04"), 0x04)  # 0.1: bare hex after key:
        self.assertEqual(parse_action("0x80"), 0x80)
        self.assertEqual(parse_action("key:a"), 0x04)
        self.assertEqual(parse_action("KEY:F5"), 0x3E)
        self.assertEqual(parse_action("Volume_Up"), 0x80)

    def test_legacy_names(self):
        self.assertEqual(parse_action("left"), 0xA5)  # mouse action without prefix
        self.assertEqual(parse_action("key:left"), 0x50)  # arrow with key: prefix
        self.assertEqual(parse_action("forward"), 0xA8)
        self.assertEqual(parse_action("dpi"), 0xAA)
        self.assertEqual(parse_action("key:lgui"), 0xE3)

    def test_unknown_codes(self):
        with self.assertRaises(ValidationError):
            parse_action("key:0xf0")
        self.assertEqual(parse_action("key:0xf0", allow_unknown=True), 0xF0)
        self.assertEqual(action_name(0xF0), "key:0xf0")
        with self.assertRaises(ValidationError):
            parse_action("key:0x100", allow_unknown=True)
        with self.assertRaises(ValidationError):
            parse_action("jump")
        with self.assertRaises(ValidationError):
            parse_action("")

    def test_search(self):
        names = [a.name for a in search_actions("volume")]
        self.assertEqual(set(names), {"mute", "volume-up", "volume-down"})
        self.assertIn("mute", [a.name for a in search_actions("0x7f")])
        media = search_actions(category="media")
        self.assertEqual({a.code for a in media}, {0x7F, 0x80, 0x81, 0xE8, 0xE9, 0xEA, 0xEB})
        it = search_actions("volume su", translate=lambda s: {"Volume up": "Volume su"}.get(s, s))
        self.assertEqual(it[0].name, "volume-up")
        self.assertIs(BY_CODE[0xA5].is_mouse, True)


if __name__ == "__main__":
    unittest.main()
