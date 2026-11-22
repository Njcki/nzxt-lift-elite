# NZXT Lift Elite (USB 1e71:2129, receiver 1e71:2101) vendor protocol — captured from NZXT CAM on Windows, wired mode
Vendor HID interface MI_01. Host->device: interrupt OUT EP 0x02, 64 bytes. Device->host: interrupt IN EP 0x82, 64 bytes.
All packets start 4e 02. Device often first answers `4e e5 00...` (busy/ack, ignore) then the real reply.

## Read profile N (N=0..4)
OUT: 4e 02 82 00 92 NN 00...   -> IN: 4e 02 94 00 01 NN <profile body from byte 6>
## Write profile N
OUT: 4e 02 94 00 93 NN <profile body from byte 6>  -> IN: 4e 02 82 00 01 00... (ok)
CAM then sends 4e 02 81 00 b0 (status read) -> IN 4e 02 97 00 01 40 01 69 10 00 00 40 01 40 01 00 00 64 00 64 00 18|19 02 08 00 40 01 ...

## Profile body (byte offsets in the full 64-byte packet)
6  polling: 0=250 1=500 2=1000 3=2000 4=4000 5=8000 Hz (confirmed)
7  lift-off distance (seen 1 and 2; confirmed it is lift-off; exact mm mapping unknown, probably 1=1mm 2=2mm)
8  number of DPI stages (5)
9  current DPI stage, 0-based (0 = first stored DPI value, 4 = fifth). Confirmed 2026-11-17 on the real mouse: cycling the purple button produced 0, 2, 3, 4, then wrapped to 0, and at 0 the pointer was very slow (400 DPI, the first stage). Captured factory bodies with byte 01/02 are therefore user stages 2 and 3, not 1 and 2.
10..19 DPI stages, 5 x uint16 little-endian (0x0190=400, 0x0320=800, 0x0640=1600, 0x0c80=3200, 0x1900=6400). Changing stage 2 800->1000 changed bytes 12-13 to e8 03 (confirmed)
20 0x0f (probably enabled-stage mask)
21..22 differs per profile (1f 00, 00 f8, e0 07, 40 fd, 1f f8) — stored profile-indicator field, little-endian RGB565 (deduced from factory values: 0x001f blue, 0xf800 red, 0x07e0 green, 0xfd40 yellow, 0xf81f purple, matching the manual's profile colours). See notes below.
23 motion sync 1=on 0=off (confirmed)
Captured profile 0: 4e0294000100050105019001200340068 00c00190f1f0001
Captured profiles 1-4 bodies:
 p1: 02 01 05 01 9001 2003 4006 800c 0019 0f 00f8 01
 p2: 03 01 05 01 2003 b004 4006 800c 0019 0f e007 01
 p3: 02 01 05 02 9001 2003 4006 800c 0019 0f 40fd 01
 p4: 02 01 05 01 9001 2003 4006 800c 0019 0f 1ff8 01

## Button remap (applies to the active/current profile as CAM sends it)
OUT: 4e 02 85 00 50 00 BB 04 AA 00...  -> IN 4e 02 82 00 01 (ok)
BB button: 0=left 1=middle 2=right 3=forward(side) 4=back(side) 5=DPI button
AA action: a5=left click a6=middle a7=right a8=forward a9=back aa=DPI cycle; values < 0xa5 = HID keyboard usage (0x04 = 'A')
Factory reset sends: (2,a7)(4,a9)(1,a6)(5,aa)(0,a5)(3,a8)

## Info reads seen at CAM startup
4e 02 81 00 03 -> 4e 02 90 00 ... (16 bytes, device id; firmware copies four words at 0x10000060/64/a4/a8)
4e 02 81 00 b8 -> 4e 02 84 00 a0 91 ea 85 ... (4-byte id; same word as bytes 8..11 of the 0x03 reply, i.e. 0x100000a4)
4e 02 81 00 0b -> 4e 02 9e 00 "HyperSonic" ... (device/firmware name)
4e 02 81 00 44 -> 4e 02 82 00 01 PP   (see Active profile below)
4e 02 81 00 b0 -> status (see above)

## Correction: remap write carries the profile index
OUT: 4e 02 85 00 50 PP BB 04 AA  (PP = profile 0..4; CAM wrote PP=00 because profile 0 was active/selected)

## Button mapping READ (captured at CAM startup, 2nd capture)
OUT: 4e 02 84 00 51 PP BB        -> IN: 4e 02 85 00 01 PP BB 04 AA
CAM reads all 5 profiles x 6 buttons this way. Also seen: OUT 4e 02 82 00 45 PP -> IN 4e 02 81 00 01 (set active profile; see below).

## Firmware update attempt (CAM 4.76.9, wired, mouse app 1.1.1 -> 1.1.5, blob Mouse_App_v1.01.0005)
OUT 4e 02 81 00 0b            -> IN 4e 02 9e 00 "HyperSonic" ...
OUT 4e 02 82 00 f5 01         -> IN 4e 02 82 00 01 01   (likely: select boot mode = updater)
OUT 4e 02 81 00 f1            -> IN 4e 02 81 00 01      (likely: reboot)
~3 s later the device disconnects and ~4 s later re-enumerates as the SAME 1e71:2129 bcdDevice 0x0101 in application mode.
CAM logs "Nonfatal error NordicChip" and gives up. No firmware data is ever transferred, so the actual flashing protocol could not be captured.
DANGEROUS: never send f5/f1 from our tool except behind an explicit high-risk flag.

## Firmware blobs (extracted from CAM 4.76.9.72, cam-core dist/index.node)
Embedded in the binary: name, then header (... version bytes, size u32 LE, checksum u32 LE), then raw image.
Checksum = 32-bit byte sum for Mouse/Dongle images, CRC32 for Bridge. All 5 verified.
| Blob | Size | Check | Reset vector |
|---|---|---|---|
| Mouse_App v1.1.5 | 78384 | sum 8157779 | 0x29371 (app at 0x20000+) |
| Mouse_Updater v1.1.0 | 72448 | sum 7537409 | 0x9371 |
| Dongle_App v1.1.0 | 55868 | sum 5829760 | 0x29371 |
| Dongle_Updater v1.1.0 | 55852 | sum 5828137 | 0x9371 |
| Bridge_App v1.1.3 | 30284 | crc32 1092912762 | 0x10185 |
Raw Cortex-M images (Nordic). Proprietary NZXT code: never publish. Transfer protocol still unknown.

## Remap action table (confirmed 2026-11-17, CAM menu order, profile 0, button Forward)
AA is a single byte. 0xa5..0xaa = mouse actions; everything else = HID keyboard usage (page 0x07) sent by the mouse's keyboard interface.
| CAM label | AA |
|---|---|
| Left Click | a5 |
| Middle Click | a6 |
| Right Click | a7 |
| Mouse Forward | a8 |
| Mouse Back | a9 |
| Mouse DPI | aa |
| A..Z, digits, etc. | standard HID usage (A = 04) |
| WINDOWS DESTRO (Right GUI) | e7 |
| Mute | 7f |
| VolumeUp | 80 |
| VolumeDown | 81 |
| PlayPause | e8 |
| Stop | e9 |
| Backward (previous track) | ea |
| Forward (next track) | eb |
Media keys use the legacy keyboard-page codes 0x7f-0x81 and 0xe8-0xeb (Linux maps these to KEY_MUTE/VOLUMEUP/VOLUMEDOWN/PLAYPAUSE/STOPCD/PREVIOUSSONG/NEXTSONG), not Consumer-page usages.

## Implementation notes (library 0.2)

Frame used by the library: `4e TT LL 00 CMD args…`, padded to 64 bytes.
TT = 02 mouse, 01 receiver. LL = 0x80 | (number of bytes after the length/00 pair,
i.e. CMD + args); this matches every captured packet (`81` for CMD only, `82` for CMD + 1 arg,
`84` for `51 PP BB` + one trailing 00 as CAM sends it, `85` for `50 PP BB 04 AA`, `94` for a
profile write). Replies starting `4e e5` are skipped.

Button read: `4e 02 84 00 51 PP BB 00` -> `4e 02 85 00 01 PP BB 04 AA`.

Info `0b` reply (`4e TT 9e 00` + 30 bytes, offsets from byte 4):
- 0..15 name, ASCII, NUL padded ("HyperSonic")
- 16 configuration byte (meaning unknown)
- 17 mode: 0 = updater/bootloader, 1 = application
- 18..21, 22..25, 26..29 versions of application, bridge, updater: major, minor, patch u16 LE

LED / indicator field: bytes 21..22 as u16 LE = RGB565 (`1f00` → 0x001f blue, `00f8` → red,
`e007` → green, `40fd` → yellow-ish, `1ff8` → magenta/purple), matching the factory
profile colours in the official manual. The factory colours flash correctly for
about two seconds when you long-press the purple button to cycle profiles
(confirmed on the real mouse, 2026-11-17). During normal use the LED is a status
light (green while charging or charged). NZXT CAM has no colour editor. A custom
write with this encoding is stored and reads back (internal slot 1 / user
profile 2: `#ff0000` → `#ffbe6b`; internal slot 4 / user profile 5: `#ff00ff` →
`#ffffff`). **A custom colour write is under test by the user** to see whether
the long-press flash follows the new value. The GUI, `liftctl set --color` and
the CoolerControl `profile_led` channel write the field (Apply writes it like
the other profile body fields and reads it back). `liftctl restore-led-colors`
writes the exact factory bytes above (not a re-encode of `#rrggbb`) and reads
them back. Named profiles still omit the colour, so loading one into another
slot leaves that slot's LED unchanged. No separate LED commit command was found;
no CAM capture of a colour *change* exists in this tree.

Status `b0` (`4e 02 97 00 01 …`): not decoded. Bytes 7..8 read 0x1069..0x107b (4201..4219) across
captures, plausibly battery millivolts, and `64` (100) appears twice, plausibly percent. Not exposed
by any tool until confirmed (e.g. watching it drop on battery).

Receiver (1e71:2101): same vendor interface 01 and the same framing. Commands addressed to the
mouse (TT=02) are assumed to be forwarded over the air (not captured in this session); the library
sends only the read-only `0b` with TT=01 to the receiver itself.

## Active profile (high confidence, write not enabled)

CAM names: `CMD_GET_PROFILE_NUMBER` / `CMD_SET_PROFILE_NUMBER`. Seen in the firmware's command handler (cases 0x44 / 0x45; the purple-button
long press uses the same switch routine) and in a USB capture of CAM:

- Get: `OUT 4e 02 81 00 44` → `IN 4e 02 82 00 01 PP` (PP = internal slot 0..4).
  Firmware copies `*DAT_00030994` into the reply. The same global is the
  fallback index when a 0x92 read is given an out-of-range slot. Captured
  reply had PP=00 (profile 1 was active in that CAM session).
- Set: `OUT 4e 02 82 00 45 PP` → `IN 4e 02 81 00 01`. Firmware clamps PP>4
  back to the current index and calls `FUN_000340f0(PP)` — the same function
  the purple-button long press uses with argument `0xff` (cycle). Captured
  CAM write used PP=00.

`liftctl debug-dump` reads 0x44 (and the other known safe reads) so the
user can confirm the byte changes after a long press. **0x45 is documented
but not sent**: `ENABLE_ACTIVE_PROFILE_APPLY` stays false and 0x45 is not
in `SAFE_COMMANDS` until that confirmation.

Whether a *custom* colour written to bytes 21..22 appears on the two-second
profile-change flash is under test by the user.

Safety: the library refuses to send any command outside the known safe set; `f5 f1 f4 f6 a1 a2 a3 a4 a5`
(firmware/boot/flash related) exist only as `DANGEROUS_*` constants, are not reachable from the CLI, GUI
or plugin, and there is no flashing routine.
