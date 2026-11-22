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
