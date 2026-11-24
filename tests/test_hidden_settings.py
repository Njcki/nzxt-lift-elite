"""Sleep timeout (byte 20), DPI stage count (byte 8), wireless 8000 Hz, the
read-only bc link status and the newly banned commands (against the emulator)."""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from nzxt_lift_elite import cli, i18n
from nzxt_lift_elite.device import FACTORY_SETTINGS
from nzxt_lift_elite.errors import DangerousCommandError, ProtocolError, ValidationError
from nzxt_lift_elite.gui.model import (
    AFK_CHOICES,
    EditorModel,
    Session,
    afk_choices,
    connection_subtitle,
    link_tooltip,
)
from nzxt_lift_elite.mock import MouseEmulator, open_mock
from nzxt_lift_elite.profile import Profile, loads_profiles
from nzxt_lift_elite.protocol import (
    CMD_LINK_STATUS,
    DANGEROUS_COMMANDS,
    EXPLICIT_ALLOWLIST,
    OFF_AFK,
    OFF_MASK,
    OFF_STAGE,
    OFF_STAGE_COUNT,
    READ_ONLY_COMMANDS,
    SAFE_COMMANDS,
    LinkStatus,
    apply_body_fields,
    assert_safe_packet,
    build_link_status_reply,
    captured_read_reply,
    header,
    link_status_packet,
    pad_packet,
    parse_link_status_reply,
    parse_profile_reply,
    polling_warning,
    set_active_profile_packet,
    validate_afk_minutes,
    validate_stage_count,
    write_profile_packet,
)
from nzxt_lift_elite.store import ProfileStore

NEW_BANS = {0x94: "factory reset", 0xB1: "power word", 0xB9: "config page", 0xBA: "flag", 0xC0: "factory/test"}


def _p(i: int = 0) -> Profile:
    return Profile.from_reply(captured_read_reply(i), expected_index=i)


class AfkFieldTest(unittest.TestCase):
    def test_byte20_is_afk_minutes(self):
        self.assertEqual(OFF_AFK, 20)
        self.assertEqual(OFF_MASK, OFF_AFK)  # legacy name kept, same byte
        for i in range(5):
            self.assertEqual(parse_profile_reply(captured_read_reply(i)).afk_minutes, 15)
            self.assertFalse(hasattr(_p(i), "stage_mask"))

    def test_set_and_validate(self):
        for v in (0, 1, 15, 60, 255):
            p = _p().with_changes(afk_minutes=v)
            self.assertEqual(p.afk_minutes, v)
            self.assertEqual(p.raw[OFF_AFK], v)
        for bad in (-1, 256, 1000, True, 1.5, "15", None):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                validate_afk_minutes(bad)
        with self.assertRaises(ValidationError):
            _p().with_changes(afk_minutes=256)

    def test_only_byte20_changes(self):
        before = _p().raw
        after = _p().with_changes(afk_minutes=1).raw
        diff = [i for i in range(64) if before[i] != after[i]]
        self.assertEqual(diff, [OFF_AFK])


