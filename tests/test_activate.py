"""0x45 set-active-profile: explicit allowlist, `liftctl activate`, GUI path behind the flag."""

from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest
from unittest import mock

from nzxt_lift_elite import cli
from nzxt_lift_elite.errors import DangerousCommandError, InactiveProfileRemapError, VerificationError
from nzxt_lift_elite.mock import MouseEmulator, open_mock
from nzxt_lift_elite.protocol import (
    CMD_SET_ACTIVE_PROFILE,
    DANGEROUS_COMMANDS,
    ENABLE_ACTIVE_PROFILE_APPLY,
    EXPLICIT_ALLOWLIST,
    READ_ONLY_COMMANDS,
    SAFE_COMMANDS,
    assert_safe_packet,
    header,
    pad_packet,
    set_active_profile_packet,
)


class AllowlistTest(unittest.TestCase):
    def test_0x45_only_with_explicit_allow(self):
        pkt = set_active_profile_packet(1)
        self.assertEqual(pkt[:6], bytes.fromhex("4e0282004501"))
        self.assertFalse(any(pkt[6:]))
        with self.assertRaises(DangerousCommandError):
            assert_safe_packet(pkt)
        assert_safe_packet(pkt, allow=EXPLICIT_ALLOWLIST)
        self.assertEqual(EXPLICIT_ALLOWLIST, frozenset({CMD_SET_ACTIVE_PROFILE}))
        self.assertNotIn(CMD_SET_ACTIVE_PROFILE, SAFE_COMMANDS)
        self.assertNotIn(CMD_SET_ACTIVE_PROFILE, READ_ONLY_COMMANDS)
        self.assertTrue(ENABLE_ACTIVE_PROFILE_APPLY)  # on since the real-mouse test

    def test_firmware_commands_never_allowed(self):
        for cmd in sorted(DANGEROUS_COMMANDS):
            with self.subTest(cmd=hex(cmd)):
                with self.assertRaises(DangerousCommandError):
                    assert_safe_packet(pad_packet(header(cmd, 1) + b"\x01"), allow=EXPLICIT_ALLOWLIST)
                with self.assertRaises(DangerousCommandError):
                    assert_safe_packet(pad_packet(header(cmd, 1) + b"\x01"), allow=frozenset({cmd}))

    def test_malformed_0x45_refused(self):
        bad = [
            pad_packet(header(CMD_SET_ACTIVE_PROFILE, 1) + b"\x05"),  # PP > 4
            pad_packet(header(CMD_SET_ACTIVE_PROFILE, 2) + b"\x01\x01"),  # extra payload
            pad_packet(header(CMD_SET_ACTIVE_PROFILE, 1, target=0x01) + b"\x01"),  # to receiver
        ]
        for pkt in bad:
            with self.subTest(pkt=pkt[:7].hex()):
                with self.assertRaises(DangerousCommandError):
                    assert_safe_packet(pkt, allow=EXPLICIT_ALLOWLIST)


class ActivateDeviceTest(unittest.TestCase):
    def setUp(self):
        self.emu = MouseEmulator()
        self.dev, self.fake = open_mock(emulator=self.emu)

    def tearDown(self):
        self.dev.close()
        self.fake.close()

    def test_activate_reads_sends_verifies(self):
        r = self.dev.activate_profile(2)
        self.assertEqual((r.before, r.after), (0, 2))
        self.assertEqual(r.reply[:5], bytes.fromhex("4e02810001"))
        self.assertEqual(self.emu.active_profile, 2)
        cmds = [p[4] for p in self.emu.log]
        # 0x44, 0x45, 0x44 verify, then the profile body re-sent unchanged:
        # 0x92 read, 0x93 write, 0xb0 status (like CAM), 0x92 read-back
        self.assertEqual(cmds, [0x44, 0x45, 0x44, 0x92, 0x93, 0xB0, 0x92])
        self.assertEqual(self.emu.log[1][:6], bytes.fromhex("4e0282004502"))
        self.assertEqual(self.emu.unsafe, [])
        self.assertEqual([(w[4], w[5]) for w in self.emu.writes], [(0x45, 2), (0x93, 2)])
        self.assertIsNotNone(r.refreshed)

    def test_reapply_writes_unchanged_body(self):
        before = bytes(self.emu.profiles[2])
        self.dev.activate_profile(2)
        write = [w for w in self.emu.writes if w[4] == 0x93][0]
        self.assertEqual(write[6:24], before[6:24])  # same known body bytes
        self.assertEqual(bytes(self.emu.profiles[2])[6:24], before[6:24])

    def test_dry_run_no_reapply(self):
        self.dev.dry_run = True
        self.dev.activate_profile(2)
        self.assertNotIn(0x93, [p[4] for p in self.emu.log])

    def test_already_active_sends_nothing(self):
        r = self.dev.activate_profile(0)
        self.assertIsNone(r.sent)
        self.assertEqual([p[4] for p in self.emu.log], [0x44])

    def test_verification_failure(self):
        orig = self.emu.handle

        def ignore_45(pkt):
            if pkt[4] == 0x45:
                self.emu.log.append(pkt)
                return [pad_packet(bytes.fromhex("4e02810001"))]
            return orig(pkt)

        self.emu.handle = ignore_45
        with self.assertRaises(VerificationError):
            self.dev.activate_profile(3)

    def test_gui_path_follows_flag(self):
        with mock.patch("nzxt_lift_elite.device.ENABLE_ACTIVE_PROFILE_APPLY", False):
            with self.assertRaises(DangerousCommandError):
                self.dev.set_active_profile(1)
        self.assertNotIn(0x45, [p[4] for p in self.emu.log])
        self.assertEqual(self.dev.set_active_profile(1), 1)  # flag on
        self.assertEqual(self.emu.active_profile, 1)

    def test_dry_run_does_not_send(self):
        self.dev.dry_run = True
        r = self.dev.activate_profile(1)
        self.assertIsNone(r.after)
        self.assertNotIn(0x45, [p[4] for p in self.emu.log])
        self.assertEqual(self.emu.active_profile, 0)


class ActivateCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--mock", *argv])
        return code, out.getvalue(), err.getvalue()

    def test_activate(self):
        code, out, _ = self.run_cli("activate", "--profile", "2")
        self.assertEqual(code, 0, out)
        self.assertIn("Profilo attivo prima: 1", out)
        self.assertIn("4e 02 82 00 45 01", out)
        self.assertIn("4e 02 81 00 01", out)
        self.assertIn("Profilo attivo dopo: 2", out)
        self.assertIn("reinviate invariate (0x93)", out)

    def test_already_active_and_bad_input(self):
        code, out, _ = self.run_cli("activate", "--profile", "1")
        self.assertEqual(code, 0)
        self.assertIn("già attivo", out)
        code, _, err = self.run_cli("activate", "--profile", "6")
        self.assertEqual(code, 1)

    def test_dry_run(self):
        code, out, _ = self.run_cli("--dry-run", "activate", "--profile", "3")
        self.assertEqual(code, 0)
        self.assertIn("dry-run", out)


class GuiApplyFlagTest(unittest.TestCase):
    """GUI Applica with «Rendi attivo» (flag on); flag off never sends 0x45."""

    def setUp(self):
        from nzxt_lift_elite.gui.model import EditorModel, Session

        self.session = Session(mock="wired")
        self.model = EditorModel()
        self.model.load_state(self.session.read_state())
        self.emu = self.session._fake.emulator
        self.emu.log.clear()

    def tearDown(self):
        self.session.close()

    def test_activate_first_then_buttons(self):
        m = self.model
        m.select(1)
        self.assertFalse(m.buttons_writable())  # not active, no «Rendi attivo» yet
        self.assertFalse(m.activation_pending)  # editing alone never activates
        m.request_activation(1)  # «Rendi attivo»
        self.assertTrue(m.buttons_writable())
        m.set_button("middle", 0x7F)
        self.assertTrue(m.activation_pending)
        self.assertTrue(m.has_pending_apply)
        results, _ = self.session.apply([m.drafts[1]], activate_profile=m.pending_activation)
        m.mark_activated(1)
        self.assertIsNone(m.pending_activation)
        writes = [(w[4], w[5]) for w in self.emu.writes]
        self.assertEqual(writes, [(0x45, 1), (0x93, 1), (0x50, 1)])  # activate FIRST, body re-sent
        self.assertEqual(self.emu.active_profile, 1)
        self.assertEqual(self.emu.buttons[(1, 1)], 0x7F)
        self.assertEqual(self.emu.buttons[(0, 1)], 0xA6)  # old active profile untouched
        self.assertEqual(results[0].buttons["middle"], 0x7F)

    def test_buttons_of_other_profile_refused_before_any_write(self):
        m = self.model
        m.select(2)
        m.set_button("middle", 0x7F)
        with self.assertRaises(InactiveProfileRemapError):
            self.session.apply([m.drafts[2]], activate_profile=1)
        self.assertEqual(self.emu.writes, [])

    def test_flag_off_never_sends_0x45(self):
        m = self.model
        m.select(1)
        m.set_polling(500)
        with mock.patch("nzxt_lift_elite.gui.model.ENABLE_ACTIVE_PROFILE_APPLY", False), \
                mock.patch("nzxt_lift_elite.device.ENABLE_ACTIVE_PROFILE_APPLY", False):
            self.assertFalse(m.activation_pending)
            self.session.apply([m.drafts[1]], activate_profile=1)
        self.assertNotIn(0x45, [p[4] for p in self.emu.log])
        self.assertEqual(self.emu.active_profile, 0)


if __name__ == "__main__":
    unittest.main()


class PendingActivationModelTest(unittest.TestCase):
    def setUp(self):
        from nzxt_lift_elite.gui.model import EditorModel, Session

        self.session = Session(mock="wired")
        self.model = EditorModel()
        self.model.load_state(self.session.read_state())

    def tearDown(self):
        self.session.close()

    def test_request_cancel_discard_and_long_press(self):
        m = self.model
        m.request_activation(0)  # already active: no-op
        self.assertIsNone(m.pending_activation)
        m.request_activation(2)
        self.assertTrue(m.has_pending_apply)
        self.assertFalse(m.any_dirty)  # activation alone is not a profile edit
        m.request_activation(None)
        self.assertFalse(m.has_pending_apply)
        m.request_activation(2)
        m.revert()  # «Annulla modifiche»
        self.assertIsNone(m.pending_activation)
        m.request_activation(3)
        change = m.adopt_poll(3, 3, m.original(3).stage)  # long press reached it
        self.assertTrue(change.active_changed)
        self.assertIsNone(m.pending_activation)
