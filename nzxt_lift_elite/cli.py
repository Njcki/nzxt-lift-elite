"""Command-line interface (``liftctl``) for the NZXT Lift Elite library.

A thin wrapper around :class:`nzxt_lift_elite.device.LiftElite`; the GUI
and the CoolerControl plugin import the library, not this module.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .actions import CATEGORIES, action_by_code, search_actions
from .device import TRANSPORTS, LiftElite, list_devices
from .errors import LiftEliteError, ProtocolError, ValidationError
from .profile import Profile, dumps_profiles, load_profiles_json, select_config
from .protocol import (
    BUTTON_NAMES,
    FACTORY_LED_APPROX_HEX,
    FACTORY_LED_COLORS,
    FACTORY_LED_NAMES,
    POLLING_HZ,
    BatteryStatus,
    LinkStatus,
    format_action,
    parse_color,
    set_active_profile_packet,
    validate_dpi_value,
    validate_stage,
)
from .i18n import _
from .store import ProfileStore

BUTTON_LABELS = {
    "left": "Left",
    "middle": "Middle (wheel)",
    "right": "Right",
    "forward": "Side forward",
    "back": "Side back",
    "dpi": "DPI button",
}
TRANSPORT_LABELS = {"wired": "wired", "wireless": "wireless (receiver)"}


def button_label(name: str) -> str:
    return _(BUTTON_LABELS[name])


def transport_label(transport: str) -> str:
    return _(TRANSPORT_LABELS[transport])


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="liftctl",
        description=_(
            "Configure an NZXT Lift Elite mouse from Linux, wired or through the receiver "
            "(unofficial protocol, worked out from NZXT CAM). Profiles are numbered 1..5 "
            "(slots 0..4 in the mouse's memory). DPI stages 1..5 are the values "
            "the purple button cycles through."
        ),
        epilog=_(
            "Examples:\n"
            "  liftctl show                       (read-only)\n"
            "  liftctl firmware-info              (read-only)\n"
            "  liftctl battery --watch 300        (read-only: battery level every 5 minutes)\n"
            "  liftctl debug-dump                 (read-only: hex of the replies)\n"
            "  liftctl set --profile 1 --polling 1000 --liftoff 2\n"
            "  liftctl set --profile 1 --dpi 400,800,1600,3200,6400 --stage 2\n"
            "  liftctl set --profile 1 --dpi-stage 2=1000 --motion-sync off\n"
            "  liftctl set --profile 1 --afk 30 --stages 3   (sleep after 30 min, 3 DPI stages)\n"
            "  liftctl remap --profile 1 --button forward --action volume-up\n"
            "  liftctl remap --profile 1 --button dpi --action key:0x04\n"
            "  liftctl remap --profile 1 --reset\n"
            "  liftctl activate --profile 2         (makes the profile active, command 0x45)\n"
            "  liftctl actions volume\n"
            "  liftctl save FPS --profile 1       (named profile)\n"
            "  liftctl load FPS --profile 3\n"
            "  liftctl set --profile 2 --color '#ff0000'\n"
            "  liftctl restore-led-colors         (factory LED colours)\n"
            "  liftctl reset-defaults --yes       (factory buttons and LED colours, all profiles)\n"
            "  liftctl save --all all.json        (file)\n"
            "  liftctl --dry-run load --all all.json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"liftctl {__version__}")
    parser.add_argument("--debug", action="store_true", help=_("hex dump of every packet on stderr"))
    parser.add_argument(
        "--dry-run", action="store_true", help=_("reads the mouse but does not write: shows the packets it would send")
    )
    parser.add_argument("--device", metavar="PATH", help=_("hidraw node to use (default: automatic detection)"))
    parser.add_argument(
        "--transport",
        choices=TRANSPORTS,
        default="auto",
        help=_("auto (cable if present, otherwise receiver), wired or wireless"),
    )
    parser.add_argument("--mock", action="store_true", help=argparse.SUPPRESS)  # emulated mouse (tests/demo)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("devices", help=_("list the mice/receivers found (sysfs only, opens nothing)"))
    p.set_defaults(handler=_cmd_devices)

    p = sub.add_parser("show", aliases=["status"], help=_("read and show the profiles (read-only)"))
    p.add_argument("--profile", type=int, metavar="N", help=_("only profile N (1..5)"))
    p.add_argument("--json", action="store_true", help=_("print as JSON"))
    p.add_argument("--no-buttons", action="store_true", help=_("do not read the button mapping"))
    p.set_defaults(handler=_cmd_show)

    p = sub.add_parser("firmware-info", help=_("firmware name, mode and versions (read-only, command 0b)"))
    p.add_argument("--json", action="store_true")
    p.set_defaults(handler=_cmd_firmware_info)

    p = sub.add_parser("battery", help=_("battery level, voltage and charging state (read-only, command b0)"))
    p.add_argument("--json", action="store_true", help=_("print as JSON"))
    p.add_argument("--watch", type=float, metavar="SECONDS",
                   help=_("read again every SECONDS and print one timestamped line per reading (Ctrl+C to stop)"))
    p.add_argument("--count", type=int, metavar="N", help=_("with --watch: stop after N readings"))
    p.set_defaults(handler=_cmd_battery)

    p = sub.add_parser(
        "debug-dump",
        help=_("read-only: send the known read commands and print the replies in hex"),
    )
    p.add_argument("--no-buttons", action="store_true", help=_("do not read the button mapping"))
    p.add_argument(
        "--out",
        metavar="FILE",
        help=_("also write to FILE (handy to compare two dumps before/after a profile change)"),
    )
    p.set_defaults(handler=_cmd_debug_dump)

    p = sub.add_parser("set", help=_("change only the given fields of a profile (read, change, write, verify)"))
    p.add_argument("--profile", type=int, required=True, metavar="N", help=_("profile 1..5"))
    p.add_argument("--polling", type=int, choices=sorted(POLLING_HZ.values()), help=_("polling rate in Hz"))
    p.add_argument("--liftoff", type=int, choices=(1, 2), help=_("lift-off distance in mm"))
    p.add_argument("--dpi", metavar=_("LIST"), help=_("all 5 DPI stages, e.g. 400,800,1600,3200,6400"))
    p.add_argument(
        "--dpi-stage", action="append", default=[], metavar=_("K=VALUE"), help=_("change only stage K (repeatable)")
    )
    p.add_argument("--stage", type=int, metavar="K", help=_("active DPI stage stored in the profile (1..5)"))
    p.add_argument("--motion-sync", choices=("on", "off"))
    p.add_argument("--color", metavar="#RRGGBB", help=_("profile LED colour (RGB565, approximated)"))
    p.add_argument("--stages", type=int, metavar="N",
                   help=_("how many DPI stages the purple button cycles through (1..5)"))
    p.add_argument("--afk", type=int, metavar="MIN",
                   help=_("sleep after MIN minutes without use, on battery only (0 = never, 0..255; factory 15)"))
    p.set_defaults(handler=_cmd_set)

    p = sub.add_parser("remap", help=_("assign an action to a button of a profile"))
    p.add_argument("--profile", type=int, required=True, metavar="N", help=_("profile 1..5"))
    p.add_argument("--button", choices=BUTTON_NAMES, help=_("button"))
    p.add_argument("--action", help=_("action, e.g. volume-up, left-click, a, f5, key:0x04 (see `liftctl actions`)"))
    p.add_argument("--reset", action="store_true", help=_("restore the profile's 6 factory buttons"))
    p.add_argument(
        "--allow-unknown-code", action="store_true", help=_("allow key:0xNN outside the known table")
    )
    p.set_defaults(handler=_cmd_remap)

    p = sub.add_parser(
        "activate",
        help=_("make a profile active on the mouse (command 0x45, like a long press of the purple button)"),
    )
    p.add_argument("--profile", type=int, required=True, metavar="N", help=_("profile 1..5"))
    p.set_defaults(handler=_cmd_activate)

    p = sub.add_parser("actions", help=_("list the actions that can be assigned to buttons"))
    p.add_argument("query", nargs="?", default="", help=_("filter (name, label or code)"))
    p.add_argument("--category", choices=CATEGORIES)
    p.set_defaults(handler=_cmd_actions)

    p = sub.add_parser(
        "save",
        help=_("save profiles: NAME = named profile in ~/.config/nzxt-lift-elite/profiles, FILE.json = file"),
    )
    p.add_argument("target", metavar=_("NAME|FILE.json"))
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--profile", type=int, metavar="N")
    g.add_argument("--all", action="store_true", help=_("all 5 profiles (file only)"))
    p.add_argument("--force", action="store_true", help=_("overwrite an existing named profile"))
    p.set_defaults(handler=_cmd_save)

    p = sub.add_parser("load", help=_("load a named profile or a JSON file into the mouse (verified after writing)"))
    p.add_argument("target", metavar=_("NAME|FILE.json"))
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--profile", type=int, metavar="N", help=_("target profile 1..5"))
    g.add_argument("--all", action="store_true", help=_("all 5 profiles of the file"))
    p.set_defaults(handler=_cmd_load)

    p = sub.add_parser(
        "restore-led-colors",
        help=_("restore the factory LED colours (only that field, then read back)"),
    )
    p.add_argument("--profile", type=int, metavar="N", help=_("only profile N (1..5); default: all 5"))
    p.set_defaults(handler=_cmd_restore_led_colors)

    p = sub.add_parser(
        "reset-defaults",
        help=_("restore all 5 profiles to the known factory defaults (buttons and LED colours), with a backup first"),
    )
    p.add_argument("--yes", "-y", action="store_true", help=_("do not ask for confirmation"))
    p.set_defaults(handler=_cmd_reset_defaults)

    p = sub.add_parser("profiles", help=_("list the saved named profiles"))
    p.set_defaults(handler=_cmd_profiles)

    p = sub.add_parser("delete", help=_("delete a named profile"))
    p.add_argument("name")
    p.set_defaults(handler=_cmd_delete)
    return parser


# --- helpers -----------------------------------------------------------------


def _open(args: argparse.Namespace) -> LiftElite:
    if getattr(args, "mock", False):
        from .mock import open_mock

        dev, fake = open_mock(
            "wireless" if args.transport == "wireless" else "wired", debug=args.debug, dry_run=args.dry_run
        )
        dev._mock_keepalive = fake  # type: ignore[attr-defined]
        return dev
    return LiftElite.open(args.device, transport=args.transport, debug=args.debug, dry_run=args.dry_run)


def _is_file_target(target: str) -> bool:
    return target.endswith(".json") or "/" in target or target.startswith(".")


def parse_cli_profile(number: int) -> int:
    """User-facing profile 1..5 → internal slot 0..4."""
    if isinstance(number, bool) or not isinstance(number, int) or not (1 <= number <= 5):
        raise ValidationError(_("the profile must be 1..5, got {number}", number=number))
    return number - 1


def display_profile(index: int) -> int:
    """Internal slot 0..4 → user-facing profile 1..5."""
    return index + 1


def _print_profile(profile: Profile, heading: str | None = None, transport: str | None = None) -> None:
    print(heading or _("Profile {n}  (LED {color})", n=display_profile(profile.index), color=profile.color_rgb_hex))
    print(f"  Polling:      {profile.polling_hz} Hz")
    warn = profile.polling_warning(transport)
    if warn:
        print(f"                ({warn})")
    print(f"  Lift-off:     {profile.liftoff} mm")
    stages = []
    for i, value in enumerate(profile.dpi_stages, start=1):
        stages.append(f"[{value}]" if i == profile.stage else str(value))
    print(_("  DPI stages:   {stages}   (active: {stage})", stages=" ".join(stages), stage=profile.stage))
    if profile.stage_count < len(profile.dpi):
        off = " ".join(str(v) for v in profile.dpi[profile.stage_count:])
        print(_("                {n} of 5 in the purple-button cycle (off: {off})", n=profile.stage_count, off=off))
    print(f"  Motion sync:  {'on' if profile.motion_sync else 'off'}")
    print(_("  Sleep:        {value}", value=afk_label(profile.afk_minutes)))
    if profile.buttons is not None:
        print(_("  Buttons:"))
        for name in BUTTON_NAMES:
            code = profile.buttons.get(name)
            if code is None:
                continue
            act = action_by_code(code)
            label = _(act.label) if act else _("unknown code")
            print(f"    {button_label(name):<20} {format_action(code):<18} ({label}, 0x{code:02x})")


def afk_label(minutes: int) -> str:
    if minutes == 0:
        return _("never (on battery)")
    return _("after {n} min without use (on battery)", n=minutes)


def link_line(link: LinkStatus | None) -> str:
    """One line for the bc status bits."""
    if link is None:
        return _("not available")
    yes, no = _("yes"), _("no")
    return _(
        "cable power {power} · USB data {data} · internal link {bridge}  (bc bits {bits:03b})",
        power=yes if link.cable_power else no,
        data=yes if link.usb_data else no,
        bridge=_("up") if link.bridge_link else _("down"),
        bits=link.bits,
    )


def _snapshot_backup(dev: LiftElite, store: ProfileStore, indexes: list[int]) -> Path | None:
    if dev.dry_run:
        return None
    try:
        current = [dev.read_profile(i) for i in indexes]
        return store.backup(current)
    except (LiftEliteError, OSError) as exc:
        print(_("warning: backup failed ({error})", error=exc), file=sys.stderr)
        return None


def _remember(store: ProfileStore, profile: Profile) -> None:
    try:
        store.remember_slot(profile)
    except OSError as exc:
        print(_("warning: could not save the slot state ({error})", error=exc), file=sys.stderr)


# --- commands ------------------------------------------------------------------


def _cmd_devices(args: argparse.Namespace) -> int:
    found = list_devices()
    if not found:
        print(_("No NZXT Lift Elite found (neither wired 1e71:2129 nor receiver 1e71:2101)."))
        return 1
    for info in found:
        print(
            f"{info.path}  {info.usb_id}  {transport_label(info.transport)}  " + _("interface {n}", n=info.interface) + 
            f"{'  report-id 4e' if info.uses_4e_report_id else ''}"
        )
    return 0


CHARGE_LABELS = {
    "charging": "charging",
    "full": "full (cable connected)",
    "discharging": "on battery",
    "unknown": "unknown",
}


def charge_label(battery: BatteryStatus) -> str:
    return _(CHARGE_LABELS.get(battery.charge_state_name, "unknown"))


def _print_battery(b: BatteryStatus) -> None:
    if not b.available:
        print(_("Battery:      unknown (the mouse has no valid fuel-gauge reading yet)"))
    else:
        est = "  " + _("(estimated from the voltage)") if b.estimated else ""
        print(_("Battery:      {pct} %", pct=b.percent) + est)
        print(_("Voltage:      {v:.3f} V ({mv} mV)", v=b.millivolts / 1000, mv=b.millivolts))
        print(_("Charging:     {state}", state=charge_label(b)))
        if b.current_ma is not None:
            print(_("Current:      {ma:+d} mA (approximate)", ma=b.current_ma))
        if b.temperature_c is not None:
            print(_("Temperature:  {t} °C", t=b.temperature_c))
        if b.health_percent is not None:
            print(_("Health:       {pct} %  ({cycles} cycles)", pct=b.health_percent, cycles=b.cycles or 0))
    print(_("Raw b0:       {hex}", hex=b.raw.hex(" ")))


def _battery_line(b: BatteryStatus | None, error: str | None = None) -> str:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if b is None:
        return f"{stamp}  " + _("no reply ({error})", error=error)
    if not b.available:
        return f"{stamp}  " + _("unknown") + f"  raw {b.raw.hex()}"
    cur = f"  {b.current_ma:+d} mA" if b.current_ma is not None else ""
    temp = f"  {b.temperature_c} °C" if b.temperature_c is not None else ""
    return f"{stamp}  {b.percent:3d} %  {b.millivolts} mV  {charge_label(b)}{cur}{temp}  raw {b.raw.hex()}"


def _cmd_battery(args: argparse.Namespace) -> int:
    if args.watch is not None and args.watch <= 0:
        raise ValidationError(_("--watch needs a positive number of seconds"))
    with _open(args) as dev:
        if args.watch is None:
            b = dev.read_battery()
            if args.json:
                print(json.dumps(b.to_dict(), indent=2))
            else:
                _print_battery(b)
            return 0
        n = 0
        try:
            while True:
                b, err = None, None
                try:
                    b = dev.read_battery()
                except ProtocolError as exc:  # asleep on the receiver: keep watching
                    err = str(exc).splitlines()[0]
                if args.json:
                    row = {"time": datetime.now().isoformat(timespec="seconds")}
                    row.update(b.to_dict() if b else {"available": False, "error": err})
                    print(json.dumps(row), flush=True)
                else:
                    print(_battery_line(b, err), flush=True)
                n += 1
                if args.count is not None and n >= args.count:
                    break
                time.sleep(args.watch)
        except KeyboardInterrupt:
            pass
    return 0


def _cmd_firmware_info(args: argparse.Namespace) -> int:
    with _open(args) as dev:
        fw = dev.firmware_info()
        rx = dev.receiver_info()
    if args.json:
        print(json.dumps({"connection": dev.transport, "mouse": fw.to_dict(), "receiver": rx.to_dict() if rx else None}, indent=2))
        return 0
    print(_("Connection:      {transport} ({path})", transport=transport_label(dev.transport), path=dev.info.path))
    _print_fw("Mouse", fw)
    if rx:
        _print_fw(_("Receiver"), rx)
    return 0


def _print_fw(title: str, fw) -> None:
    print(f"{title}:")
    print(_("  Name:          {v}", v=fw.name))
    print(_("  Mode:          {v}", v=fw.mode_name))
    print(_("  Application:   {v}", v=fw.fmt(fw.app_version)))
    print(f"  Bridge:        {fw.fmt(fw.bridge_version)}")
    print(f"  Updater:       {fw.fmt(fw.updater_version)}")


def _cmd_show(args: argparse.Namespace) -> int:
    with _open(args) as dev:
        if args.profile is not None:
            slot = parse_cli_profile(args.profile)
            profiles = [dev.read_profile(slot, with_buttons=not args.no_buttons)]
        else:
            profiles = dev.read_all_profiles(with_buttons=not args.no_buttons)
        fw = None
        try:
            fw = dev.firmware_info()
        except LiftEliteError:
            pass
        active = None
        try:
            active = dev.read_active_profile()
        except LiftEliteError:
            pass
        link = None
        try:
            link = dev.read_link_status(timeout=min(dev.timeout, 0.5), retries=1)
        except LiftEliteError:
            pass
    if args.json:
        sys.stdout.write(dumps_profiles(profiles))
        return 0
    head = f"NZXT Lift Elite {transport_label(dev.transport)} ({dev.info.path})"
    if fw:
        head += f", firmware {fw.fmt(fw.app_version)}"
    print(head)
    if active is not None:
        print(_("Active profile on the mouse: {n}  (command 0x44, internal slot {slot})", n=display_profile(active), slot=active))
    print(_("Link status: {line}", line=link_line(link)))
    print()
    for i, profile in enumerate(profiles):
        if i:
            print()
        heading = None
        if active is not None and profile.index == active:
            heading = _("Profile {n}  (LED {color})  ← active on the mouse", n=display_profile(profile.index), color=profile.color_rgb_hex)
        _print_profile(profile, heading=heading, transport=dev.transport)
    return 0


def _fmt_dump_packet(data: bytes) -> str:
    """One-line hex, trailing zeros omitted but length kept."""
    end = len(data)
    while end > 8 and data[end - 1] == 0:
        end -= 1
    body = " ".join(f"{b:02x}" for b in data[:end])
    omitted = len(data) - end
    if omitted:
        return f"{body}  (+{omitted} zero)"
    return body


def _cmd_debug_dump(args: argparse.Namespace) -> int:
    """Read-only differential dump. Send no writes, never f1/f4/f5/f6/a1-a5."""
    from .protocol import (
        CMD_SET_ACTIVE_PROFILE,
        DANGEROUS_COMMANDS,
        READ_ONLY_COMMANDS,
        command_of,
        parse_active_profile_reply,
        parse_link_status_reply,
    )

    lines: list[str] = []

    def emit(s: str = "") -> None:
        lines.append(s)
        print(s)

    with _open(args) as dev:
        emit(_("# liftctl debug-dump — read-only, no write commands"))
        emit(_("# device: {path} ({transport})", path=dev.info.path, transport=transport_label(dev.transport)))
        emit(_("# usage: run it, long-press the purple button to change profile,"))
        emit(_("#        run it again and compare the two outputs (especially line 44)."))
        emit()
        rows = dev.debug_dump(with_buttons=not args.no_buttons)

    for label, request, reply, error in rows:
        cmd = command_of(request)
        if cmd is None or cmd not in READ_ONLY_COMMANDS or cmd in DANGEROUS_COMMANDS or cmd == CMD_SET_ACTIVE_PROFILE:
            raise RuntimeError(_("debug-dump tried an unsafe command: {hex}", hex=request[:8].hex()))
        emit(f"## {label}")
        emit(f"OUT {_fmt_dump_packet(request)}")
        if error:
            emit(f"ERR {error}")
        else:
            emit(f"IN  {_fmt_dump_packet(reply)}")
            if label.startswith("44 ") and reply is not None:
                try:
                    slot = parse_active_profile_reply(reply)
                    emit(_("# experimental reading: internal slot {slot} = user profile {n}", slot=slot, n=slot + 1))
                except Exception:
                    pass
            if label.startswith("bc ") and reply is not None:
                try:
                    emit("# " + link_line(parse_link_status_reply(reply)))
                except Exception:
                    pass
        emit()

    sent = [command_of(req) for _, req, _, _ in rows]
    if any(c is None or c not in READ_ONLY_COMMANDS for c in sent):
        raise RuntimeError(_("debug-dump: command is not read-only"))
    emit(_("# commands sent: {n} (all read-only)", n=len(rows)))

    if args.out:
        Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(_("# also written to {path}", path=args.out))
    return 0


def _parse_dpi_list(text: str) -> list[int]:
    try:
        values = [int(part.strip()) for part in text.split(",") if part.strip()]
    except ValueError as exc:
        raise ValidationError(_("--dpi: invalid list {value}", value=repr(text))) from exc
    return values


def _parse_stage_overrides(items: list[str]) -> dict[int, int]:
    out: dict[int, int] = {}
    for item in items:
        if "=" not in item:
            raise ValidationError(_("--dpi-stage wants K=VALUE, got {value}", value=repr(item)))
        k, v = item.split("=", 1)
        try:
            stage, value = int(k), int(v)
        except ValueError as exc:
            raise ValidationError(_("invalid --dpi-stage: {value}", value=repr(item))) from exc
        validate_stage(stage)
        out[stage] = validate_dpi_value(value)
    return out


def _cmd_set(args: argparse.Namespace) -> int:
    slot = parse_cli_profile(args.profile)
    dpi = _parse_dpi_list(args.dpi) if args.dpi else None
    overrides = _parse_stage_overrides(args.dpi_stage)
    color = parse_color(args.color) if args.color else None
    motion = None if args.motion_sync is None else args.motion_sync == "on"
    if all(v is None for v in (args.polling, args.liftoff, dpi, args.stage, motion, color, args.stages, args.afk)) \
            and not overrides:
        raise ValidationError(_("nothing to change: give at least one option (--polling, --dpi, …)"))
    store = ProfileStore()
    with _open(args) as dev:
        current = dev.read_profile(slot)
        target = current.with_changes(
            polling_hz=args.polling,
            liftoff=args.liftoff,
            dpi=dpi,
            dpi_stages=overrides or None,
            stage=args.stage,
            motion_sync=motion,
            color=color,
            stage_count=args.stages,
            afk_minutes=args.afk,
        )
        if target.known_body() == current.known_body():
            print(_("The profile already has these values: nothing written."))
            _print_profile(current, transport=dev.transport)
            return 0
        if args.dry_run:
            _print_profile(target, heading=_("Profile {n} (planned, not written)", n=display_profile(slot)), transport=dev.transport)
            dev.apply_profile(target, current=current)
            return 0
        backup = _snapshot_backup(dev, store, [slot])
        written = dev.apply_profile(target, current=current)
    _remember(store, written)
    warn = written.polling_warning(dev.transport)
    _print_profile(written, transport=dev.transport)
    print("\n" + _("Write verified (read back from the mouse).") + (f" Backup: {backup}" if backup else ""))
    if warn and args.polling:
        print(_("Note: {text}", text=warn), file=sys.stderr)
    return 0


def _cmd_restore_led_colors(args: argparse.Namespace) -> int:
    indexes = [parse_cli_profile(args.profile)] if args.profile is not None else list(range(5))
    store = ProfileStore()
    with _open(args) as dev:
        current = {p.index: p for p in (dev.read_profile(i, with_buttons=False) for i in indexes)}
        if args.dry_run:
            for i in indexes:
                want = FACTORY_LED_COLORS[i]
                cur = current[i]
                state = _("unchanged") if cur.color == want else f"{cur.color_rgb_hex} → {FACTORY_LED_APPROX_HEX[i]}"
                print(_("Profile {n}  {name} {hex}  ({raw})  [{state}]", n=display_profile(i), name=_(FACTORY_LED_NAMES[i]), hex=FACTORY_LED_APPROX_HEX[i], raw=want.hex(), state=state))
            dev.restore_factory_led_colors(indexes)
            return 0
        backup = _snapshot_backup(dev, store, indexes)
        written = dev.restore_factory_led_colors(indexes)
    for p in written:
        _remember(store, p)
        want = FACTORY_LED_COLORS[p.index]
        already = current[p.index].color == want
        verb = _("already factory") if already else _("restored")
        print(
            _("Profile {n}  {name} {hex}  ({raw})  {verb}", n=display_profile(p.index), name=_(FACTORY_LED_NAMES[p.index]),
              hex=p.color_rgb_hex, raw=p.color.hex(), verb=verb)
        )
        if p.color != want:
            raise ValidationError(
                _("profile {n}: read {got}, expected {want}", n=display_profile(p.index), got=p.color.hex(), want=want.hex())
            )
    print("\n" + _("LED colours read back from the mouse.") + (f" Backup: {backup}" if backup else ""))
    return 0


def reset_summary() -> str:
    """What `reset-defaults` / «Reset everything» restores (firmware default table)."""
    return _("Every profile gets the factory buttons and LED colour (Blue, Red, Green, Yellow, Purple), "
             "all 5 DPI stages 400/800/1600/3200/6400 with stage N active on profile N, 1000 Hz polling, "
             "1 mm lift-off, motion sync on and sleep after 15 min (values from the mouse firmware's "
             "default profile table).")


def _confirm(question: str) -> bool:
    if not sys.stdin.isatty():
        return False
    try:
        answer = input(question + " [" + _("y/N") + "] ")
    except EOFError:
        return False
    return answer.strip().lower() in {"y", "yes", "s", "si", "sì"}


def _cmd_reset_defaults(args: argparse.Namespace) -> int:
    """Factory reset through the normal verified writes plus 0x45 only."""
    print(_("Reset all 5 profiles to the factory defaults."))
    print(reset_summary())
    if not args.yes and not args.dry_run:
        if not sys.stdin.isatty():
            raise ValidationError(_("reset-defaults changes all 5 profiles: add --yes to confirm"))
        if not _confirm(_("Continue?")):
            print(_("Cancelled: nothing was written."))
            return 1
    store = ProfileStore()

    def progress(index: int, profile: Profile) -> None:
        state = _("verified") if not args.dry_run else "dry-run"
        print(_("Profile {n}  LED {name}, stage {stage} active ({dpi} DPI), {hz} Hz, lift-off {mm} mm, "
                "motion sync {ms}, factory buttons  [{state}]", n=display_profile(index),
                name=_(FACTORY_LED_NAMES[index]), stage=profile.stage, dpi=profile.dpi[profile.stage - 1],
                hz=profile.polling_hz, mm=profile.liftoff, ms=_("on") if profile.motion_sync else _("off"), state=state))

    with _open(args) as dev:
        result = dev.reset_defaults(backup=lambda profiles: store.backup(profiles, reason="reset"), progress=progress)
    if not args.dry_run:
        for p in result.results:
            _remember(store, p)
    print(_("Active profile restored: {n}", n=display_profile(result.active)))
    if result.backup:
        print(_("Backup: {path}", path=result.backup))
    return 0


def _cmd_remap(args: argparse.Namespace) -> int:
    slot = parse_cli_profile(args.profile)
    if args.reset == bool(args.button or args.action):
        raise ValidationError(_("use --button and --action together, or only --reset"))
    if not args.reset and not (args.button and args.action):
        raise ValidationError(_("both --button and --action are needed"))
    store = ProfileStore()
    with _open(args) as dev:
        current = dev.read_profile(slot)
        if args.reset:
            target_buttons = {name: code for name, code in zip(BUTTON_NAMES, (0xA5, 0xA6, 0xA7, 0xA8, 0xA9, 0xAA))}
        else:
            target_buttons = {args.button: args.action}
        target = current.with_changes(buttons=target_buttons, allow_unknown_actions=args.allow_unknown_code)
        if target.buttons == current.buttons:
            print(_("The buttons already have this mapping: nothing written."))
            return 0
        if not args.dry_run:
            backup = _snapshot_backup(dev, store, [slot])
        written = dev.apply_profile(target, current=current)
    if args.dry_run:
        print(_("(dry-run: no write packet sent)"))
        return 0
    _remember(store, written)
    _print_profile(written, transport=dev.transport)
    print("\n" + _("Mapping verified (read back from the mouse).") + (f" Backup: {backup}" if backup else ""))
    return 0


def _cmd_activate(args: argparse.Namespace) -> int:
    """Send only ``4e 02 82 00 45 PP`` (between two 0x44 reads)."""
    slot = parse_cli_profile(args.profile)
    with _open(args) as dev:
        before = dev.read_active_profile()
        print(_("Active profile before: {n}  (0x44, internal slot {slot})", n=display_profile(before), slot=before))
        if before == slot:
            print(_("Profile {n} is already active: no command sent.", n=args.profile))
            return 0
        packet = set_active_profile_packet(slot)
        print(_("Sending:   {hex}  (0x45, internal slot {slot})", hex=packet[:6].hex(" "), slot=slot))
        result = dev.activate_profile(slot)
    if dev.dry_run:
        print(_("(dry-run: 0x45 not sent)"))
        return 0
    if result.reply is not None:
        print(_("Reply:     {hex}", hex=result.reply[:5].hex(" ")))
    print(_("Active profile after: {n}  (read back with 0x44) — verified.", n=display_profile(result.after)))
    if result.refreshed is not None:
        print(_("Profile settings re-sent unchanged (0x93) and read back, so the mouse applies them."))
    return 0


def _cmd_actions(args: argparse.Namespace) -> int:
    results = search_actions(args.query, args.category)
    if not results:
        print(_("No action found."))
        return 1
    for act in results:
        aliases = _("  (also: {aliases})", aliases=", ".join(act.aliases)) if act.aliases else ""
        print(f"0x{act.code:02x}  {act.name:<24} {_(act.label):<26} [{act.category}]{aliases}")
    return 0


def _cmd_save(args: argparse.Namespace) -> int:
    store = ProfileStore()
    is_file = _is_file_target(args.target)
    if args.all and not is_file:
        raise ValidationError(_("--all saves to a file: use a name ending in .json"))
    with _open(args) as dev:
        if args.all:
            profiles = dev.read_all_profiles()
        else:
            slot = parse_cli_profile(args.profile)
            profiles = [dev.read_profile(slot)]
    if is_file:
        path = Path(args.target)
        path.write_text(dumps_profiles(profiles), encoding="utf-8")
        print(_("Saved {n} profiles to {path}", n=len(profiles), path=path))
    else:
        path = store.save_named(args.target, profiles[0], overwrite=args.force or not store.path_for(args.target).exists())
        print(_("Profile {n} saved as {name} in {path}", n=display_profile(profiles[0].index), name=repr(args.target), path=path))
    return 0


def _cmd_load(args: argparse.Namespace) -> int:
    store = ProfileStore()
    if _is_file_target(args.target):
        configs = load_profiles_json(args.target)
    else:
        if args.all:
            raise ValidationError(_("--all loads a .json file, not a named profile"))
        configs = [store.load_named(args.target)]
    slot = None if args.profile is None else parse_cli_profile(args.profile)
    selected = select_config(configs, profile_index=slot, require_all=args.all)
    with _open(args) as dev:
        if not args.dry_run:
            backup = _snapshot_backup(dev, store, [i for i, _ in selected])
        results = []
        for index, config in selected:
            results.append(dev.apply_config(index, config))
    if args.dry_run:
        print(_("(dry-run: no write packet sent)"))
        return 0
    for profile in results:
        _remember(store, profile)
        _print_profile(profile, transport=dev.transport)
        print()
    print(_("Load verified ({n} profiles).", n=len(results)) + (f" Backup: {backup}" if backup else ""))
    return 0


def _cmd_profiles(args: argparse.Namespace) -> int:
    store = ProfileStore()
    items = store.list_named()
    if not items:
        print(_("No named profiles in {path}", path=store.profiles_dir))
        return 0
    for item in items:
        c = item.config
        dpi = ",".join(map(str, c.dpi_stages)) if c.dpi_stages else "-"
        print(f"{item.name:<24} polling {c.polling_hz or '-'} Hz  DPI {dpi}  ({item.path.name})")
    return 0


def _cmd_delete(args: argparse.Namespace) -> int:
    ProfileStore().delete_named(args.name)
    print(_("Profile {name} deleted.", name=repr(args.name)))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
        return args.handler(args)
    except BrokenPipeError:
        try:
            sys.stdout.close()
        except Exception:
            pass
        return 0
    except KeyboardInterrupt:
        print(_("Interrupted."), file=sys.stderr)
        return 130
    except (LiftEliteError, OSError) as exc:
        print(_("error: {error}", error=exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
