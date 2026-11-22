"""Command-line interface (``liftctl``) for the NZXT Lift Elite library.

A thin wrapper around :class:`nzxt_lift_elite.device.LiftElite`; the GUI
and the CoolerControl plugin import the library, not this module.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .actions import CATEGORIES, action_by_code, search_actions
from .device import TRANSPORTS, LiftElite, list_devices
from .errors import LiftEliteError, ValidationError
from .profile import Profile, dumps_profiles, load_profiles_json, select_config
from .protocol import (
    BUTTON_NAMES,
    FACTORY_LED_APPROX_HEX,
    FACTORY_LED_COLORS,
    FACTORY_LED_NAMES,
    POLLING_HZ,
    format_action,
    parse_color,
    validate_dpi_value,
    validate_stage,
)
from .store import ProfileStore

BUTTON_LABELS_IT = {
    "left": "Sinistro",
    "middle": "Centrale (rotella)",
    "right": "Destro",
    "forward": "Laterale avanti",
    "back": "Laterale indietro",
    "dpi": "Tasto DPI",
}
TRANSPORT_IT = {"wired": "via cavo", "wireless": "wireless (ricevitore)"}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="liftctl",
        description=(
            "Configura un mouse NZXT Lift Elite da Linux, via cavo o tramite il ricevitore "
            "(protocollo non ufficiale, ricavato da NZXT CAM). I profili si indicano 1..5 "
            "(nella memoria del mouse sono gli slot 0..4). Gli stadi DPI 1..5 sono i valori "
            "che si ciclano col tasto viola."
        ),
        epilog=(
            "Esempi:\n"
            "  liftctl show                       (sola lettura)\n"
            "  liftctl firmware-info              (sola lettura)\n"
            "  liftctl debug-dump                 (sola lettura: hex delle risposte)\n"
            "  liftctl set --profile 1 --polling 1000 --liftoff 2\n"
            "  liftctl set --profile 1 --dpi 400,800,1600,3200,6400 --stage 2\n"
            "  liftctl set --profile 1 --dpi-stage 2=1000 --motion-sync off\n"
            "  liftctl remap --profile 1 --button forward --action volume-up\n"
            "  liftctl remap --profile 1 --button dpi --action key:0x04\n"
            "  liftctl remap --profile 1 --reset\n"
            "  liftctl actions volume\n"
            "  liftctl save FPS --profile 1       (profilo con nome)\n"
            "  liftctl load FPS --profile 3\n"
            "  liftctl set --profile 2 --color '#ff0000'\n"
            "  liftctl restore-led-colors         (colori LED di fabbrica)\n"
            "  liftctl save --all tutti.json      (file)\n"
            "  liftctl --dry-run load --all tutti.json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"liftctl {__version__}")
    parser.add_argument("--debug", action="store_true", help="dump esadecimale di ogni pacchetto su stderr")
    parser.add_argument(
        "--dry-run", action="store_true", help="legge dal mouse ma non scrive: mostra i pacchetti che invierebbe"
    )
    parser.add_argument("--device", metavar="PATH", help="nodo hidraw da usare (default: rilevamento automatico)")
    parser.add_argument(
        "--transport",
        choices=TRANSPORTS,
        default="auto",
        help="auto (cavo se presente, altrimenti ricevitore), wired o wireless",
    )
    parser.add_argument("--mock", action="store_true", help=argparse.SUPPRESS)  # emulated mouse (tests/demo)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("devices", help="elenca mouse/ricevitore trovati (solo sysfs, non apre nulla)")
    p.set_defaults(handler=_cmd_devices)

    p = sub.add_parser("show", aliases=["status"], help="legge e mostra i profili (sola lettura)")
    p.add_argument("--profile", type=int, metavar="N", help="solo il profilo N (1..5)")
    p.add_argument("--json", action="store_true", help="stampa in JSON")
    p.add_argument("--no-buttons", action="store_true", help="non leggere la mappatura dei tasti")
    p.set_defaults(handler=_cmd_show)

    p = sub.add_parser("firmware-info", help="nome, modalità e versioni firmware (sola lettura, comando 0b)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(handler=_cmd_firmware_info)

    p = sub.add_parser(
        "debug-dump",
        help="sola lettura: invia i comandi di lettura già noti e stampa le risposte in esadecimale",
    )
    p.add_argument("--no-buttons", action="store_true", help="non leggere la mappatura dei tasti")
    p.add_argument(
        "--out",
        metavar="FILE",
        help="scrive anche su FILE (utile per confrontare due dump prima/dopo il cambio profilo)",
    )
    p.set_defaults(handler=_cmd_debug_dump)

    p = sub.add_parser("set", help="modifica solo i campi indicati di un profilo (legge, cambia, scrive, verifica)")
    p.add_argument("--profile", type=int, required=True, metavar="N", help="profilo 1..5")
    p.add_argument("--polling", type=int, choices=sorted(POLLING_HZ.values()), help="frequenza di polling in Hz")
    p.add_argument("--liftoff", type=int, choices=(1, 2), help="distanza di lift-off in mm")
    p.add_argument("--dpi", metavar="LISTA", help="tutti e 5 gli stadi DPI, es. 400,800,1600,3200,6400")
    p.add_argument(
        "--dpi-stage", action="append", default=[], metavar="K=VALORE", help="cambia solo lo stadio K (ripetibile)"
    )
    p.add_argument("--stage", type=int, metavar="K", help="stadio DPI attivo salvato nel profilo (1..5)")
    p.add_argument("--motion-sync", choices=("on", "off"))
    p.add_argument("--color", metavar="#RRGGBB", help="colore del LED del profilo (RGB565, approssimato)")
    p.set_defaults(handler=_cmd_set)

    p = sub.add_parser("remap", help="assegna un'azione a un tasto di un profilo")
    p.add_argument("--profile", type=int, required=True, metavar="N", help="profilo 1..5")
    p.add_argument("--button", choices=BUTTON_NAMES, help="tasto")
    p.add_argument("--action", help="azione, es. volume-up, left-click, a, f5, key:0x04 (vedi `liftctl actions`)")
    p.add_argument("--reset", action="store_true", help="ripristina i 6 tasti di fabbrica del profilo")
    p.add_argument(
        "--allow-unknown-code", action="store_true", help="permette key:0xNN fuori dalla tabella nota"
    )
    p.set_defaults(handler=_cmd_remap)

    p = sub.add_parser("actions", help="elenca le azioni assegnabili ai tasti")
    p.add_argument("query", nargs="?", default="", help="filtro (nome, etichetta o codice)")
    p.add_argument("--category", choices=CATEGORIES)
    p.set_defaults(handler=_cmd_actions)

    p = sub.add_parser(
        "save",
        help="salva profili: NOME = profilo con nome in ~/.config/nzxt-lift-elite/profiles, FILE.json = file",
    )
    p.add_argument("target", metavar="NOME|FILE.json")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--profile", type=int, metavar="N")
    g.add_argument("--all", action="store_true", help="tutti e 5 i profili (solo file)")
    p.add_argument("--force", action="store_true", help="sovrascrive un profilo con nome esistente")
    p.set_defaults(handler=_cmd_save)

    p = sub.add_parser("load", help="carica un profilo con nome o un file JSON nel mouse (verifica dopo la scrittura)")
    p.add_argument("target", metavar="NOME|FILE.json")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--profile", type=int, metavar="N", help="profilo di destinazione 1..5")
    g.add_argument("--all", action="store_true", help="tutti e 5 i profili del file")
    p.set_defaults(handler=_cmd_load)

    p = sub.add_parser(
        "restore-led-colors",
        help="ripristina i colori LED di fabbrica (solo quel campo, poi rilegge)",
    )
    p.add_argument("--profile", type=int, metavar="N", help="solo il profilo N (1..5); default: tutti e 5")
    p.set_defaults(handler=_cmd_restore_led_colors)

    p = sub.add_parser("profiles", help="elenca i profili con nome salvati")
    p.set_defaults(handler=_cmd_profiles)

    p = sub.add_parser("delete", help="elimina un profilo con nome")
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
        raise ValidationError(f"il profilo deve essere 1..5, ricevuto {number}")
    return number - 1


def display_profile(index: int) -> int:
    """Internal slot 0..4 → user-facing profile 1..5."""
    return index + 1


def _print_profile(profile: Profile, heading: str | None = None, transport: str | None = None) -> None:
    print(heading or f"Profilo {display_profile(profile.index)}  (LED {profile.color_rgb_hex})")
    print(f"  Polling:      {profile.polling_hz} Hz")
    warn = profile.polling_warning(transport)
    if warn:
        print(f"                ({warn})")
    print(f"  Lift-off:     {profile.liftoff} mm")
    stages = []
    for i, value in enumerate(profile.dpi_stages, start=1):
        stages.append(f"[{value}]" if i == profile.stage else str(value))
    print(f"  Stadi DPI:    {' '.join(stages)}   (attivo: {profile.stage})")
    print(f"  Motion sync:  {'on' if profile.motion_sync else 'off'}")
    if profile.buttons is not None:
        print("  Tasti:")
        for name in BUTTON_NAMES:
            code = profile.buttons.get(name)
            if code is None:
                continue
            act = action_by_code(code)
            label = act.label if act else "codice sconosciuto"
            print(f"    {BUTTON_LABELS_IT[name]:<20} {format_action(code):<18} ({label}, 0x{code:02x})")


def _snapshot_backup(dev: LiftElite, store: ProfileStore, indexes: list[int]) -> Path | None:
    if dev.dry_run:
        return None
    try:
        current = [dev.read_profile(i) for i in indexes]
        return store.backup(current)
    except (LiftEliteError, OSError) as exc:
        print(f"avviso: backup non riuscito ({exc})", file=sys.stderr)
        return None


def _remember(store: ProfileStore, profile: Profile) -> None:
    try:
        store.remember_slot(profile)
    except OSError as exc:
        print(f"avviso: impossibile salvare lo stato dello slot ({exc})", file=sys.stderr)


# --- commands ------------------------------------------------------------------


def _cmd_devices(args: argparse.Namespace) -> int:
    found = list_devices()
    if not found:
        print("Nessun NZXT Lift Elite trovato (né via cavo 1e71:2129 né ricevitore 1e71:2101).")
        return 1
    for info in found:
        print(
            f"{info.path}  {info.usb_id}  {TRANSPORT_IT[info.transport]}  interfaccia {info.interface}"
            f"{'  report-id 4e' if info.uses_4e_report_id else ''}"
        )
    return 0


def _cmd_firmware_info(args: argparse.Namespace) -> int:
    with _open(args) as dev:
        fw = dev.firmware_info()
        rx = dev.receiver_info()
    if args.json:
        print(json.dumps({"connection": dev.transport, "mouse": fw.to_dict(), "receiver": rx.to_dict() if rx else None}, indent=2))
        return 0
    print(f"Collegamento:    {TRANSPORT_IT[dev.transport]} ({dev.info.path})")
    _print_fw("Mouse", fw)
    if rx:
        _print_fw("Ricevitore", rx)
    return 0


def _print_fw(title: str, fw) -> None:
    print(f"{title}:")
    print(f"  Nome:          {fw.name}")
    print(f"  Modalità:      {fw.mode_name}")
    print(f"  Applicazione:  {fw.fmt(fw.app_version)}")
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
    if args.json:
        sys.stdout.write(dumps_profiles(profiles))
        return 0
    head = f"NZXT Lift Elite {TRANSPORT_IT[dev.transport]} ({dev.info.path})"
    if fw:
        head += f", firmware {fw.fmt(fw.app_version)}"
    print(head)
    if active is not None:
        print(
            f"Profilo attivo (sperimentale, comando 0x44): {active + 1} "
            f"(slot interno {active})"
        )
    print()
    for i, profile in enumerate(profiles):
        if i:
            print()
        _print_profile(profile, transport=dev.transport)
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
    )

    lines: list[str] = []

    def emit(s: str = "") -> None:
        lines.append(s)
        print(s)

    with _open(args) as dev:
        emit("# liftctl debug-dump — sola lettura, nessun comando di scrittura")
        emit(f"# dispositivo: {dev.info.path} ({TRANSPORT_IT[dev.transport]})")
        emit("# uso: esegui, tieni premuto a lungo il tasto viola per cambiare profilo,")
        emit("#      esegui di nuovo e confronta i due output (in particolare la riga 44).")
        emit()
        rows = dev.debug_dump(with_buttons=not args.no_buttons)

    for label, request, reply, error in rows:
        cmd = command_of(request)
        if cmd is None or cmd not in READ_ONLY_COMMANDS or cmd in DANGEROUS_COMMANDS or cmd == CMD_SET_ACTIVE_PROFILE:
            raise RuntimeError(f"debug-dump ha tentato un comando non sicuro: {request[:8].hex()}")
        emit(f"## {label}")
        emit(f"OUT {_fmt_dump_packet(request)}")
        if error:
            emit(f"ERR {error}")
        else:
            emit(f"IN  {_fmt_dump_packet(reply)}")
            if label.startswith("44 ") and reply is not None:
                try:
                    slot = parse_active_profile_reply(reply)
                    emit(f"# interpretazione sperimentale: slot interno {slot} = profilo utente {slot + 1}")
                except Exception:
                    pass
        emit()

    sent = [command_of(req) for _, req, _, _ in rows]
    if any(c is None or c not in READ_ONLY_COMMANDS for c in sent):
        raise RuntimeError("debug-dump: comando non di sola lettura")
    emit(f"# comandi inviati: {len(rows)} (tutti di sola lettura)")

    if args.out:
        Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"# scritto anche in {args.out}")
    return 0


def _parse_dpi_list(text: str) -> list[int]:
    try:
        values = [int(part.strip()) for part in text.split(",") if part.strip()]
    except ValueError as exc:
        raise ValidationError(f"--dpi: lista non valida {text!r}") from exc
    return values


def _parse_stage_overrides(items: list[str]) -> dict[int, int]:
    out: dict[int, int] = {}
    for item in items:
        if "=" not in item:
            raise ValidationError(f"--dpi-stage vuole K=VALORE, trovato {item!r}")
        k, v = item.split("=", 1)
        try:
            stage, value = int(k), int(v)
        except ValueError as exc:
            raise ValidationError(f"--dpi-stage non valido: {item!r}") from exc
        validate_stage(stage)
        out[stage] = validate_dpi_value(value)
    return out


def _cmd_set(args: argparse.Namespace) -> int:
    slot = parse_cli_profile(args.profile)
    dpi = _parse_dpi_list(args.dpi) if args.dpi else None
    overrides = _parse_stage_overrides(args.dpi_stage)
    color = parse_color(args.color) if args.color else None
    motion = None if args.motion_sync is None else args.motion_sync == "on"
    if all(v is None for v in (args.polling, args.liftoff, dpi, args.stage, motion, color)) and not overrides:
        raise ValidationError("niente da cambiare: indica almeno un'opzione (--polling, --dpi, …)")
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
        )
        if target.known_body() == current.known_body():
            print("Il profilo ha già questi valori: nessuna scrittura.")
            _print_profile(current, transport=dev.transport)
            return 0
        if args.dry_run:
            _print_profile(target, heading=f"Profilo {display_profile(slot)} (previsto, non scritto)", transport=dev.transport)
            dev.apply_profile(target, current=current)
            return 0
        backup = _snapshot_backup(dev, store, [slot])
        written = dev.apply_profile(target, current=current)
    _remember(store, written)
    warn = written.polling_warning(dev.transport)
    _print_profile(written, transport=dev.transport)
    print("\nScrittura verificata (riletta dal mouse)." + (f" Backup: {backup}" if backup else ""))
    if warn and args.polling:
        print(f"Nota: {warn}", file=sys.stderr)
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
                state = "invariato" if cur.color == want else f"{cur.color_rgb_hex} → {FACTORY_LED_APPROX_HEX[i]}"
                print(f"Profilo {display_profile(i)}  {FACTORY_LED_NAMES[i]} {FACTORY_LED_APPROX_HEX[i]}  ({want.hex()})  [{state}]")
            dev.restore_factory_led_colors(indexes)
            return 0
        backup = _snapshot_backup(dev, store, indexes)
        written = dev.restore_factory_led_colors(indexes)
    for p in written:
        _remember(store, p)
        want = FACTORY_LED_COLORS[p.index]
        already = current[p.index].color == want
        verb = "già di fabbrica" if already else "ripristinato"
        print(
            f"Profilo {display_profile(p.index)}  {FACTORY_LED_NAMES[p.index]} "
            f"{p.color_rgb_hex}  ({p.color.hex()})  {verb}"
        )
        if p.color != want:
            raise ValidationError(
                f"profilo {display_profile(p.index)}: letto {p.color.hex()}, atteso {want.hex()}"
            )
    print("\nColori LED riletti dal mouse." + (f" Backup: {backup}" if backup else ""))
    return 0


def _cmd_remap(args: argparse.Namespace) -> int:
    slot = parse_cli_profile(args.profile)
    if args.reset == bool(args.button or args.action):
        raise ValidationError("usa --button e --action insieme, oppure solo --reset")
    if not args.reset and not (args.button and args.action):
        raise ValidationError("servono sia --button sia --action")
    store = ProfileStore()
    with _open(args) as dev:
        current = dev.read_profile(slot)
        if args.reset:
            target_buttons = {name: code for name, code in zip(BUTTON_NAMES, (0xA5, 0xA6, 0xA7, 0xA8, 0xA9, 0xAA))}
        else:
            target_buttons = {args.button: args.action}
        target = current.with_changes(buttons=target_buttons, allow_unknown_actions=args.allow_unknown_code)
        if target.buttons == current.buttons:
            print("I tasti hanno già questa mappatura: nessuna scrittura.")
            return 0
        if not args.dry_run:
            backup = _snapshot_backup(dev, store, [slot])
        written = dev.apply_profile(target, current=current)
    if args.dry_run:
        print("(dry-run: nessun pacchetto di scrittura inviato)")
        return 0
    _remember(store, written)
    _print_profile(written, transport=dev.transport)
    print("\nMappatura verificata (riletta dal mouse)." + (f" Backup: {backup}" if backup else ""))
    return 0


def _cmd_actions(args: argparse.Namespace) -> int:
    results = search_actions(args.query, args.category)
    if not results:
        print("Nessuna azione trovata.")
        return 1
    for act in results:
        aliases = f"  (anche: {', '.join(act.aliases)})" if act.aliases else ""
        print(f"0x{act.code:02x}  {act.name:<24} {act.label:<26} [{act.category}]{aliases}")
    return 0


def _cmd_save(args: argparse.Namespace) -> int:
    store = ProfileStore()
    is_file = _is_file_target(args.target)
    if args.all and not is_file:
        raise ValidationError("--all salva in un file: usa un nome che finisce con .json")
    with _open(args) as dev:
        if args.all:
            profiles = dev.read_all_profiles()
        else:
            slot = parse_cli_profile(args.profile)
            profiles = [dev.read_profile(slot)]
    if is_file:
        path = Path(args.target)
        path.write_text(dumps_profiles(profiles), encoding="utf-8")
        print(f"Salvati {len(profiles)} profili in {path}")
    else:
        path = store.save_named(args.target, profiles[0], overwrite=args.force or not store.path_for(args.target).exists())
        print(f"Profilo {display_profile(profiles[0].index)} salvato come {args.target!r} in {path}")
    return 0


def _cmd_load(args: argparse.Namespace) -> int:
    store = ProfileStore()
    if _is_file_target(args.target):
        configs = load_profiles_json(args.target)
    else:
        if args.all:
            raise ValidationError("--all carica un file .json, non un profilo con nome")
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
        print("(dry-run: nessun pacchetto di scrittura inviato)")
        return 0
    for profile in results:
        _remember(store, profile)
        _print_profile(profile, transport=dev.transport)
        print()
    print(f"Caricamento verificato ({len(results)} profili)." + (f" Backup: {backup}" if backup else ""))
    return 0


def _cmd_profiles(args: argparse.Namespace) -> int:
    store = ProfileStore()
    items = store.list_named()
    if not items:
        print(f"Nessun profilo con nome in {store.profiles_dir}")
        return 0
    for item in items:
        c = item.config
        dpi = ",".join(map(str, c.dpi_stages)) if c.dpi_stages else "-"
        print(f"{item.name:<24} polling {c.polling_hz or '-'} Hz  DPI {dpi}  ({item.path.name})")
    return 0


def _cmd_delete(args: argparse.Namespace) -> int:
    ProfileStore().delete_named(args.name)
    print(f"Profilo {args.name!r} eliminato.")
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
        print("Interrotto.", file=sys.stderr)
        return 130
    except (LiftEliteError, OSError) as exc:
        print(f"errore: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
