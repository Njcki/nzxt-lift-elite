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

## Status `b0`: battery / fuel gauge (decoded from the firmware, 2026-11-23)

`OUT 4e 02 81 00 b0` → `IN 4e 02 97 00 <23 bytes>`. Read-only: the firmware
command handler (case 0xb0) only copies RAM into the reply. Same command and the
same 23-byte reply through the receiver (the receiver forwards TT=02 to the
mouse). CAM sends it after every profile write.

The mouse has an I2C fuel gauge. The register table the firmware polls
(flash 0x3ba64: register, size, RAM pointer) matches the CellWise CW2217:
0x02 VCELL, 0x04 SOC, 0x06 TEMP, 0x0E CURRENT, 0xA4 cycle count, 0xA6 SOH. The
I2C completion callback (~0x31990) converts and stores them. Payload layout
(offsets after `4e 02 97 00`; absolute packet byte = offset + 4; u16 are
little-endian):

| off | size | meaning | source in the firmware |
|---|---|---|---|
| 0 | 1 | gauge-data-valid flag (1 after the first complete gauge read, 0 after a gauge re-init) | RAM 0x20005967 |
| 1 | 2 | design capacity, mAh (constant 320) | struct +0 |
| 3 | 2 | **cell voltage, mV** (VCELL × 0.3125) | struct +2 |
| 5 | 2 | current, signed, firmware-scaled (~mA, unit provisional), median of 15 samples; > 0 while charging | struct +4 |
| 7 | 2 | full capacity, mAh (constant 320) | struct +6 |
| 9 | 2 | remaining capacity, mAh (= capacity × SOC / 100) | struct +8 |
| 11 | 2 | cycle count (CYCLECNT >> 4) | struct +10 |
| 13 | 2 | **state of charge, %** (gauge SOC; capped at 99 while charging) | struct +12 |
| 15 | 1 | state of health, % | struct +14 |
| 17 | 1 | temperature, °C (TEMP / 2 − 40) | struct +16 |
| 18 | 1 | **charge state**: 0 on battery, 1 charging, 2 cable in and full | struct +17 |
| 19 | 4 | power-management word written by `b1` (never sent by this library); low half = low-battery cut-off threshold | RAM 0x200000e8 |

So the percent comes from the fuel gauge itself; no voltage curve is needed.
The charge state is set by the charger task (~0x31c2c): only while VBUS is
present (GPIO P0.31), from the battery temperature (charging allowed below
45 °C, reduced below 11 °C) and the current; it becomes 2 when the current
stops and SOC is 100. Other firmware users of the same struct: the LED task
breathes green while charging and stays green when full; the idle task powers
the mouse off when SOC < 3 % on battery; a SOC change raises host event 0x80.

Captured replies: wired `01 4001 6910 0000 4001 4001 0000 6400 6400 18 02 08004001`
= valid, 4201 mV, 0 mA, SOC 100 %, SOH 100 %, 24 °C, state 2 (full, cable in);
receiver `… 7b10 … 19 02 …` = 4219 mV, 25 °C, also full (the cable was plugged
in for charging during that session).

Library: `parse_battery_reply()` / `BatteryStatus` and
`LiftElite.read_battery()` (device lock, `b0` only). The header byte after
`4e 02 97 00` is the valid flag, so replies are matched on the 4-byte prefix
(`REPLY_STATUS_PREFIX`); flag 0 or a voltage outside 2.8–4.5 V gives
"battery unknown" instead of an error. If the SOC field were ever > 100 the
percent is estimated from the voltage with a typical Li-ion curve
(`battery_percent_from_mv`, flagged `estimated`). Still provisional until
confirmed by watching it drop on battery (`liftctl battery --watch 300`): the
current unit and sign convention, and the exact SOC behaviour near 100 % while
charging.
Tools: `liftctl battery [--json] [--watch SECONDS]`, the GUI battery indicator
(read on connect and every 30 s on the cable / 60 s through the receiver, only
while the window is focused). The CoolerControl plugin does not expose it: its
status metrics are temperature, fan speed, MHz and watts only, with no percent.

### Receiver

Receiver (1e71:2101): same vendor interface 01 and the same framing. Commands addressed to the
mouse (TT=02) are assumed to be forwarded over the air (not captured in this session); the library
sends only the read-only `0b` with TT=01 to the receiver itself.

