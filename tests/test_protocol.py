"""Unit tests for packet building and parsing, using the CAM captures."""

from __future__ import annotations

import unittest

from nzxt_lift_elite.protocol import (
    ACTION_CODES,
    BUTTON_IDS,
    CAPTURED_BODIES,
    CAPTURED_PROFILE_0_REPLY,
    CMD_GET_ACTIVE_PROFILE,
    CMD_SET_ACTIVE_PROFILE,
    ENABLE_ACTIVE_PROFILE_APPLY,
    PACKET_SIZE,
    POLLING_HZ,
    READ_ONLY_COMMANDS,
    REPLY_OK,
    SAFE_COMMANDS,
    OFF_STAGE,
    apply_body_fields,
    encode_wire_stage,
    decode_wire_stage,
    captured_read_reply,
    encoded_body,
    factory_remap_packets,
    format_action,
    id_word_packet,
    is_busy_reply,
    parse_action,
    parse_active_profile_reply,
    parse_button,
    parse_profile_body,
    parse_profile_reply,
    polling_warning,
    read_active_profile_packet,
    read_profile_packet,
    read_profile_reply_prefix,
    remap_packet,
    set_active_profile_packet,
    status_packet,
    uid_packet,
    write_profile_packet,
    assert_safe_packet,
)
from nzxt_lift_elite.errors import DangerousCommandError, ProtocolError, ValidationError
from nzxt_lift_elite.profile import Profile


class CapturedProfilesTest(unittest.TestCase):
    EXPECTED = {
        0: dict(
            polling_hz=8000,
            liftoff=1,
            stage_count=5,
            stage=2,
            dpi=(400, 800, 1600, 3200, 6400),
            stage_mask=0x0F,
            color=bytes.fromhex("1f00"),
            motion_sync=True,
        ),
        1: dict(
            polling_hz=1000,
            liftoff=1,
            stage_count=5,
            stage=2,
            dpi=(400, 800, 1600, 3200, 6400),
            stage_mask=0x0F,
            color=bytes.fromhex("00f8"),
            motion_sync=True,
        ),
        2: dict(
            polling_hz=2000,
            liftoff=1,
            stage_count=5,
            stage=2,
            dpi=(800, 1200, 1600, 3200, 6400),
            stage_mask=0x0F,
            color=bytes.fromhex("e007"),
            motion_sync=True,
        ),
        3: dict(
            polling_hz=1000,
            liftoff=1,
            stage_count=5,
            stage=3,
            dpi=(400, 800, 1600, 3200, 6400),
            stage_mask=0x0F,
            color=bytes.fromhex("40fd"),
            motion_sync=True,
        ),
        4: dict(
            polling_hz=1000,
            liftoff=1,
            stage_count=5,
            stage=2,
            dpi=(400, 800, 1600, 3200, 6400),
            stage_mask=0x0F,
            color=bytes.fromhex("1ff8"),
            motion_sync=True,
        ),
    }

    def test_profile_0_hex_matches_documented_string(self):
        # PROTOCOL.md wraps the hex with a space; the reconstructed bytes must
        # equal the concatenated form of that line.
        documented = bytes.fromhex("4e029400010005010501900120034006800c00190f1f0001")
        self.assertEqual(CAPTURED_PROFILE_0_REPLY, documented)
        self.assertEqual(
            encoded_body(captured_read_reply(0)),
            CAPTURED_BODIES[0],
        )

    def test_round_trip_all_five_captured_bodies(self):
        for index, expected in self.EXPECTED.items():
            with self.subTest(profile=index):
                body = parse_profile_body(CAPTURED_BODIES[index], index)
                self.assertEqual(body.index, index)
                for field, value in expected.items():
                    self.assertEqual(getattr(body, field), value, field)
                # Re-encode known body (bytes 6..23) and compare to the capture.
                rebuilt = captured_read_reply(index)
                self.assertEqual(encoded_body(rebuilt), CAPTURED_BODIES[index])
                # apply_body_fields with no changes is a no-op on the known body.
                same = apply_body_fields(rebuilt)
                self.assertEqual(encoded_body(same), CAPTURED_BODIES[index])
                # Profile.with_changes likewise.
                profile = Profile.from_body(body)
                self.assertEqual(profile.known_body(), CAPTURED_BODIES[index])
                self.assertEqual(profile.with_changes().known_body(), CAPTURED_BODIES[index])

    def test_write_packet_uses_0x93_and_preserves_body(self):
        for index in range(5):
            reply = captured_read_reply(index)
            pkt = write_profile_packet(index, reply)
            self.assertEqual(len(pkt), PACKET_SIZE)
            self.assertEqual(pkt[:6], bytes((0x4E, 0x02, 0x94, 0x00, 0x93, index)))
            self.assertEqual(encoded_body(pkt), CAPTURED_BODIES[index])
            # The rest of the 64-byte packet stays zero in these fixtures.
            self.assertEqual(pkt[24:], bytes(PACKET_SIZE - 24))

    def test_dpi_stage_2_800_to_1000_matches_capture(self):
        # PROTOCOL.md: changing stage 2 from 800 to 1000 set bytes 12-13 to e8 03.
        reply = captured_read_reply(0)
        self.assertEqual(reply[12:14], bytes.fromhex("2003"))
        changed = apply_body_fields(reply, dpi_stages={2: 1000})
        self.assertEqual(changed[12:14], bytes.fromhex("e803"))
        parsed = parse_profile_reply(changed, expected_index=0)
        self.assertEqual(parsed.dpi[1], 1000)
        # Other stages untouched.
        self.assertEqual(parsed.dpi[0], 400)
        self.assertEqual(parsed.dpi[2], 1600)