class StageCountTest(unittest.TestCase):
    def test_strict_validation(self):
        for v in (1, 2, 3, 4, 5):
            self.assertEqual(validate_stage_count(v), v)
        for bad in (0, 6, 255, -1, True, False, 3.0, "3", None):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                validate_stage_count(bad)
        with self.assertRaises(ValidationError):
            apply_body_fields(captured_read_reply(0), stage_count=6)
        with self.assertRaises(ValidationError):
            apply_body_fields(captured_read_reply(0), stage_count=0)

    def test_clamp_active_stage_on_write(self):
        p = _p().with_changes(stage=5)
        self.assertEqual(p.stage, 5)
        q = p.with_changes(stage_count=3)
        self.assertEqual((q.stage_count, q.stage), (3, 3))  # clamped to the last enabled stage
        self.assertEqual(q.raw[OFF_STAGE], 2)
        self.assertEqual(q.raw[OFF_STAGE_COUNT], 3)
        self.assertEqual(q.dpi, p.dpi)  # disabled stages keep their values
        self.assertEqual(q.dpi_stages, p.dpi[:3])
        # already inside: untouched
        r = _p().with_changes(stage_count=3)
        self.assertEqual(r.stage, _p().stage)
        # an explicit active stage outside the new count is refused, not clamped
        with self.assertRaises(ValidationError):
            _p().with_changes(stage_count=2, stage=4)
        self.assertEqual(_p().with_changes(stage_count=2, stage=2).stage, 2)
        # 1 stage: the active stage is the first one
        self.assertEqual(_p().with_changes(stage=4).with_changes(stage_count=1).stage, 1)

    def test_dpi_edits_with_fewer_stages(self):
        p = _p().with_changes(stage_count=3)
        q = p.with_changes(dpi=(400, 800, 1200, 2400, 4800))  # always all five values
        self.assertEqual(q.dpi, (400, 800, 1200, 2400, 4800))
        self.assertEqual(p.with_changes(dpi_stages={5: 9000}).dpi[4], 9000)
        with self.assertRaises(ValidationError):
            p.with_changes(dpi=(400, 800, 1200))

    def test_mock_firmware_resets_out_of_range_stage(self):
        emu = MouseEmulator()
        raw = bytearray(captured_read_reply(0))
        raw[OFF_STAGE_COUNT], raw[OFF_STAGE] = 3, 4  # what the library never sends
        emu.handle(write_profile_packet(0, bytes(raw)))
        self.assertEqual(emu.profiles[0][OFF_STAGE], 0)

    def test_purple_button_wraps_after_count(self):
        dev, fake = open_mock("wired")
        try:
            dev.apply_profile(dev.read_profile(0, with_buttons=False).with_changes(stage_count=3))
            emu = fake.emulator
            emu.active_profile = 0
            seen = [emu.press_dpi_button() for _ in range(6)]
            self.assertEqual(max(seen), 2)
            self.assertEqual(dev.read_profile(0, with_buttons=False).stage_count, 3)
        finally:
            dev.close(); fake.close()


class WritePathTest(unittest.TestCase):
    def test_apply_afk_and_count_verified(self):
        dev, fake = open_mock("wireless")
        try:
            cur = dev.read_profile(2, with_buttons=False)
            out = dev.apply_profile(cur.with_changes(afk_minutes=1, stage_count=4, polling_hz=8000))
            self.assertEqual((out.afk_minutes, out.stage_count, out.polling_hz), (1, 4, 8000))
            writes = fake.emulator.writes
            self.assertEqual(len(writes), 1)
            self.assertEqual((writes[0][20], writes[0][8], writes[0][6]), (1, 4, 5))
            self.assertFalse(fake.emulator.unsafe)
        finally:
            dev.close(); fake.close()

    def test_factory_reset_restores_count_and_afk(self):
        for f in FACTORY_SETTINGS:
            self.assertEqual((f["stage_count"], f["afk_minutes"]), (5, 15))
        dev, fake = open_mock("wired")
        try:
            for i in range(5):
                dev.apply_profile(dev.read_profile(i, with_buttons=False).with_changes(
                    afk_minutes=0, stage_count=2, stage=2))
            result = dev.reset_defaults()
            for p in result.results:
                self.assertEqual((p.stage_count, p.afk_minutes, p.stage), (5, 15, p.index + 1))
            sent = {pkt[4] for pkt in fake.emulator.log}
            self.assertFalse(sent & DANGEROUS_COMMANDS)
            self.assertFalse(fake.emulator.unsafe)
        finally:
            dev.close(); fake.close()