## Active profile

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
user can confirm the byte changes after a long press. 0x45 is not in
`SAFE_COMMANDS`. Only `LiftElite.activate_profile` sends it (`liftctl activate`,
`liftctl reset-defaults`, and the GUI), through an explicit one-command
allowlist; see "Setting the active profile (0x45)" below. The GUI uses the
same path (`ENABLE_ACTIVE_PROFILE_APPLY = True` since confirmation on a real
mouse on 2026-11-23): **Make active** marks the edited profile; Apply sends
0x45 first and verifies it with 0x44, then writes buttons and settings.

Whether a *custom* colour written to bytes 21..22 appears on the two-second
profile-change flash is under test by the user.

Safety: the library refuses to send any command outside the known safe set; `f5 f1 f4 f6 a1 a2 a3 a4 a5`
(firmware/boot/flash related) exist only as `DANGEROUS_*` constants, are not reachable from the CLI, GUI
or plugin, and there is no flashing routine.

## DPI stage change notifications (none) and the GUI live poll

A short press of the purple button changes the active DPI stage of the active
profile inside the mouse only. In the application firmware's button handler
(short-press branch) the firmware increments the profile's
stage byte (wrapping at the stage count), reprograms the sensor, blinks the
indicator and schedules the same deferred flash save used after a profile write
(`0x93`/`0xb1`). No report is queued for the host on the vendor interface, and the
standard HID mouse report has no DPI field, so there is no notification to listen for.

The GUI polls instead, and only with read-only commands already used at startup:
`0x44` (active profile) followed by one `0x92` read of that profile (byte 9 = stage).
That is 2 short transactions every 700 ms on the cable or every 1000 ms through the
receiver. The poll runs only while the window is focused, a mouse is connected and no
other operation (Apply, full read, hotplug re-read) is in progress. It never opens
the device itself, never waits for another program's lock, and backs off to 5 s after
3 failures in a row (mouse asleep, busy, or unplugged). It never writes.
When the stage changes, the GUI moves the applied baseline; unsaved edits are kept.
A stage the user picked but has not applied yet stays pending.


## Button remap writes go to the ACTIVE profile (firmware ignores PP)

Found on the real mouse on 2026-11-23. Profile 2 was being edited while profile 1
was active, and the middle button was set to Mute. The write sent
`4e 02 85 00 50 01 01 04 7f`, and the read-back of profile 2 still returned
middle-click.

Firmware command handler, case `0x50`, type `04` (the only type we send): the
profile index is loaded from the active-profile global (`*DAT_00030cdc`). PP
(`pbVar22[1]`) is not used for the write. The type `01` (macro) path and `0x93`
(profile body) honour PP, and so does the button read `0x51`. So a remap always
changes the active profile's button. The decompiled type-04 path does not call
the deferred flash-save routine either.

Library rule since then: before any `0x50` write, `0x44` is read (read-only). The
write is refused unless the target profile is the active one. If `0x44` cannot be
read, the write is refused too. The check happens before anything of that Apply is
written, body included. The GUI locks the button rows of a non-active profile.
The emulator models the firmware behaviour. Mute = `7f` (consumer slot 4 in the
firmware table) is consistent with the firmware and with the CAM menu capture.


## Setting the active profile (0x45): `liftctl activate --profile N`

Packets (PP = internal slot 0..4 = profile N-1), all under the device lock:
1. `4e 02 81 00 44` → `4e 02 82 00 01 PP_before` (print current)
2. only if different: `4e 02 82 00 45 PP` (zero padded) → `4e 02 81 00 01`
3. `4e 02 81 00 44` → must answer PP (up to 3 reads, 100 ms apart), else error

Safety gate: `assert_safe_packet(..., allow=EXPLICIT_ALLOWLIST)` where
`EXPLICIT_ALLOWLIST = {0x45}`. Only `LiftElite.activate_profile` passes it. The
packet must be exactly `4e 02 82 00 45 PP` with PP ≤ 4, addressed to the mouse
(not the receiver), with zero padding. Firmware/boot commands (f1 f4 f5 f6 a1–a5)
can never be allowed. `--dry-run` reads 0x44 but does not send 0x45.

Firmware (Mouse_App 1.1.5 command handler, case 0x45, and the profile-switch
callees):
- The 0x45 handler clamps PP > 4 to the current index, calls `FUN_000340f0(PP)`
  and replies with 1 byte (`4e 02 81 00 01`).
