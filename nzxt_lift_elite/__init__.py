"""Libreria non ufficiale per il mouse NZXT Lift Elite (USB 1e71:2129, ricevitore 1e71:2101).

Il punto di ingresso per GUI e plugin CoolerControl è :class:`LiftElite`,
insieme al modello :class:`Profile`, alla tabella delle azioni in
:mod:`nzxt_lift_elite.actions` e ai file in :mod:`nzxt_lift_elite.store`.

Esempio::

    from nzxt_lift_elite import LiftElite

    with LiftElite.open() as mouse:          # cavo o ricevitore
        p0 = mouse.read_profile(0)            # include i 6 tasti
        new = p0.with_changes(polling_hz=1000, dpi_stages={2: 1000},
                              buttons={"forward": "volume-up"})
        mouse.apply_profile(new)              # scrive solo le differenze e verifica
"""

__version__ = "0.2.0"

from .device import DeviceState, HidrawInfo, LiftElite, find_hidraw, list_devices, parse_report_descriptor
from .errors import (
    DangerousCommandError,
    DeviceBusyError,
    DeviceNotFoundError,
    LiftEliteError,
    ProtocolError,
    ValidationError,
    VerificationError,
)
from .profile import Profile, ProfileConfig, load_profiles_json, save_profiles_json
from .protocol import PACKET_SIZE, POLLING_HZ, PRODUCT_ID, PRODUCT_ID_DONGLE, VENDOR_ID, VENDOR_INTERFACE, FirmwareInfo

__all__ = [
    "DangerousCommandError",
    "DeviceBusyError",
    "DeviceNotFoundError",
    "DeviceState",
    "FirmwareInfo",
    "HidrawInfo",
    "LiftElite",
    "LiftEliteError",
    "PACKET_SIZE",
    "POLLING_HZ",
    "PRODUCT_ID",
    "PRODUCT_ID_DONGLE",
    "Profile",
    "ProfileConfig",
    "ProtocolError",
    "VENDOR_ID",
    "VENDOR_INTERFACE",
    "ValidationError",
    "VerificationError",
    "__version__",
    "find_hidraw",
    "list_devices",
    "load_profiles_json",
    "parse_report_descriptor",
    "save_profiles_json",
]