class CommandsTest(unittest.TestCase):
    def test_read_profile_packet(self):
        pkt = read_profile_packet(3)
        self.assertEqual(len(pkt), 64)
        self.assertEqual(pkt[:6], bytes.fromhex("4e0282009203"))
        self.assertEqual(pkt[6:], bytes(58))
        self.assertEqual(read_profile_reply_prefix(3), bytes.fromhex("4e0294000103"))

    def test_status_packet(self):
        pkt = status_packet()
        self.assertEqual(pkt[:5], bytes.fromhex("4e028100b0"))
        self.assertEqual(len(pkt), 64)

    def test_active_profile_packets(self):
        get = read_active_profile_packet()
        self.assertEqual(get[:5], bytes.fromhex("4e02810044"))
        self.assertEqual(len(get), 64)
        self.assertEqual(parse_active_profile_reply(bytes.fromhex("4e0282000103") + bytes(58)), 3)
        with self.assertRaises(ValidationError):
            parse_active_profile_reply(bytes.fromhex("4e0282000105") + bytes(58))
        set_pkt = set_active_profile_packet(2)
        self.assertEqual(set_pkt[:6], bytes.fromhex("4e0282004502"))
        self.assertEqual(uid_packet()[:5], bytes.fromhex("4e02810003"))
        self.assertEqual(id_word_packet()[:5], bytes.fromhex("4e028100b8"))
        self.assertFalse(ENABLE_ACTIVE_PROFILE_APPLY)
        self.assertIn(CMD_GET_ACTIVE_PROFILE, SAFE_COMMANDS)
        self.assertIn(CMD_GET_ACTIVE_PROFILE, READ_ONLY_COMMANDS)
        self.assertNotIn(CMD_SET_ACTIVE_PROFILE, SAFE_COMMANDS)
        self.assertNotIn(CMD_SET_ACTIVE_PROFILE, READ_ONLY_COMMANDS)
        assert_safe_packet(get)
        with self.assertRaises(DangerousCommandError):
            assert_safe_packet(set_pkt)

    def test_busy_reply(self):
        self.assertTrue(is_busy_reply(bytes.fromhex("4ee500") + bytes(61)))
        self.assertFalse(is_busy_reply(bytes.fromhex("4e02940001")))

    def test_polling_map_is_complete(self):
        self.assertEqual(
            [POLLING_HZ[i] for i in range(6)],
            [250, 500, 1000, 2000, 4000, 8000],
        )
        self.assertIsNotNone(polling_warning(4000))
        self.assertIsNotNone(polling_warning(8000))
        self.assertIsNone(polling_warning(1000))

    def test_remap_packets(self):
        # 4e 02 85 00 50 PP BB 04 AA, PP = onboard profile
        pkt = remap_packet(0, "forward", "back")
        self.assertEqual(pkt[:9], bytes.fromhex("4e02850050000304a9"))
        self.assertEqual(len(pkt), 64)
        pkt3 = remap_packet(3, "forward", "volume-up")
        self.assertEqual(pkt3[:9], bytes.fromhex("4e02850050030304" "80"))
        # Keyboard usage: A is 0x04.
        a_pkt = remap_packet(0, "dpi", "key:a")
        self.assertEqual(a_pkt[6:9], bytes((5, 0x04, 0x04)))
        self.assertEqual(parse_action("key:0x04"), 0x04)
        self.assertEqual(format_action(0x04), "a")
        self.assertEqual(format_action(0xA8), "mouse-forward")
        self.assertEqual(format_action(0xF0), "key:0xf0")

    def test_factory_reset_order(self):
        packets = factory_remap_packets(2)
        # (2,a7)(4,a9)(1,a6)(5,aa)(0,a5)(3,a8)
        expected = [(2, 0xA7), (4, 0xA9), (1, 0xA6), (5, 0xAA), (0, 0xA5), (3, 0xA8)]
        self.assertEqual(len(packets), 6)
        for pkt, (button, action) in zip(packets, expected, strict=True):
            self.assertEqual(pkt[5], 2)
            self.assertEqual(pkt[6], button)
            self.assertEqual(pkt[7], 0x04)
            self.assertEqual(pkt[8], action)

    def test_bad_values_rejected(self):
        with self.assertRaises(ValidationError):
            read_profile_packet(5)
        with self.assertRaises(ValidationError):
            parse_button("thumb")
        with self.assertRaises(ValidationError):
            parse_action("key:")
        with self.assertRaises(ValidationError):
            parse_action("jump")
        with self.assertRaises(ProtocolError):
            parse_profile_body(b"\x00\x01", 0)