class JsonTest(unittest.TestCase):
    def test_export_and_load(self):
        p = _p(1).with_changes(stage_count=3, afk_minutes=30)
        d = p.to_json_dict()
        self.assertEqual(d["dpi_stages"], list(p.dpi))  # all five values
        self.assertEqual((d["dpi_stage_count"], d["afk_minutes"]), (3, 30))
        cfg = loads_profiles(json.dumps({"format": 2, "profiles": [d]}))[0]
        back = cfg.apply_to(_p(1))
        self.assertEqual((back.stage_count, back.afk_minutes, back.dpi), (3, 30, p.dpi))

    def test_old_files_leave_new_fields_alone(self):
        cfg = loads_profiles('{"polling_hz": 1000}')[0]
        base = _p(0).with_changes(stage_count=2, afk_minutes=5)
        out = cfg.apply_to(base)
        self.assertEqual((out.stage_count, out.afk_minutes), (2, 5))

    def test_invalid_values_rejected(self):
        for bad in ('{"dpi_stage_count": 6}', '{"dpi_stage_count": 0}', '{"afk_minutes": 300}',
                    '{"afk_minutes": -1}', '{"dpi_stage_count": 2, "dpi_stage": 3}'):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                loads_profiles(bad)

    def test_named_profile_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ProfileStore(Path(tmp))
            store.save_named("Office", _p(0).with_changes(stage_count=2, afk_minutes=60))
            cfg = store.load_named("Office")
            self.assertEqual((cfg.dpi_stage_count, cfg.afk_minutes), (2, 60))
            out = cfg.apply_to(_p(3), target_index=3)
            self.assertEqual((out.stage_count, out.afk_minutes, out.index), (2, 60, 3))


class LinkStatusTest(unittest.TestCase):
    def test_parse(self):
        st = parse_link_status_reply(build_link_status_reply(cable_power=True, usb_data=False, bridge_link=True))
        self.assertEqual((st.cable_power, st.usb_data, st.bridge_link, st.bits), (True, False, True, 0b101))
        noisy = parse_link_status_reply(pad_packet(bytes.fromhex("4e028100fa")))
        self.assertEqual(noisy.bits, 0b010)  # bits 3..7 masked off
        self.assertEqual(noisy.to_dict(), {"bits": 2, "cable_power": False, "usb_data": True, "internal_link": False})
        with self.assertRaises(ProtocolError):
            parse_link_status_reply(pad_packet(bytes.fromhex("4e029700")))

    def test_bc_is_a_safe_read(self):
        self.assertEqual(CMD_LINK_STATUS, 0xBC)
        self.assertIn(CMD_LINK_STATUS, SAFE_COMMANDS)
        self.assertIn(CMD_LINK_STATUS, READ_ONLY_COMMANDS)
        self.assertNotIn(CMD_LINK_STATUS, DANGEROUS_COMMANDS)
        pkt = link_status_packet()
        self.assertEqual(pkt[:5], bytes.fromhex("4e028100bc"))
        self.assertFalse(any(pkt[5:]))
        assert_safe_packet(pkt)

    def test_device_reads(self):
        for transport, bits in (("wired", 0b111), ("wireless", 0)):
            dev, fake = open_mock(transport)
            try:
                self.assertEqual(dev.read_link_status().bits, bits)
                self.assertEqual(dev.read_state(with_buttons=False).link.bits, bits)
                self.assertFalse(fake.emulator.writes)
            finally:
                dev.close(); fake.close()

    def test_no_reply_does_not_break_the_state_read(self):
        emu = MouseEmulator(link_bits=None)
        dev, fake = open_mock("wireless", emulator=emu)
        try:
            t0 = time.monotonic()
            state = dev.read_state(with_buttons=False)
            self.assertIsNone(state.link)
            self.assertEqual(len(state.profiles), 5)
            self.assertLess(time.monotonic() - t0, 3.0)
            self.assertEqual(sum(1 for p in emu.log if p[4] == CMD_LINK_STATUS), 1)  # one try only
        finally:
            dev.close(); fake.close()

    def test_gui_texts(self):
        i18n.set_language("en")
        self.addCleanup(i18n.set_language, "it")
        st = LinkStatus(0b001)
        self.assertEqual(connection_subtitle("Wireless (receiver)", st, "/dev/x"), "Wireless (receiver) · cable power · /dev/x")
        self.assertEqual(connection_subtitle("Cable", LinkStatus(0b111), None), "Cable")
        self.assertEqual(connection_subtitle("Cable", None, "/dev/x"), "Cable · /dev/x")
        tip = link_tooltip(LinkStatus(0b110))
        self.assertIn("USB cable power: no", tip)
        self.assertIn("USB data (wired mode): yes", tip)
        self.assertIn("8K bridge: up", tip)
        self.assertIsNone(link_tooltip(None))


