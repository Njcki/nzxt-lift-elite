# Reverse engineering the NZXT Lift Elite

*[Leggi in italiano](REVERSE_ENGINEERING.it.md)*

This guide explains how the vendor protocol of the NZXT Lift Elite mouse was
worked out, so that you can check the findings, extend them, or use the same
method on another device. The result of the work is in
[`PROTOCOL.md`](../PROTOCOL.md) and in `nzxt_lift_elite/protocol.py`.

NZXT does not document the protocol. Everything here comes from watching what
the official software (NZXT CAM on Windows) sends to the mouse, from static
analysis of that software, and from careful, read-mostly testing on Linux.
This project is not affiliated with NZXT.

> **Read the [safety section](#7-safety-rules) before you send anything to a
> real device.** A wrong command can leave the mouse in its updater or
> bootloader, or worse.

## Contents

1. [The device](#1-the-device)
2. [Tools](#2-tools)
3. [Capturing USB traffic from NZXT CAM](#3-capturing-usb-traffic-from-nzxt-cam)
4. [Finding the packet framing](#4-finding-the-packet-framing)
5. [Mapping fields by diffing captures](#5-mapping-fields-by-diffing-captures)
6. [Static analysis of the software and firmware](#6-static-analysis-of-the-software-and-firmware)
7. [Safety rules](#7-safety-rules)
8. [Verifying on Linux](#8-verifying-on-linux)
9. [What is still unknown](#9-what-is-still-unknown)
10. [Sharing captures without leaking data](#10-sharing-captures-without-leaking-data)

## 1. The device

| | USB ID | Notes |
|---|---|---|
| Mouse, USB cable | `1e71:2129` | exposes three HID interfaces |
| 2.4 GHz receiver | `1e71:2101` | same vendor interface and framing |

Only one interface matters for configuration: the **vendor-defined HID
interface `MI_01`** (interface number 1). It uses 64-byte interrupt reports:

- host → device: interrupt OUT, endpoint `0x02`
- device → host: interrupt IN, endpoint `0x82`

The other interfaces carry ordinary mouse and keyboard input (the keyboard
interface is what the mouse uses when a button is mapped to a key).

On Linux each interface gets its own `/dev/hidrawN` node. To see which node
is which:

```sh
lsusb -d 1e71:                      # is it connected, cable or receiver?
for d in /sys/class/hidraw/hidraw*; do
    echo "$d: $(grep HID_ID "$d/device/uevent") $(basename "$(readlink -f "$d/device")")"
done
```

The sysfs device name ends in `:1.1` style suffixes; the vendor interface is
the one on interface `1` (the library picks it automatically, see
`nzxt_lift_elite/device.py`).

## 2. Tools

Everything used is free software or freely available:

| Purpose | Tool |
|---|---|
| Official configuration software (the "oracle") | NZXT CAM on Windows |
| USB capture on Windows | [USBPcap](https://desowin.org/usbpcap/) |
| Viewing and filtering captures | [Wireshark](https://www.wireshark.org/) / `tshark` |
| Scripting, parsing captures, diffing | Python 3 |
| Disassembly / decompilation | [Ghidra](https://ghidra-sre.org/) (also headless), [Capstone](https://www.capstone-engine.org/), `pefile` |
| Testing on Linux | the kernel `hidraw` interface, `liftctl --debug` / `--dry-run` / `debug-dump` |

A Windows virtual machine with USB pass-through works as well as a real
Windows install for the capture step.

## 3. Capturing USB traffic from NZXT CAM

The basic idea: **change exactly one setting in CAM, capture what goes over
the wire, and compare.**

### 3.1 Recording

1. Install USBPcap (it ships with the Wireshark installer as an option) and
   reboot.
2. Plug the mouse in **with the cable** first: it is the simplest path (no
   radio, no receiver in the middle). Repeat interesting captures later with
   the receiver.
3. Start Wireshark, pick the `USBPcapN` interface of the root hub the mouse
   is on (if unsure, capture all of them once and look for the device
   descriptor with vendor `0x1e71`).
4. Start the capture, then start CAM: its start-up reads are a goldmine
   (device info, every profile, every button mapping, the active profile).
5. Do **one** action in CAM, wait a couple of seconds, write down the time
   and what you did. Then the next action. Short captures with one change
   each are much easier to read than one long session.
6. Save as `.pcap`/`.pcapng`. USBPcap captures use link type 249
   (`LINKTYPE_USBPCAP`): every packet has a small USBPcap header (IRP id,
   status, function, direction, bus, device address, endpoint, transfer
   type, data length) followed by the payload. That header is easy to parse
   with a few lines of Python if you want to script the analysis instead of
   clicking through Wireshark.

Useful single-setting experiments:

- change the polling rate through every value
- change a single DPI stage value (for example stage 2 from 800 to 1000)
- toggle motion sync, change lift-off distance
- assign each entry of CAM's button action menu, in menu order, to one button
- switch profiles in CAM
- reset a profile to factory defaults

### 3.2 Wireshark filters

First find the device address (it changes when you re-plug the mouse):

```
usb.idVendor == 0x1e71
```

Then keep only the interrupt traffic of that device (replace `N`):

```
usb.device_address == N && usb.transfer_type == 0x01
```

Further narrowing:

| Filter | Shows |
|---|---|
| `usb.endpoint_address == 0x02` | host → mouse (commands) |
| `usb.endpoint_address == 0x82` | mouse → host (replies) |
| `usb.capdata[0] == 0x4e` | only vendor frames (they all start with `4e`) |
| `usb.capdata[0] == 0x4e && usb.capdata[4] == 0x92` | one command byte, here *read profile* |
| `!(usb.capdata[1] == 0xe5)` | hide the short "busy" replies |

Depending on the Wireshark version and on whether it decodes the HID report
descriptor, the payload field is `usb.capdata` or `usbhid.data`; use
whichever shows up when you click the payload bytes.

To get a text dump you can diff:

```sh
tshark -r capture.pcapng -Y 'usb.transfer_type == 0x01 && usb.capdata[0] == 0x4e' \
       -T fields -e frame.time_relative -e usb.endpoint_address -e usb.capdata
```

## 4. Finding the packet framing

Lining up commands and replies quickly shows a fixed structure. Every vendor
frame is 64 bytes, zero padded:

```
4e TT LL 00 CMD args...
```

| Byte | Meaning |
|---|---|
| 0 | `0x4e`, magic |
| 1 | target: `02` = mouse, `01` = receiver |
| 2 | `0x80 \| n`, where `n` = number of bytes after byte 3 (command + arguments) |
| 3 | `00` for single-frame messages |
| 4 | command byte |
| 5… | arguments |

The length rule was derived from the captures and matches every packet seen:
`81` for a command with no arguments, `82` for one argument, `85` for a
5-byte button write, `94` for a full profile write.

Replies use the same header. The first payload byte is a status (`01` = OK)
for nearly every command; the info command `0b` is the exception, its data
starts immediately. The device often answers first with a short `4e e5 …`
frame, which means "busy / acknowledged"; skip it and read again.

The pattern is strictly **request → response**: the host sends one OUT
report and waits for the matching IN report. The library does the same and
never pipelines commands.

## 5. Mapping fields by diffing captures

With the framing known, each experiment becomes a small diff. Some examples
of how specific fields were identified:

**Profiles (`92` read, `93` write).** At start-up CAM sends
`4e 02 82 00 92 NN` for `NN` = 0…4 and gets back `4e 02 94 00 01 NN` followed
by the profile body. A write is the same body sent with command `93`.
Comparing bodies before and after one change in CAM:

- polling rate → byte 6 changes (`0`=250, `1`=500, `2`=1000, `3`=2000,
  `4`=4000, `5`=8000 Hz)
- DPI stage 2 from 800 to 1000 → bytes 12–13 go to `e8 03`, so the DPI values
  are five little-endian `uint16` at bytes 10–19
- motion sync → byte 23 (`1` on, `0` off)
- lift-off → byte 7 (values 1 and 2 seen)

**Active DPI stage (byte 9).** The captures alone could not tell whether this
byte was 0-based or 1-based. It was settled on Linux with read-only reads:
pressing the DPI button and reading the profile after each press gave
`0, 2, 3, 4`, then wrapped to `0`, and at `0` the pointer was at the first
(lowest) DPI value. So the byte is **0-based**; the tools show it as 1–5.

**Buttons (`51` read, `50` write).** CAM reads every button of every profile
at start-up with `4e 02 84 00 51 PP BB 00` and writes with
`4e 02 85 00 50 PP BB 04 AA`. Assigning each entry of CAM's action menu, in
order, to the same button produced the action table in `PROTOCOL.md`:
`a5`…`aa` are mouse actions, other values are HID keyboard usages (media keys
use the legacy keyboard-page codes `7f`–`81` and `e8`–`eb`).

**Profile LED colour (bytes 21–22).** These two bytes differ for each factory
profile: `1f00`, `00f8`, `e007`, `40fd`, `1ff8`. Read as little-endian
16-bit values they are `0x001f`, `0xf800`, `0x07e0`, `0xfd40`, `0xf81f`,
which is exactly RGB565 blue, red, green, yellow and purple, the profile
colours listed in the official manual. The colour is visible for about two
seconds when you long-press the purple button to change profile. CAM has no
colour editor, so there is no capture of a colour change; whether a custom
value shows up on that flash is still being tested.

**Active profile (`44` read, `45` set).** CAM sends
`4e 02 81 00 44` at start-up and gets `4e 02 82 00 01 PP`; it also sends
`4e 02 82 00 45 PP` (reply `4e 02 81 00 01`). The names come from CAM itself
(`CMD_GET_PROFILE_NUMBER` / `CMD_SET_PROFILE_NUMBER`, see §6) and the
firmware handler confirms the meaning. Reading is enabled in the tools;
setting is implemented but switched off until it is confirmed on the real
device.

**Things that are *not* mapped** are kept as raw bytes and written back
unchanged (see §7).

## 6. Static analysis of the software and firmware

Captures show *what* is sent; static analysis helps explain *why*, names the
commands, and reveals commands CAM sends only in rare situations.

**The host software.** The device logic of CAM lives in a native Node.js
module, a Windows x64 PE written in Rust. Useful starting points:

- plain `strings` already reveals the command names (`CMD_GET_PROFILE_NUMBER`,
  `CMD_SET_PROFILE_NUMBER`, `CMD_GET_ACTIVE_CPI_INDEX`, …) and the Rust source
  paths of the device classes, which tell you which code handles this mouse
- the PE exception directory (`.pdata`) gives reliable function boundaries,
  which helps a lot with Rust code
- logging/tracing metadata embeds `file.rs:line` locations; annotating call
  sites with them makes the decompiled code much easier to follow
- Rust `async` state machines decompile badly by default (Ghidra reports bad
  instruction counts); fixing the jump-table bounds by hand solves it
- the function that builds the `4e …` frame is a good anchor: every command
  wrapper calls it with a constant command byte

**The firmware.** The mouse runs a Nordic nRF52840 (ARM Cortex-M4, Thumb,
FreeRTOS). The firmware images are embedded in CAM for its updater. They are
proprietary and are **not** included in or linked from this repository. If you
analyse your own copy:

- load the raw image as `ARM:LE:32:Cortex` at its correct base address; the
  reset vector in the vector table (second word) tells you whether the base is
  right
- find the **command dispatcher**: a large `switch` on the command byte of the
  incoming report. Commands already confirmed from captures (`92`, `93`, `50`,
  `51`) are the anchors; once you have those cases, the neighbouring cases
  are the remaining commands
- follow what each case reads or writes. For example, case `44` copies a
  global "current profile" index into the reply, the same global is the
  fallback when `92` gets an out-of-range slot, and case `45` calls the same
  profile-switch routine that the purple-button long press calls. That is how
  `44`/`45` were identified with high confidence
- helper scripts that find literal-pool users of a constant and `BL` callers
  of a function are enough to navigate a small Thumb image without symbols

Static analysis findings are treated as **hypotheses** until a capture or a
safe read on the real device confirms them. `PROTOCOL.md` says which fields
are confirmed and which are not.

## 7. Safety rules

These rules are built into the library and should be followed by anyone
experimenting:

1. **Never send unknown commands.** No fuzzing of the command byte. The
   library has an allowlist (`SAFE_COMMANDS` in `protocol.py`) and refuses
   anything else before it reaches the device.
2. **Read before you write.** Start with read-only commands (`0b` info,
   `92` read profile, `51` read button, `44` active profile, `b0` status).
   `--dry-run` prints the packets that *would* be written without sending
   them.
3. **Back up first.** Before any write the tools save a copy of all five
   profiles (`~/.config/nzxt-lift-elite/backups/`, last 50 kept).
4. **Change only what you understand.** Writes start from the bytes just read
   from the device and modify only decoded fields; unknown bytes are written
   back as they were.
5. **Verify every write** by reading it back and comparing.
6. **Test against the emulator** (`nzxt_lift_elite/mock.py`, `--mock`) before
   touching hardware.
7. **Keep a known-good configuration** you can restore (`liftctl save --all`).

### Firmware update and bootloader commands

The firmware contains commands to switch boot mode, reset, erase, write and
verify flash. **They can brick the mouse.** They are deliberately **not
documented in detail** here. In the library they exist only as
`DANGEROUS_*` constants so that they can be recognised and refused; nothing
in the CLI, GUI or CoolerControl plugin can send them, and there is no
flashing routine. If you find one of these command bytes in a capture, do
not replay it.

## 8. Verifying on Linux

Install the udev rule from the [README](../README.md) so you can open the
hidraw node as a normal user, then:

```sh
liftctl devices                 # what is connected (sysfs only, opens nothing)
liftctl firmware-info           # read-only: command 0b
liftctl --debug show            # read-only, hex dump of every packet on stderr
liftctl debug-dump --out a.txt  # read-only: all known reads, in hex
```

`debug-dump` is the tool for confirming a field: run it, change something
*on the mouse itself* (press the DPI button, long-press the purple button),
run it again with `--out b.txt` and `diff a.txt b.txt`.

If you want to experiment from Python, this is the whole exchange for the
read-only info command (adjust the node; the library adds locking, retries
and the safety checks):

```python
import os, select

frame = bytes([0x4e, 0x02, 0x81, 0x00, 0x0b]).ljust(64, b"\0")
fd = os.open("/dev/hidrawN", os.O_RDWR)
# If the report descriptor of this interface does not declare 0x4e as a
# report ID, hidraw expects a leading report ID 0: write b"\0" + frame instead.
os.write(fd, frame)
while select.select([fd], [], [], 1.0)[0]:
    reply = os.read(fd, 72)
    if reply[:1] == b"\0":
        reply = reply[1:]         # strip report ID 0 if present
    if reply[1] != 0xe5:          # skip "busy" frames
        print(reply[:64].hex(" "))
        break
os.close(fd)
```

The library reads the report descriptor from sysfs and decides by itself
whether the report ID byte is needed.

Use the library (`from nzxt_lift_elite import LiftElite`) rather than raw
writes for anything beyond reads: it enforces the allowlist.

## 9. What is still unknown

- **Active profile set (`45`)**: packet known, firmware routine known, not yet
  confirmed on the device, so it is disabled.
- **Custom LED colours**: a custom RGB565 value is stored and reads back;
  whether the profile-change flash shows it is under test.
- **Status `b0`**: not decoded. Two bytes look like a battery voltage in
  millivolts and `100` appears twice, plausibly a percentage. Not exposed until
  confirmed (for example by watching it drop on battery).
- **Receiver**: commands for the mouse (target `02`) are assumed to be
  forwarded over the air; the button reads and receiver versions were captured
  over the cable.
- Byte 20 of the profile (`0x0f`, probably an enabled-stage mask) and the exact
  lift-off millimetre mapping.

Captures that answer any of these are welcome.

## 10. Sharing captures without leaking data

USB captures can contain more than you think. Before sharing one:

- the replies to `03` and `b8` contain a **device-unique identifier**; redact
  those bytes
- a full-bus capture also contains every other device on that hub (and
  anything typed on a USB keyboard): capture only the hub the mouse is on, or
  filter and export only the mouse's packets (*File → Export Specified
  Packets*)
- rename files so they do not contain user names or machine names
- never share firmware images or CAM binaries: they are NZXT's property
