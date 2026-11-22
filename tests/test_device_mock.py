"""LiftElite against a fake hidraw node (socket pair + emulator thread)."""

from __future__ import annotations

import os
import unittest

from nzxt_lift_elite.device import LiftElite
from nzxt_lift_elite.errors import DangerousCommandError, ProtocolError, VerificationError
from nzxt_lift_elite.mock import FakeHidraw, MouseEmulator, fake_info, open_mock
from nzxt_lift_elite.profile import ProfileConfig
from nzxt_lift_elite.protocol import (
    CAPTURED_BODIES,
    CMD_GET_ACTIVE_PROFILE,
    CMD_READ_BUTTON,
    CMD_SET_ACTIVE_PROFILE,
    CMD_WRITE_BUTTON,
    CMD_WRITE_PROFILE,
    DANGEROUS_COMMANDS,
    ENABLE_ACTIVE_PROFILE_APPLY,
    READ_ONLY_COMMANDS,
    command_of,
    header,
    pad_packet,
    set_active_profile_packet,
)


class DeviceMockTest(unittest.TestCase):
    def setUp(self):
        self.emu = MouseEmulator(busy_every=3)
        self.dev, self.fake = open_mock(emulator=self.emu)

    def tearDown(self):
        self.fake.close()

    def test_read_state_is_read_only(self):
        state = self.dev.read_state()
        self.assertEqual(state.firmware.name, "HyperSonic")
        self.assertEqual(state.firmware.fmt(state.firmware.app_version), "1.1.1")
        self.assertIsNone(state.receiver_firmware)  # wired
        self.assertEqual(len(state.profiles), 5)
        for p in state.profiles:
            self.assertEqual(p.known_body(), CAPTURED_BODIES[p.index])
            self.assertEqual(p.buttons["forward"], 0xA8)
        self.assertEqual(state.active_profile, 0)
        self.assertEqual(self.emu.writes, [])
        self.assertEqual(self.emu.unsafe, [])
        # 5 profiles x (1 profile read + 6 button reads) + 0b + 0x44
        reads = [p for p in self.emu.log if p[4] == CMD_READ_BUTTON]
        self.assertEqual(len(reads), 30)
        self.assertEqual(reads[0][:8], bytes.fromhex("4e02840051000000"))

    def test_apply_writes_only_differences_and_verifies(self):
        p0 = self.dev.read_profile(0)
        target = p0.with_changes(polling_hz=1000, liftoff=2, dpi_stages={5: 26000}, motion_sync=False,
                                 buttons={"forward": "volume-up", "dpi": "key:0x04"})
        result = self.dev.apply_profile(target)
        self.assertEqual(result.polling_hz, 1000)
        self.assertEqual(result.liftoff, 2)
        self.assertEqual(result.dpi[4], 26000)
        self.assertFalse(result.motion_sync)
        self.assertEqual(result.buttons["forward"], 0x80)
        self.assertEqual(result.buttons["dpi"], 0x04)
        kinds = [p[4] for p in self.emu.writes]
        self.assertEqual(kinds, [CMD_WRITE_PROFILE, CMD_WRITE_BUTTON, CMD_WRITE_BUTTON])
        # remap carries the profile index
        self.assertEqual(self.emu.writes[1][:9], bytes.fromhex("4e0285005000030480"))
        # Unknown trailing bytes / colour preserved
        self.assertEqual(self.emu.profiles[0][21:23], bytes.fromhex("1f00"))
        # Same target again: nothing written
        self.dev.apply_profile(target)
        self.assertEqual(len(self.emu.writes), 3)

    def test_remap_other_profile(self):
        self.dev.remap(4, "back", "previous-track")
        self.assertEqual(self.emu.buttons[(4, 4)], 0xEA)
        self.assertEqual(self.emu.buttons[(0, 4)], 0xA9)

    def test_apply_config_from_file_fields(self):
        cfg = ProfileConfig(dpi_stages=(800, 1200, 1600, 3200, 6400), dpi_stage=3,
                            buttons={"left": 0xA5, "right": 0xA6})
        result = self.dev.apply_config(2, cfg)
        self.assertEqual(result.dpi_stages, (800, 1200, 1600, 3200, 6400))
        self.assertEqual(result.stage, 3)
        self.assertEqual(self.emu.buttons[(2, 2)], 0xA6)


    def test_apply_profile_writes_colour(self):
        cur = self.dev.read_profile(1)
        target = cur.with_changes(polling_hz=500, color=bytes.fromhex("ffff"))
        back = self.dev.apply_profile(target)
        self.assertEqual(back.polling_hz, 500)
        self.assertEqual(back.color, bytes.fromhex("ffff"))
        body_writes = [w for w in self.emu.writes if len(w) >= 23 and w[4] == 0x93]
        self.assertEqual(body_writes[-1][21:23], bytes.fromhex("ffff"))

    def test_restore_factory_led_colors(self):
        from nzxt_lift_elite.protocol import FACTORY_LED_COLORS
        dirty = bytes.fromhex("ffff")
        for index in (1, 4):
            p = self.dev.read_profile(index, with_buttons=False)
            self.dev.write_profile_body(p.with_changes(color=dirty), verify=False)
        # other fields on slot 1 must stay
        before = self.dev.read_profile(1, with_buttons=False)
        self.assertEqual(before.color, dirty)
        results = self.dev.restore_factory_led_colors()
        self.assertEqual([p.color for p in results], list(FACTORY_LED_COLORS))
        again = self.dev.read_profile(1, with_buttons=False)
        self.assertEqual(again.polling_hz, before.polling_hz)
        self.assertEqual(again.dpi_stages, before.dpi_stages)
        # already factory: no extra body write
        n = len(self.emu.writes)
        self.dev.restore_factory_led_colors([0])
        self.assertEqual(len(self.emu.writes), n)

    def test_verification_failure(self):
        self.emu.ignore_writes = True
        p1 = self.dev.read_profile(1)
        with self.assertRaises(VerificationError):
            self.dev.apply_profile(p1.with_changes(polling_hz=500))
        with self.assertRaises(VerificationError):
            self.dev.remap(1, "left", "right-click")

    def test_remap_reset(self):
        self.emu.buttons[(3, 0)] = 0x04
        self.dev.remap_reset(3)
        self.assertEqual([self.emu.buttons[(3, b)] for b in range(6)], [0xA5, 0xA6, 0xA7, 0xA8, 0xA9, 0xAA])

    def test_dangerous_commands_are_never_sent(self):
        for cmd in sorted(DANGEROUS_COMMANDS):
            with self.subTest(cmd=hex(cmd)):
                with self.assertRaises(DangerousCommandError):
                    self.dev._write(pad_packet(header(cmd, 1) + b"\x01"))
                with self.assertRaises(DangerousCommandError):
                    self.dev.transact(pad_packet(header(cmd)), b"\x4e\x02")
        with self.assertRaises(DangerousCommandError):
            self.dev._write(pad_packet(header(0x46)))  # still-unknown command
        with self.assertRaises(DangerousCommandError):
            self.dev._write(set_active_profile_packet(1))  # 0x45 not enabled
        self.assertFalse(ENABLE_ACTIVE_PROFILE_APPLY)
        self.assertNotIn(CMD_SET_ACTIVE_PROFILE, READ_ONLY_COMMANDS)
        with self.assertRaises(DangerousCommandError):
            self.dev._write(pad_packet(header(0x93, 0, target=0x01)))  # write to receiver
        self.assertEqual(self.emu.unsafe, [])
        self.assertEqual(self.emu.log, [])

    def test_dry_run_sends_no_writes(self):
        dev = LiftElite(info=fake_info(), fd=self.fake.fd, dry_run=True, timeout=0.5)
        p0 = dev.read_profile(0)
        dev.apply_profile(p0.with_changes(polling_hz=250, buttons={"left": "a"}))
        self.assertEqual(self.emu.writes, [])

    def test_wireless_asleep_times_out_with_hint(self):
        emu = MouseEmulator(asleep=True)
        dev, fake = open_mock("wireless", emulator=emu, timeout=0.1, retries=2)
        try:
            with self.assertRaises(ProtocolError) as ctx:
                dev.read_profile(0)
            self.assertIn("ricevitore", str(ctx.exception))
            self.assertEqual(len(emu.log), 2)  # reads are retried
        finally:
            fake.close()

    def test_wireless_reads_receiver_info(self):
        dev, fake = open_mock("wireless")
        try:
            state = dev.read_state(with_buttons=False)
            self.assertEqual(dev.transport, "wireless")
            self.assertEqual(state.receiver_firmware.name, "Receiver")
        finally:
            fake.close()

    def test_prepend_zero_report_id_on_write(self):
        dev = LiftElite(info=fake_info(), fd=self.fake.fd)
        self.assertEqual(dev._encode_write(bytes(64))[:1], b"\x00")
        self.assertEqual(len(dev._encode_write(bytes(64))), 65)


    def test_read_active_profile_and_refuses_set(self):
        self.assertEqual(self.dev.read_active_profile(), 0)
        self.emu.active_profile = 3
        self.assertEqual(self.dev.read_active_profile(), 3)
        with self.assertRaises(DangerousCommandError):
            self.dev.set_active_profile(1)
        self.assertEqual(self.emu.writes, [])
        self.assertEqual(self.emu.active_profile, 3)

    def test_debug_dump_sends_only_safe_reads(self):
        rows = self.dev.debug_dump()
        self.assertGreaterEqual(len(rows), 5 + 5 + 30)  # info/uid/44/status/b8 + profiles + buttons
        labels = [r[0] for r in rows]
        self.assertTrue(any(l.startswith("44 ") for l in labels))
        for label, request, reply, error in rows:
            self.assertIsNone(error, msg=f"{label}: {error}")
            self.assertIsNotNone(reply)
            cmd = command_of(request)
            self.assertIn(cmd, READ_ONLY_COMMANDS)
            self.assertNotEqual(cmd, CMD_SET_ACTIVE_PROFILE)
            self.assertNotIn(cmd, DANGEROUS_COMMANDS)
        self.assertEqual(self.emu.writes, [])
        self.assertEqual(self.emu.unsafe, [])
        # 0x44 reply decodes as the emulator's current slot
        forty_four = next(r for r in rows if r[0].startswith("44 "))
        from nzxt_lift_elite.protocol import parse_active_profile_reply
        self.assertEqual(parse_active_profile_reply(forty_four[2]), 0)


class FlockTest(unittest.TestCase):
    def test_lock_on_real_file(self):
        import tempfile

        with tempfile.NamedTemporaryFile() as tmp:
            fd1 = os.open(tmp.name, os.O_RDWR)
            fd2 = os.open(tmp.name, os.O_RDWR)
            try:
                a = LiftElite(info=fake_info(), fd=fd1, lock_timeout=0.2)
                b = LiftElite(info=fake_info(), fd=fd2, lock_timeout=0.2)
                from nzxt_lift_elite.errors import DeviceBusyError

                with a.locked():
                    with a.locked():  # re-entrant
                        with self.assertRaises(DeviceBusyError):
                            with b.locked():
                                pass
                with b.locked():
                    pass
            finally:
                os.close(fd1)
                os.close(fd2)


if __name__ == "__main__":
    unittest.main()
