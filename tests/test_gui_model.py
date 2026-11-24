"""GUI logic without GTK: drafts, dirty tracking, summaries, DPI slider, session."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from nzxt_lift_elite.gui import i18n
from nzxt_lift_elite.gui.model import (
    SLIDER_MAX,
    EditorModel,
    Session,
    action_label,
    action_search,
    dpi_to_slider,
    export_profiles,
    group_actions,
    import_profile,
    slider_to_dpi,
    snap_dpi,
)
from nzxt_lift_elite.store import ProfileStore


class SliderTest(unittest.TestCase):
    def test_round_trip_and_snapping(self):
        self.assertEqual(slider_to_dpi(0), 100)
        self.assertEqual(slider_to_dpi(SLIDER_MAX), 26000)
        for dpi in (400, 800, 1600, 3200, 6400, 12000, 26000):
            self.assertEqual(slider_to_dpi(dpi_to_slider(dpi)), dpi)
        for pos in range(0, SLIDER_MAX + 1, 37):
            self.assertEqual(slider_to_dpi(pos) % 50, 0)
        self.assertEqual(snap_dpi(1234), 1250)
        self.assertEqual(snap_dpi(10), 50)
        self.assertEqual(snap_dpi(99999), 26000)


class EditorTest(unittest.TestCase):
    def setUp(self):
        i18n.set_language("it")
        self.session = Session(mock="wired")
        self.model = EditorModel()
        self.model.load_state(self.session.read_state())

    def tearDown(self):
        self.session.close()

    def test_edit_summary_apply(self):
        m = self.model
        self.assertFalse(m.any_dirty)
        m.select(1)
        m.set_polling(500)
        m.set_dpi(2, 1234)  # snapped
        m.set_active_stage(3)
        m.set_button("forward", 0x80)
        m.set_color_rgb(0, 255, 0)
        self.assertEqual(m.dirty_slots(), [1])
        text = "\n".join(m.changes(1))
        self.assertIn("1000 → 500 Hz", text)
        self.assertIn("Stadio 2: 800 → 1250 DPI", text)
        self.assertIn("Laterale avanti: Mouse avanti → Volume su", text)
        self.session._fake.emulator.active_profile = 1  # buttons: active profile only
        results, backup = self.session.apply([m.drafts[i] for i in m.dirty_slots()])
        self.assertIsNone(backup)  # no backups in mock mode
        for p in results:
            m.mark_applied(p)
        self.assertFalse(m.any_dirty)
        fresh = self.session.read_state()
        p1 = fresh.profiles[1]
        self.assertEqual((p1.polling_hz, p1.dpi[1], p1.stage, p1.buttons["forward"]), (500, 1250, 3, 0x80))

    def test_revert_and_factory(self):
        m = self.model
        m.set_motion_sync(False)
        m.set_liftoff(2)
        self.assertTrue(m.is_dirty())
        m.revert()
        self.assertFalse(m.any_dirty)
        m.set_button("left", 0x04)
        m.factory_buttons()
        self.assertFalse(m.is_dirty())

    def test_polling_note(self):
        m = self.model
        # Slot 0 is 8000 Hz. By cable 8000 is real: no note. Over the
        # receiver every rate gets a short note (radio 4 kHz / sensor mode).
        self.assertEqual(m.transport, "wired")
        self.assertIsNone(m.polling_note())
        m.set_polling(1000)
        self.assertIsNone(m.polling_note())
        with mock.patch.object(type(m), "transport", new_callable=mock.PropertyMock, return_value="wireless"):
            low = m.polling_note()
            self.assertIsNotNone(low)
            m.set_polling(8000)
            high = m.polling_note()
            self.assertIn("4000", high)
            self.assertNotEqual(low, high)

    def test_named_profile_into_other_slot(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ProfileStore(Path(tmp))
            store.save_named("FPS", self.model.original(2))
            self.model.select(4)
            self.model.apply_config(store.load_named("FPS"))
            self.assertEqual(self.model.current().index, 4)
            self.assertEqual(self.model.current().dpi_stages, (800, 1200, 1600, 3200, 6400))
            self.assertTrue(self.model.is_dirty(4))

    def test_export_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = export_profiles(Path(tmp) / "tutti", list(self.model.drafts.values()))
            self.assertEqual(path.suffix, ".json")
            cfg = import_profile(path, 3)
            self.assertEqual(cfg.index, 3)
            self.assertEqual(cfg.dpi_stage, 3)  # captured profile 3, wire byte 0x02

    def test_labels_and_search(self):
        self.assertEqual(action_label(0x80), "Volume su")
        self.assertEqual(action_label(0xF3), "Codice sconosciuto 0xf3")
        self.assertEqual(action_search("volume su")[0].code, 0x80)
        groups = group_actions(action_search(""))
        self.assertEqual(groups[0][0], "mouse")
        self.assertEqual(groups[1][0], "media")
        with mock.patch.dict(os.environ, {"LIFT_ELITE_LANG": "en"}):
            self.assertEqual(i18n.detect_language(), "en")
        i18n.set_language("en")
        self.assertEqual(action_label(0x80), "Volume up")
        i18n.set_language("it")

    def test_no_writes_on_startup(self):
        emu = self.session._fake.emulator
        self.assertEqual(emu.writes, [])


if __name__ == "__main__":
    unittest.main()
