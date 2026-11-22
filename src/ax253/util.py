"""Python ax253 Utility Functions."""

__author__ = "Masen Furer KF7HVM <kf7hvm@0x26.net>"
__copyright__ = "Copyright 2022 Masen Furer and Contributors"
__license__ = "Apache License, Version 2.0"


def optional_bool_or_bytes(v):
    """pass through None and bool; otherwise cast as `bytes`."""
    if v is not None and not isinstance(v, bool):
        return bytes(v)
    return v