class DangerousCommandsTest(unittest.TestCase):
    def test_new_bans_listed(self):
        for cmd in NEW_BANS:
            self.assertIn(cmd, DANGEROUS_COMMANDS)
            self.assertNotIn(cmd, SAFE_COMMANDS)
            self.assertNotIn(cmd, READ_ONLY_COMMANDS)
        for old in (0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0xF1, 0xF4, 0xF5, 0xF6):
            self.assertIn(old, DANGEROUS_COMMANDS)

    def test_refused_by_the_guard(self):
        for cmd in NEW_BANS:
            for payload in (b"", b"\x05", b"\x00\x00\x00\x00"):
                pkt = pad_packet(header(cmd, len(payload)) + payload)
                with self.subTest(cmd=hex(cmd), payload=payload.hex()), self.assertRaises(DangerousCommandError):
                    assert_safe_packet(pkt)
                with self.assertRaises(DangerousCommandError):
                    assert_safe_packet(pkt, allow=EXPLICIT_ALLOWLIST)
                with self.assertRaises(DangerousCommandError):
                    assert_safe_packet(pkt, allow=frozenset({cmd}))

    def test_never_reach_the_mouse(self):
        dev, fake = open_mock("wired")
        try:
            for cmd in NEW_BANS:
                pkt = pad_packet(header(cmd, 4) + bytes(4))
                with self.subTest(cmd=hex(cmd)):
                    with self.assertRaises(DangerousCommandError):
                        dev.transact(pkt, bytes.fromhex("4e02"))
                    with self.assertRaises(DangerousCommandError):
                        dev._write(pkt)
            self.assertFalse(any(p[4] in NEW_BANS for p in fake.emulator.log))
            self.assertFalse(fake.emulator.unsafe)
        finally:
            dev.close(); fake.close()

    def test_0x45_allowlist_unchanged(self):
        self.assertEqual(EXPLICIT_ALLOWLIST, frozenset({0x45}))
        assert_safe_packet(set_active_profile_packet(2), allow=EXPLICIT_ALLOWLIST)
        bad = bytearray(set_active_profile_packet(2)); bad[6] = 1
        with self.assertRaises(DangerousCommandError):
            assert_safe_packet(bytes(bad), allow=EXPLICIT_ALLOWLIST)


class PollingTest(unittest.TestCase):
    def test_8000_allowed_over_the_receiver(self):
        dev, fake = open_mock("wireless")
        try:
            out = dev.apply_profile(dev.read_profile(1, with_buttons=False).with_changes(polling_hz=8000))
            self.assertEqual(out.polling_hz, 8000)
        finally:
            dev.close(); fake.close()

    def test_notes(self):
        i18n.set_language("en")
        self.addCleanup(i18n.set_language, "it")
        self.assertIsNone(polling_warning(8000, "wired"))
        self.assertIsNone(polling_warning(1000, "wired"))
        hp = polling_warning(8000, "wireless")
        self.assertIn("4000 Hz", hp)
        self.assertIn("high-performance", hp)
        for hz in (250, 500, 1000, 2000, 4000):
            self.assertIn("power-saving", polling_warning(hz, "wireless"))
        self.assertIsNotNone(polling_warning(8000))
        self.assertIsNone(polling_warning(1000))


