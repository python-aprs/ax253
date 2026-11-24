r"""
AX.25 frames and the TNC2 text format.

``ax253`` encodes and decodes the AX.25 UI frames that APRS and other packet
radio applications send. It is the bottom layer of `kiss3`_ (KISS TNCs) and
`aprs3`_ (APRS), and does no I/O of its own beyond the asyncio protocols.

.. _kiss3: https://github.com/python-aprs/kiss3
.. _aprs3: https://github.com/python-aprs/aprs3

Frames
======

A frame parses from TNC2 monitor text, the format APRS-IS and most TNCs
print:

>>> from ax253 import Frame
>>> frame = Frame.from_str("N0CALL-7>APRS,WIDE1-1*,WIDE2-1:>On the air")
>>> frame.source
Address(callsign=b'N0CALL', ssid=7, digi=False)
>>> [str(p) for p in frame.path]
['WIDE1-1*', 'WIDE2-1']
>>> frame.info
b'>On the air'

``bytes(frame)`` is the AX.25 encoding a KISS TNC sends over the air, and
``Frame.from_bytes`` reads it back:

>>> raw = bytes(frame)
>>> raw[:7]
b'\x82\xa0\xa4\xa6@@`'
>>> print(Frame.from_bytes(raw))
N0CALL-7>APRS,WIDE1-1*,WIDE2-1:>On the air

``Frame.ui`` builds a new UI frame; the ``*`` convention marks digipeaters
that have already repeated it:

>>> print(Frame.ui(source="N0CALL", destination="APRS", path=["WIDE1-1"], info=b">hello"))
N0CALL>APRS,WIDE1-1:>hello

Addresses
=========

>>> from ax253 import Address
>>> address = Address.from_str("N0CALL-7")
>>> address.callsign, address.ssid
(b'N0CALL', 7)
>>> str(address)
'N0CALL-7'

Addresses compare by station (callsign, ignoring case, and SSID), and
compare equal to their text:

>>> frame.source == "n0call-7"
True

Streams
=======

``AX25BytestreamDecoder`` (HDLC-framed bytes) and ``TNC2Decode`` (text
lines) turn a byte stream into frames, keeping partial input between
calls:

>>> from ax253 import TNC2Decode
>>> decoder = TNC2Decode()
>>> [str(f) for f in decoder.update(b"N0CALL>APRS:>one\r\nN0CALL>AP")]
['N0CALL>APRS:>one']
>>> [str(f) for f in decoder.update(b"RS:>two\r\n")]
['N0CALL>APRS:>two']

``FrameDecodeProtocol`` wraps a decoder as an asyncio protocol, with an async
``read()``; ``SyncFrameDecode`` is the base of the blocking transports in
kiss3 and aprs3.
"""

from importlib.metadata import version

from .address import Address
from .decode import FrameDecodeProtocol, GenericDecoder, SyncFrameDecode
from .frame import AX25BytestreamDecoder, Control, Frame, FrameType
from .tnc2 import TNC2Decode, TNC2Protocol

__author__ = "Masen Furer KF7HVM <kf7hvm@0x26.net>"
__copyright__ = "Copyright 2022 Masen Furer and Contributors"
__license__ = "Apache License, Version 2.0"
__distribution__ = "ax253"
__version__ = version(__distribution__)
__all__ = [
    "Address",
    "AX25BytestreamDecoder",
    "Control",
    "Frame",
    "FrameDecodeProtocol",
    "FrameType",
    "GenericDecoder",
    "SyncFrameDecode",
    "TNC2Decode",
    "TNC2Protocol",
    "__distribution__",
    "__version__",
]
