"""Battery (b0 status): decoding, emulator, LiftElite.read_battery, liftctl battery."""

from __future__ import annotations

import contextlib
import io
import json
import os
import unittest
from unittest import mock

from nzxt_lift_elite import cli, i18n
from nzxt_lift_elite.errors import ProtocolError
from nzxt_lift_elite.mock import EmulatedBattery, MouseEmulator, battery_from_env, mv_for_percent, open_mock
from nzxt_lift_elite.protocol import (
    CAPTURED_STATUS_REPLY,
    CHARGE_CHARGING,
    CHARGE_DISCHARGING,
    CHARGE_FULL,
    CMD_STATUS,
    DANGEROUS_COMMANDS,
    READ_ONLY_COMMANDS,
    SAFE_COMMANDS,
    battery_percent_from_mv,
    build_status_reply,
    pad_packet,
    parse_battery_reply,
)

# receiver capture: 4219 mV, 25 °C, full
DONGLE_STATUS_REPLY = bytes.fromhex("4e0297000140017b1000004001400100006400640019020800400100")


class ParseTest(unittest.TestCase):
    def test_captured_wired_reply(self):
        b = parse_battery_reply(CAPTURED_STATUS_REPLY)
        self.assertTrue(b.available)
        self.assertEqual(b.percent, 100)
        self.assertEqual(b.millivolts, 4201)  # bytes 7..8 = 69 10, little-endian
        self.assertEqual(b.charge_state, CHARGE_FULL)
        self.assertFalse(b.charging)
        self.assertTrue(b.external_power)
        self.assertEqual((b.current_ma, b.temperature_c, b.health_percent, b.cycles), (0, 24, 100, 0))
        self.assertEqual((b.remaining_mah, b.capacity_mah), (320, 320))
        self.assertFalse(b.estimated)
        self.assertEqual(len(b.raw), 23)

    def test_captured_receiver_reply(self):
        b = parse_battery_reply(DONGLE_STATUS_REPLY)
        self.assertEqual((b.percent, b.millivolts, b.temperature_c, b.charge_state_name), (100, 4219, 25, "full"))

    def test_build_matches_capture(self):
        self.assertEqual(build_status_reply(), pad_packet(CAPTURED_STATUS_REPLY))

    def test_round_trip_discharging(self):
        pkt = build_status_reply(percent=37, millivolts=3761, charge_state=CHARGE_DISCHARGING, current_ma=-12,
                                 temperature_c=-5, cycles=42, health_percent=97)
        b = parse_battery_reply(pkt)
        self.assertEqual((b.percent, b.millivolts, b.current_ma, b.temperature_c, b.cycles, b.health_percent),
                         (37, 3761, -12, -5, 42, 97))
        self.assertEqual(b.charge_state_name, "discharging")
        self.assertFalse(b.external_power)
        self.assertEqual(b.remaining_mah, 320 * 37 // 100)

    def test_charging_flag(self):
        b = parse_battery_reply(build_status_reply(percent=60, millivolts=4010, charge_state=CHARGE_CHARGING,
                                                   current_ma=150))
        self.assertTrue(b.charging)
        self.assertEqual(b.to_dict()["charge_state"], "charging")

    def test_gauge_not_valid_is_unknown(self):
        b = parse_battery_reply(build_status_reply(valid=False))
        self.assertFalse(b.available)
        self.assertIsNone(b.percent)

    def test_garbage_voltage_is_unknown(self):
        for mv in (0, 1200, 5000):
            with self.subTest(mv=mv):
                self.assertFalse(parse_battery_reply(build_status_reply(millivolts=mv)).available)

    def test_soc_out_of_range_falls_back_to_voltage(self):
        b = parse_battery_reply(build_status_reply(percent=250, millivolts=3800))
        self.assertTrue(b.available)
        self.assertTrue(b.estimated)
        self.assertEqual(b.percent, 50)

    def test_not_a_status_reply(self):
        with self.assertRaises(ProtocolError):
            parse_battery_reply(bytes.fromhex("4e02820001"))

    def test_voltage_curve(self):
        self.assertEqual(battery_percent_from_mv(3000), 0)
        self.assertEqual(battery_percent_from_mv(4300), 100)
        values = [battery_percent_from_mv(mv) for mv in range(3300, 4201, 10)]
        self.assertEqual(values, sorted(values))
        for pct in (0, 10, 50, 90, 100):
            self.assertAlmostEqual(battery_percent_from_mv(mv_for_percent(pct)), pct, delta=1)

    def test_b0_is_a_safe_read(self):
        self.assertIn(CMD_STATUS, SAFE_COMMANDS)
        self.assertIn(CMD_STATUS, READ_ONLY_COMMANDS)
        self.assertNotIn(CMD_STATUS, DANGEROUS_COMMANDS)


class DeviceTest(unittest.TestCase):
    def _open(self, battery: EmulatedBattery, transport: str = "wired"):
        emu = MouseEmulator(battery=battery)
        dev, fake = open_mock(transport, emulator=emu)
        self.addCleanup(fake.close)
        return dev, emu

    def test_read_battery_sends_only_b0(self):
        dev, emu = self._open(EmulatedBattery.at(42, CHARGE_DISCHARGING))
        b = dev.read_battery()
        self.assertEqual(b.percent, 42)
        self.assertEqual({p[4] for p in emu.log}, {CMD_STATUS})
        self.assertEqual(emu.unsafe, [])
        self.assertEqual(emu.writes, [])

    def test_read_battery_through_receiver(self):
        dev, emu = self._open(EmulatedBattery.at(64), "wireless")
        self.assertEqual(dev.read_battery().percent, 64)
        self.assertEqual(emu.writes, [])

    def test_gauge_flag_zero_still_answers(self):
        bat = EmulatedBattery.at(10)
        bat.valid = False
        dev, _emu = self._open(bat)
        self.assertFalse(dev.read_battery().available)  # 4e 02 97 00 00 … matched, not a timeout

    def test_read_state_includes_battery(self):
        dev, _emu = self._open(EmulatedBattery.at(88, CHARGE_CHARGING))
        state = dev.read_state(with_buttons=False)
        self.assertEqual(state.battery.percent, 88)
        self.assertTrue(state.battery.charging)

    def test_asleep_raises(self):
        emu = MouseEmulator(asleep=True)
        dev, fake = open_mock("wireless", emulator=emu, retries=1, timeout=0.05)
        self.addCleanup(fake.close)
        with self.assertRaises(ProtocolError):
            dev.read_battery()

    def test_mock_defaults_and_env(self):
        self.assertEqual(battery_from_env("wired", "").charge_state, CHARGE_CHARGING)
        self.assertEqual(battery_from_env("wireless", "").charge_state, CHARGE_DISCHARGING)
        b = battery_from_env("wired", "15:full")
        self.assertEqual((b.percent, b.charge_state), (15, CHARGE_FULL))
        self.assertFalse(battery_from_env("wired", "unknown").valid)
        self.assertEqual(battery_from_env("wired", "zz").percent, 64)
        with mock.patch.dict(os.environ, {"LIFT_ELITE_MOCK_BATTERY": "33:charging"}):
            dev, fake = open_mock("wired")
            self.addCleanup(fake.close)
            self.assertEqual(dev.read_battery().percent, 33)


class CliTest(unittest.TestCase):
    def setUp(self):
        i18n.set_language("en")
        self.addCleanup(i18n.set_language, "it")

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--mock", *argv])
        return code, out.getvalue(), err.getvalue()

    def test_battery_text(self):
        code, out, _err = self.run_cli("battery")
        self.assertEqual(code, 0)
        self.assertIn("Battery:      72 %", out)
        self.assertIn("Charging:     charging", out)
        self.assertIn("Voltage:      4.", out)
        self.assertIn("Raw b0:       01 40 01", out)

    def test_battery_italian(self):
        i18n.set_language("it")
        code, out, _err = self.run_cli("--transport", "wireless", "battery")
        self.assertEqual(code, 0)
        self.assertIn("Batteria:     64 %", out)
        self.assertIn("a batteria", out)

    def test_battery_json(self):
        code, out, _err = self.run_cli("battery", "--json")
        data = json.loads(out)
        self.assertEqual((data["percent"], data["charging"], data["charge_state"]), (72, True, "charging"))
        self.assertEqual(len(bytes.fromhex(data["raw"])), 23)

    def test_battery_watch(self):
        with mock.patch("nzxt_lift_elite.cli.time.sleep") as sleep:
            code, out, _err = self.run_cli("battery", "--watch", "30", "--count", "3")
        self.assertEqual(code, 0)
        lines = out.strip().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertRegex(lines[0], r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d +72 %  \d+ mV  charging")
        self.assertEqual(sleep.call_count, 2)
        sleep.assert_called_with(30.0)

    def test_battery_watch_json(self):
        with mock.patch("nzxt_lift_elite.cli.time.sleep"):
            _code, out, _err = self.run_cli("battery", "--watch", "1", "--count", "2", "--json")
        rows = [json.loads(line) for line in out.strip().splitlines()]
        self.assertEqual(len(rows), 2)
        self.assertIn("time", rows[0])
        self.assertEqual(rows[1]["percent"], 72)

    def test_battery_watch_rejects_zero(self):
        code, _out, err = self.run_cli("battery", "--watch", "0")
        self.assertEqual(code, 1)
        self.assertIn("--watch", err)


if __name__ == "__main__":
    unittest.main()