class ModelTest(unittest.TestCase):
    def setUp(self):
        i18n.set_language("en")
        self.addCleanup(i18n.set_language, "it")
        self.session = Session(mock="wired")
        self.addCleanup(self.session.close)
        self.model = EditorModel()
        self.model.load_state(self.session.read_state())

    def test_edits_and_summary(self):
        m = self.model
        m.set_active_stage(5)
        m.set_stage_count(3)
        self.assertEqual((m.current().stage_count, m.current().stage), (3, 3))
        m.set_afk(1)
        changes = m.changes(m.selected)
        self.assertIn("DPI stages in the cycle: 5 → 3", changes)
        self.assertIn("Sleep after: 15 min → 1 min", changes)
        m.set_afk(0)
        self.assertIn("Sleep after: 15 min → Never", m.changes(m.selected))
        with self.assertRaises(ValidationError):
            m.set_stage_count(6)
        self.assertEqual(m.link.bits, 0b111)

    def test_afk_choices(self):
        self.assertEqual(AFK_CHOICES, (0, 1, 2, 5, 10, 15, 30, 60))
        self.assertEqual(afk_choices(15), AFK_CHOICES)
        self.assertEqual(afk_choices(20, 15), (0, 1, 2, 5, 10, 15, 20, 30, 60))
        self.assertEqual(afk_choices(5, 120), (0, 1, 2, 5, 10, 15, 30, 60, 120))


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.tmp.name, "HOME": self.tmp.name})
        self.env.start()
        i18n.set_language("en")

    def tearDown(self):
        i18n.set_language("it")
        self.env.stop()
        self.tmp.cleanup()

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--mock", *argv])
        return code, out.getvalue(), err.getvalue()

    def test_set_afk_and_stages(self):
        code, out, err = self.run_cli("set", "--profile", "1", "--afk", "30", "--stages", "3")
        self.assertEqual(code, 0, err)
        self.assertIn("Sleep:        after 30 min without use (on battery)", out)
        self.assertIn("3 of 5 in the purple-button cycle (off: 3200 6400)", out)
        self.assertIn("Write verified", out)
        code, out, err = self.run_cli("set", "--profile", "1", "--afk", "0")
        self.assertEqual(code, 0, err)
        self.assertIn("never (on battery)", out)

    def test_rejects_bad_values(self):
        for argv in (("--stages", "6"), ("--stages", "0"), ("--afk", "256"), ("--afk", "-1"),
                     ("--stages", "2", "--stage", "4")):
            with self.subTest(argv=argv):
                code, out, err = self.run_cli("set", "--profile", "1", *argv)
                self.assertNotEqual(code, 0)
                self.assertNotIn("Write verified", out)

    def test_show_and_debug_dump(self):
        code, out, err = self.run_cli("show", "--no-buttons")
        self.assertEqual(code, 0, err)
        self.assertIn("Sleep:        after 15 min without use (on battery)", out)
        self.assertIn("Link status: cable power yes · USB data yes · internal link up  (bc bits 111)", out)
        code, out, err = self.run_cli("debug-dump", "--no-buttons")
        self.assertEqual(code, 0, err)
        self.assertIn("## bc link-status", out)
        self.assertIn("OUT 4e 02 81 00 bc", out)
        self.assertIn("IN  4e 02 81 00 07", out)
        self.assertIn("# cable power yes", out)

    def test_dry_run_writes_nothing(self):
        code, out, err = self.run_cli("--dry-run", "set", "--profile", "2", "--afk", "1", "--stages", "2")
        self.assertEqual(code, 0, err)
        self.assertIn("planned, not written", out)


def _gtk_available() -> bool:
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gtk  # noqa: F401
    except (ImportError, ValueError):
        return False
    return True


