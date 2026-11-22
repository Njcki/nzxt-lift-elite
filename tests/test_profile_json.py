"""JSON save/load and ProfileConfig application."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from nzxt_lift_elite.errors import ValidationError
from nzxt_lift_elite.profile import (
    Profile,
    dumps_profiles,
    load_profiles_json,
    parse_profiles_document,
    save_profiles_json,
    select_config,
)
from nzxt_lift_elite.protocol import CAPTURED_BODIES, captured_read_reply


def _all_profiles() -> list[Profile]:
    return [
        Profile.from_reply(captured_read_reply(i), expected_index=i)
        for i in range(5)
    ]


class JsonRoundTripTest(unittest.TestCase):
    def test_save_and_load_all_five(self):
        profiles = _all_profiles()
        text = dumps_profiles(profiles)
        data = json.loads(text)
        self.assertEqual(data["device"], "NZXT Lift Elite")
        self.assertEqual(data["usb"], "1e71:2129")
        self.assertEqual(data["format"], 2)
        self.assertEqual(len(data["profiles"]), 5)
        # Profiles built without a button read have buttons = null.
        self.assertIsNone(data["profiles"][0]["buttons"])
        self.assertEqual(data["profiles"][0]["color"], "#0000ff")

        configs = parse_profiles_document(data)
        selected = select_config(configs, profile_index=None, require_all=True)
        self.assertEqual([i for i, _ in selected], list(range(5)))

        for index, config in selected:
            applied = config.apply_to(profiles[index], target_index=index)
            self.assertEqual(applied.known_body(), CAPTURED_BODIES[index])
            self.assertEqual(applied.polling_hz, profiles[index].polling_hz)
            self.assertEqual(applied.dpi, profiles[index].dpi)
            self.assertEqual(applied.stage, profiles[index].stage)
            self.assertEqual(applied.motion_sync, profiles[index].motion_sync)
            self.assertEqual(applied.color, profiles[index].color)

    def test_load_applies_partial_changes_via_rmw(self):
        original = Profile.from_reply(captured_read_reply(0), expected_index=0)
        configs = parse_profiles_document(
            {
                "profile": 0,
                "polling_hz": 1000,
                "dpi_stage_overrides": {"2": 1000},
                "motion_sync": False,
                "buttons": {"dpi": "key:a", "forward": "back"},
            }
        )
        self.assertEqual(len(configs), 1)
        updated = configs[0].apply_to(original, target_index=0)
        self.assertEqual(updated.polling_hz, 1000)
        self.assertEqual(updated.dpi[1], 1000)
        self.assertEqual(updated.dpi[0], 400)
        self.assertFalse(updated.motion_sync)
        self.assertEqual(updated.liftoff, 1)  # preserved
        self.assertEqual(updated.color, original.color)
        self.assertEqual(updated.buttons, {"dpi": 0x04, "forward": 0xA9})

    def test_json_color_is_applied_from_full_export(self):
        original = Profile.from_reply(captured_read_reply(0), expected_index=0)
        configs = parse_profiles_document({"profile": 0, "polling_hz": 500, "color": "#ffffff"})
        updated = configs[0].apply_to(original, target_index=0)
        self.assertEqual(updated.polling_hz, 500)
        self.assertNotEqual(updated.color, original.color)
        self.assertEqual(updated.color_rgb_hex, "#ffffff")

    def test_load_profile_n_from_single_or_matching_entry(self):
        configs = parse_profiles_document(
            {"profiles": [{"profile": 0, "polling_hz": 250}]}
        )
        # A single entry can be applied to a different slot.
        chosen = select_config(configs, profile_index=3, require_all=False)
        self.assertEqual(chosen[0][0], 3)
        self.assertEqual(chosen[0][1].polling_hz, 250)

        all_five = parse_profiles_document(json.loads(dumps_profiles(_all_profiles())))
        chosen = select_config(all_five, profile_index=2, require_all=False)
        self.assertEqual(chosen[0][0], 2)
        self.assertEqual(chosen[0][1].polling_hz, 2000)

    def test_load_all_requires_five_indexed_profiles(self):
        configs = parse_profiles_document({"profiles": [{"profile": 0, "polling_hz": 250}]})
        with self.assertRaises(ValidationError):
            select_config(configs, profile_index=None, require_all=True)

    def test_file_round_trip(self):
        profiles = _all_profiles()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profiles.json"
            save_profiles_json(path, profiles)
            loaded = load_profiles_json(path)
            self.assertEqual(len(loaded), 5)
            self.assertEqual(loaded[2].polling_hz, 2000)
            self.assertEqual(loaded[2].dpi_stages, (800, 1200, 1600, 3200, 6400))
            self.assertEqual(loaded[3].dpi_stage, 3)

    def test_validation_errors(self):
        with self.assertRaises(ValidationError):
            parse_profiles_document({"polling_hz": 1234})
        with self.assertRaises(ValidationError):
            parse_profiles_document({"dpi_stages": []})
        with self.assertRaises(ValidationError):
            parse_profiles_document({"buttons": {"thumb": "left"}})
        with self.assertRaises(ValidationError):
            parse_profiles_document({"usb": "1234:5678", "profiles": []})
        with self.assertRaises(ValidationError):
            parse_profiles_document({"liftoff": 9})
        with self.assertRaises(ValidationError):
            parse_profiles_document({"motion_sync": "yes"})
        with self.assertRaises(ValidationError):
            parse_profiles_document({"unknown_field": 1, "polling_hz": 1000})


if __name__ == "__main__":
    unittest.main()