class ProfileEditTest(unittest.TestCase):
    def test_individual_dpi_stage_and_other_fields(self):
        profile = Profile.from_reply(captured_read_reply(1), expected_index=1)
        edited = (
            profile.with_changes(polling_hz=500, liftoff=2, motion_sync=False, stage=3)
            .set_dpi_stage(2, 1000)
        )
        self.assertEqual(edited.polling_hz, 500)
        self.assertEqual(edited.liftoff, 2)
        self.assertFalse(edited.motion_sync)
        self.assertEqual(edited.stage, 3)
        self.assertEqual(edited.raw[OFF_STAGE], 2)
        self.assertEqual(edited.dpi[1], 1000)
        self.assertEqual(edited.dpi[0], 400)
        # Raw trailing bytes (after the known body) stay zeros, colour preserved.
        self.assertEqual(edited.color, bytes.fromhex("00f8"))
        self.assertEqual(edited.index, 1)
        # Original object is unchanged (frozen dataclass).
        self.assertEqual(profile.polling_hz, 1000)
        self.assertEqual(profile.dpi[1], 800)


    def test_active_stage_is_zero_based_on_the_wire(self):
        # Captured profiles stored 01 or 02; those are 0-based (user stages 2 and 3).
        reply = captured_read_reply(0)
        self.assertEqual(reply[OFF_STAGE], 1)
        parsed = parse_profile_reply(reply, expected_index=0)
        self.assertEqual(parsed.stage, 2)
        self.assertEqual(parsed.current_dpi, 800)
        # User stage 1 encodes as byte 0 and round-trips.
        written = apply_body_fields(reply, stage=1)
        self.assertEqual(written[OFF_STAGE], 0)
        self.assertEqual(parse_profile_reply(written, expected_index=0).stage, 1)
        self.assertEqual(parse_profile_reply(written, expected_index=0).current_dpi, 400)
        written = apply_body_fields(reply, stage=5)
        self.assertEqual(written[OFF_STAGE], 4)
        self.assertEqual(parse_profile_reply(written, expected_index=0).stage, 5)
        from nzxt_lift_elite.errors import ValidationError
        with self.assertRaises(ValidationError):
            apply_body_fields(reply, stage=0)
        with self.assertRaises(ValidationError):
            apply_body_fields(reply, stage=6)
        bad = bytearray(reply)
        bad[OFF_STAGE] = 5
        with self.assertRaises(ProtocolError):
            parse_profile_reply(bytes(bad), expected_index=0)
        self.assertEqual(decode_wire_stage(0), 1)
        self.assertEqual(encode_wire_stage(1), 0)


    def test_full_dpi_list(self):
        profile = Profile.from_reply(captured_read_reply(0), expected_index=0)
        edited = profile.with_changes(dpi=(400, 800, 1200, 2400, 6400))
        self.assertEqual(edited.dpi, (400, 800, 1200, 2400, 6400))
        self.assertEqual(edited.stage_count, 5)
        self.assertEqual(edited.stage_mask, 0x0F)  # preserved from the capture


if __name__ == "__main__":
    unittest.main()
