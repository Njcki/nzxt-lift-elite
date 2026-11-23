"""Live DPI stage (purple button short press): model merge + read-only poll."""

from __future__ import annotations

import threading
import time
import unittest

from nzxt_lift_elite.errors import ProtocolError
from nzxt_lift_elite.gui import i18n
from nzxt_lift_elite.gui.model import EditorModel, Session
from nzxt_lift_elite.protocol import CMD_GET_ACTIVE_PROFILE, CMD_READ_PROFILE, READ_ONLY_COMMANDS

FORBIDDEN = {0xF1, 0xF4, 0xF5, 0xF6, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x45}


class _Base(unittest.TestCase):
    def setUp(self):
        i18n.set_language("it")
        self.session = Session(mock="wired")
        self.model = EditorModel()
        self.model.load_state(self.session.read_state())
        self.emu = self.session._fake.emulator
        self.emu.log.clear()

    def tearDown(self):
        self.session.close()


class AdoptDeviceStageTest(_Base):
    def test_no_pending_choice_follows_mouse_and_keeps_dpi_edits(self):
        m = self.model
        self.assertEqual(m.current().stage, 2)
        m.set_dpi(2, 1350)  # unsaved value edit on the active stage
        self.assertTrue(m.adopt_device_stage(0, 3))
        self.assertEqual(m.original(0).stage, 3)  # applied baseline moved
        self.assertEqual(m.current().stage, 3)  # radio follows the mouse
        self.assertEqual(m.current().dpi[1], 1350)  # edit kept
        self.assertEqual(m.dirty_slots(), [0])
        self.assertNotIn("Stadio attivo", " ".join(m.changes(0)))  # not shown as an edit

    def test_clean_slot_stays_clean(self):
        m = self.model
        self.assertTrue(m.adopt_device_stage(0, 5))
        self.assertFalse(m.any_dirty)

    def test_pending_user_choice_stays_pending(self):
        m = self.model
        m.set_active_stage(4)  # user picked stage 4, not applied yet
        self.assertTrue(m.adopt_device_stage(0, 3))
        self.assertEqual(m.original(0).stage, 3)
        self.assertEqual(m.current().stage, 4)  # still pending
        self.assertIn("Stadio attivo: 3 → 4", m.changes(0))

    def test_pending_choice_reached_by_button_is_no_longer_a_change(self):
        m = self.model
        m.set_active_stage(3)
        self.assertTrue(m.adopt_device_stage(0, 3))
        self.assertFalse(m.any_dirty)

    def test_same_or_invalid_stage_is_ignored(self):
        m = self.model
        self.assertFalse(m.adopt_device_stage(0, 2))
        self.assertFalse(m.adopt_device_stage(0, 0))
        self.assertFalse(m.adopt_device_stage(0, 9))
        self.assertFalse(m.adopt_device_stage(7, 1))

    def test_other_slot_and_active_profile(self):
        m = self.model
        m.select(1)
        self.assertTrue(m.adopt_device_stage(3, 1, active_profile=3))
        self.assertEqual(m.onboard_active_profile, 3)
        self.assertEqual(m.original(3).stage, 1)
        self.assertEqual(m.current().index, 1)