- `FUN_000340f0` is the routine the long press calls with `0xff` (cycle). With an
  explicit index it does the following:
  - stores the new index in the active-profile byte;
  - re-applies that profile's polling (`FUN_000340b8`; 8000 Hz only when USB
    is up), resets the DPI-button state (`FUN_00032808`) and re-applies the
    sensor settings (`FUN_00034380`, `FUN_000342c0`);
  - shows an LED colour for 2 s (`FUN_00031834(rgb565, 2)`). The decompiled code
    takes that colour from the profile that was active *before* the switch
    (it reads the index before storing the new one). So the flash after
    `liftctl activate` may show the OLD profile's colour. The long press
    instead queues its own indication (`FUN_000310b4(4)`).
  - queues the same deferred config save (`FUN_0003359c(3,0)`) as the long
    press and every 0x93 profile write. The active index persists like after a
    long press.
- It does not modify, reorder or reset any profile data. The reset-all-profiles
  routine `FUN_0003403c` is not called. No firmware/flash-erase path is involved.

### 0x45 and the sensor / polling: why the library re-sends the body (0x93)

Real mouse (2026-11-23): after switching to profile 1 with 0x45 (stored: stage 2
= 1000 DPI, 8000 Hz; 0x92 read back correct) the pointer was much slower than it
should be. Cycling DPI stages did not help; writing the profile again (polling
8000 → 2000 → 8000) fixed it. Switching to a profile with a lower polling rate
lost smoothness the same way. Purple-button long presses were fine.

What the firmware does differently (Mouse_App 1.1.5, base 0x29000):
- Long press (button handler): `FUN_000340f0(0xff)` then `FUN_000310b4(4)`. That
  second call queues event 9 / code 4 ("profile changed") through
  `FUN_0003104c`. When the link flag at `0x20007331` is set, `FUN_0003104c`
  sends the event over the internal link (`FUN_000321a4`), otherwise it handles
  it locally (`FUN_0003425c`).
- 0x93 profile write (command handler): stores the body, then calls
  `FUN_0003208c()` when that same flag is set. `FUN_0003208c` derives the
  polling code from the active profile's Hz and posts message type 3 via
  `FUN_00032048`. After that come the same `340b8 / 32808 / 34380 / 342c0 / 3359c`
  calls as the switch routine.
- 0x45 (command handler): only `FUN_000340f0(PP)`, with neither the event (9/4)
  nor the polling message (type 3). The locally applied parts (`FUN_000340b8`,
  `FUN_00034380`, `FUN_000342c0`) read the NEW profile, but the other side of the
  link is never told about the switch. The most likely result is that it keeps
  the previous profile's report rate / sensor timing. This fits both symptoms
  (8000 Hz profile running slow, lower-rate profile not smooth). The bridge side
  was not decompiled, so this part is an inference.
- Fix in the library: `LiftElite.activate_profile` (used by `liftctl activate`,
  the GUI **Make active** / auto-apply, and the final re-activation of
  `reset-defaults`) re-sends the profile's current, unchanged body with the normal
  0x93 write right after a verified 0x45 and reads it back
  (`reapply_profile`). Sequence: `44 45 44 92 93 b0 92`. In dry-run only 0x44 is
  read. Long presses need nothing, because the firmware notifies by itself.

## Factory defaults (firmware default profile table)

`FUN_0003403c` (the reset-all-profiles routine behind command 0x94, which the
library never sends) copies a 0x31-byte template at `0x3bcfb` (file offset
`0x12cfb`) into each of the 5 profile structs (`0x31` bytes each, active profile
index at offset 0 of the array). It then sets the active-stage byte to the
profile index and the colour from the table at `0x3bd2c`. It also sets the
active profile to 2 (profile 3). Template, using the struct offsets the 0x92/0x93
handlers use:

| struct off | bytes | meaning |
|---|---|---|
| +1 | `05` | stage count 5 |
| +2 | `02` → overwritten with the profile index | active stage: profile N → stage N |
| +3..+12 | `9001 2003 4006 800c 0019` | DPI 400 / 800 / 1600 / 3200 / 6400 |
| +0xd | `e803` | polling 1000 Hz (u16 Hz; 0x92 reports code 2) |
| +0xf | `01` | lift-off 1 mm |
| +0x10 | `0f` | stage mask |
| +0x11 | from table | LED colour `1f00 00f8 e007 40fd 1ff8` |
| +0x13 | `01` | motion sync on |

The same template is in the updater and dongle images. This matches the manual
("400 / 800 / 1600 (Default) / 3200 / 6400"; the out-of-box active profile 3 sits
at stage 3 = 1600) and the reviews (1000 Hz default). `liftctl reset-defaults`
writes these values with the normal verified 0x93 write (stage count and mask
are never written). It keeps the user's active profile instead of switching to
profile 3.