@unittest.skipUnless(_gtk_available(), "GTK4/libadwaita or a display not available")
class GuiRowsTest(unittest.TestCase):
    """The new controls on the real window (emulated mouse, nothing written)."""

    def _run(self, body, transport="wired"):
        from gi.repository import GLib

        from nzxt_lift_elite.gui import app as gui

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        out: dict = {}
        flag = "--mock" if transport == "wired" else "--mock-wireless"
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp.name}):
            application = gui.LiftEliteApp(gui.parse_args([flag, "--lang", "en"]))

            def check():
                win = application.window
                if win is None or not win.model.loaded:
                    if time.monotonic() - out.setdefault("_t0", time.monotonic()) > 10:
                        out["timed_out"] = True
                        application.quit()
                        return False
                    return True
                try:
                    body(win, out)
                except Exception as exc:  # noqa: BLE001 - re-raised below
                    out["error"] = exc
                win._force_close = True
                application.quit()
                return False

            GLib.timeout_add(100, check)
            application.run(["lift-elite-control"])
        i18n.set_language("it")
        if "error" in out:
            raise out["error"]
        self.assertNotIn("timed_out", out, "window never loaded")
        return out

    def test_sleep_row_and_stage_count(self):
        from gi.repository import Adw

        def body(win, out):
            emu = win.session._fake.emulator
            perf = win.afk_row.get_ancestor(Adw.PreferencesGroup)
            out["perf_title"] = perf.get_title()
            out["same_group"] = win.polling_row.get_ancestor(Adw.PreferencesGroup) is perf
            out["afk_title"] = (win.afk_row.get_title(), win.afk_row.get_subtitle())
            out["afk_items"] = [win.afk_row.get_model().get_string(i) for i in range(win.afk_row.get_model().get_n_items())]
            out["afk_selected"] = win.afk_row.get_selected()
            dpi = win.stage_rows[0].get_ancestor(Adw.PreferencesGroup)
            out["suffix"] = win.stage_count_dd.get_ancestor(Adw.PreferencesGroup) is dpi
            out["count_sel"] = win.stage_count_dd.get_selected()
            out["rows_before"] = len([r for r in win.stage_rows if r.get_visible()])
            win.afk_row.set_selected(AFK_CHOICES.index(1))
            win.model.set_active_stage(5); win.populate()
            win.stage_count_dd.set_selected(2)  # 3 stages
            p = win.model.current()
            out["after"] = (p.afk_minutes, p.stage_count, p.stage)
            out["off"] = [r.has_css_class("stage-off") for r in win.stage_rows]
            out["checks"] = [c.get_sensitive() for c in win.stage_checks]
            out["visible"] = [r.get_visible() for r in win.stage_rows]
            out["sub5"] = win.stage_subtitles[4].get_label()
            out["summary"] = win.model.changes(win.model.selected)
            out["writes"] = list(emu.writes)
            # a custom value from the mouse shows up as an extra entry
            base = win.model.original().with_changes(afk_minutes=20)
            win.model.device[base.index] = base; win.model.drafts[base.index] = base
            win.populate()
            n = win.afk_row.get_model().get_n_items()
            out["custom_items"] = [win.afk_row.get_model().get_string(i) for i in range(n)]
            out["custom_sel"] = win.afk_row.get_model().get_string(win.afk_row.get_selected())

        out = self._run(body)
        self.assertEqual(out["perf_title"], "Performance")
        self.assertTrue(out["same_group"])
        self.assertEqual(out["afk_title"], ("Sleep after", "Only on battery"))
        self.assertEqual(out["afk_items"], ["Never", "1 min", "2 min", "5 min", "10 min", "15 min", "30 min", "60 min"])
        self.assertEqual(out["afk_selected"], 5)  # factory 15 min
        self.assertTrue(out["suffix"])
        self.assertEqual(out["count_sel"], 4)  # 5 stages
        self.assertEqual(out["rows_before"], 5)
        self.assertEqual(out["after"], (1, 3, 3))  # active stage clamped 5 -> 3
        self.assertEqual(out["off"], [False, False, False, True, True])
        self.assertEqual(out["checks"], [True, True, True, False, False])
        self.assertEqual(out["visible"], [True] * 5)  # dimmed, not hidden
        self.assertEqual(out["sub5"], "off")
        self.assertIn("DPI stages in the cycle: 5 → 3", out["summary"])
        self.assertEqual(out["writes"], [])  # nothing written without Apply
        self.assertIn("20 min", out["custom_items"])
        self.assertEqual(out["custom_sel"], "20 min")

    def test_wireless_8000_and_connection(self):
        def body(win, out):
            from nzxt_lift_elite.gui.model import POLLING_CHOICES

            out["choices"] = POLLING_CHOICES
            win.polling_row.set_selected(POLLING_CHOICES.index(8000))
            out["hz"] = win.model.current().polling_hz
            out["sub8000"] = win.polling_row.get_subtitle()
            win.polling_row.set_selected(POLLING_CHOICES.index(1000))
            out["sub1000"] = win.polling_row.get_subtitle()
            out["conn"] = win.row_conn.get_subtitle()
            out["tip"] = win.row_conn.get_tooltip_text()

        out = self._run(body, transport="wireless")
        self.assertIn(8000, out["choices"])
        self.assertEqual(out["hz"], 8000)
        self.assertIn("4000 Hz", out["sub8000"])
        self.assertIn("high-performance", out["sub8000"])
        self.assertIn("power-saving", out["sub1000"])
        self.assertTrue(out["conn"].startswith("Wireless"))
        self.assertIn("USB cable power: no", out["tip"])


if __name__ == "__main__":
    unittest.main()