class PollTest(_Base):
    def _cmds(self):
        return [p[4] for p in self.emu.log]

    def test_poll_sees_short_press_with_read_only_commands(self):
        r = self.session.poll_active_stage(0)
        self.assertEqual((r.active_profile, r.slot, r.stage), (0, 0, 2))
        self.emu.press_dpi_button()
        r = self.session.poll_active_stage(0)
        self.assertEqual(r.stage, 3)
        for _ in range(3):  # 3 -> 4 -> 5 -> wraps to 1
            self.emu.press_dpi_button()
        self.assertEqual(self.session.poll_active_stage(0).stage, 1)
        cmds = set(self._cmds())
        self.assertEqual(cmds, {CMD_GET_ACTIVE_PROFILE, CMD_READ_PROFILE})
        self.assertTrue(cmds <= READ_ONLY_COMMANDS)
        self.assertFalse(cmds & FORBIDDEN)
        self.assertEqual(self.emu.writes, [])
        self.assertEqual(self.emu.unsafe, [])

    def test_poll_follows_active_profile(self):
        self.emu.active_profile = 2
        self.emu.press_dpi_button()
        r = self.session.poll_active_stage(0)
        self.assertEqual((r.active_profile, r.slot), (2, 2))
        self.assertEqual(r.stage, self.emu.profiles[2][9] + 1)

    def test_poll_skips_while_another_operation_runs(self):
        hold, release = threading.Event(), threading.Event()

        def busy():
            with self.session._io:
                hold.set()
                release.wait(2)

        t = threading.Thread(target=busy)
        t.start()
        hold.wait(2)
        try:
            self.assertIsNone(self.session.poll_active_stage(0))
            self.assertEqual(self.emu.log, [])  # nothing sent
        finally:
            release.set()
            t.join()

    def test_poll_never_opens_a_closed_session(self):
        self.session.close()
        self.assertIsNone(self.session.poll_active_stage(0))

    def test_asleep_mouse_fails_fast_and_restores_timeouts(self):
        dev = self.session._dev
        before = dev.timeout, dev.retries, dev.lock_timeout
        self.emu.asleep = True
        t0 = time.monotonic()
        with self.assertRaises(ProtocolError):
            self.session.poll_active_stage(None, timeout=0.1)
        self.assertLess(time.monotonic() - t0, 1.0)
        self.assertEqual((dev.timeout, dev.retries, dev.lock_timeout), before)

    def test_poll_falls_back_to_slot_when_0x44_fails(self):
        dev = self.session._dev
        orig = dev.read_active_profile

        def broken():
            raise ProtocolError("no 0x44")

        dev.read_active_profile = broken
        try:
            r = self.session.poll_active_stage(1)
        finally:
            dev.read_active_profile = orig
        self.assertEqual((r.active_profile, r.slot), (None, 1))


class ActiveProfileTest(_Base):
    """Long press of the purple button: onboard profile changes (read via 0x44)."""

    def _poll_and_merge(self):
        r = self.session.poll_active_stage(self.model.onboard_active_profile)
        return r, self.model.adopt_poll(r.active_profile, r.slot, r.stage)

    def test_first_load_selects_active_profile(self):
        self.emu.active_profile = 3
        m = EditorModel()
        m.load_state(self.session.read_state())
        self.assertEqual((m.onboard_active_profile, m.selected), (3, 3))

    def test_long_press_without_edits_follows_and_reads_new_stage(self):
        self.emu.long_press_dpi_button()  # profile 1 -> 2 (slot 1)
        self.emu.press_dpi_button()  # and a short press on the new profile
        r, change = self._poll_and_merge()
        self.assertEqual((r.active_profile, r.slot), (1, 1))
        self.assertTrue(change.active_changed and change.switched and change.stage_changed)
        m = self.model
        self.assertEqual((m.onboard_active_profile, m.selected), (1, 1))
        self.assertEqual(m.original(1).stage, self.emu.profiles[1][9] + 1)
        self.assertFalse(m.any_dirty)
        self.assertEqual(self.emu.writes, [])
        self.assertFalse({p[4] for p in self.emu.log} & FORBIDDEN)

    def test_long_press_with_unsaved_edits_keeps_edited_profile(self):
        m = self.model
        m.set_dpi(1, 450)
        self.emu.long_press_dpi_button()
        _r, change = self._poll_and_merge()
        self.assertTrue(change.active_changed)
        self.assertFalse(change.switched)
        self.assertEqual((m.onboard_active_profile, m.selected), (1, 0))
        self.assertEqual(m.current().dpi[0], 450)  # edit kept
        self.assertEqual(m.dirty_slots(), [0])

    def test_editing_another_profile_is_not_moved(self):
        m = self.model
        m.select(4)
        self.emu.long_press_dpi_button()
        _r, change = self._poll_and_merge()
        self.assertTrue(change.active_changed)
        self.assertFalse(change.switched)
        self.assertEqual((m.onboard_active_profile, m.selected), (1, 4))

    def test_wraps_and_0x45_is_never_sent(self):
        for _ in range(5):
            self.emu.long_press_dpi_button()
            self._poll_and_merge()
        self.assertEqual(self.model.onboard_active_profile, 0)
        self.assertNotIn(0x45, {p[4] for p in self.emu.log})
        self.assertNotIn(0x45, READ_ONLY_COMMANDS)

    def test_gate_kept(self):
        from nzxt_lift_elite import protocol

        self.assertTrue(protocol.ENABLE_ACTIVE_PROFILE_APPLY)
        self.assertNotIn(0x45, protocol.SAFE_COMMANDS)  # only via the explicit allowlist
        self.assertEqual(protocol.EXPLICIT_ALLOWLIST, frozenset({0x45}))


if __name__ == "__main__":
    unittest.main()
