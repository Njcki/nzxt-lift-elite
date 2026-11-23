"""Exceptions raised by the NZXT Lift Elite library."""


class LiftEliteError(Exception):
    """Base error for this library."""


class DeviceNotFoundError(LiftEliteError):
    """No matching hidraw node was found for the Lift Elite vendor interface."""


class ProtocolError(LiftEliteError):
    """A packet was malformed, unexpected, or not acknowledged in time."""


class VerificationError(LiftEliteError):
    """A write succeeded but the value read back does not match what was sent."""


class ValidationError(LiftEliteError):
    """A user-supplied value is outside the supported range or format."""


class DangerousCommandError(LiftEliteError):
    """Refused to send a firmware/boot command that could brick the mouse."""


class DeviceBusyError(LiftEliteError):
    """Another program (GUI, CLI or CoolerControl plugin) holds the device lock."""


class InactiveProfileRemapError(LiftEliteError):
    """Button remap refused: the target profile is not the one active on the mouse.

    The firmware's 0x50 handler (remap type 04) ignores the profile byte and
    writes the button into the *active* profile, so remapping any other
    profile would silently change the wrong one.
    """
