"""Frame Check Sequence

The FCS is a sequence of 16 bits used for checking the integrity of a
received frame.

Derived from https://github.com/casebeer/afsk

Copyright (c) 2013 Christopher H. Casebeer. All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

   1. Redistributions of source code must retain the above copyright notice,
      this list of conditions and the following disclaimer.

   2. Redistributions in binary form must reproduce the above copyright notice,
      this list of conditions and the following disclaimer in the documentation
      and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""

import struct

from attrs import define, field

__author__ = "Christopher H. Casebeer"
__copyright__ = "Copyright (c) 2013 Christopher H. Casebeer. All rights reserved."
__license__ = "BSD 2-clause Simplified License"


def _crc_table() -> tuple[int, ...]:
    table = []
    for byte in range(256):
        crc = byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
        table.append(crc)
    return tuple(table)


# CRC-16/X.25 (reflected polynomial 0x8408), one entry per input byte
_CRC_TABLE = _crc_table()


@define
class FCS:
    fcs: int = field(default=0xFFFF)

    def update_bit(self, bit) -> "FCS":
        check = self.fcs & 0x1 == 1
        self.fcs >>= 1
        if check != bit:
            self.fcs ^= 0x8408
        return self

    def update(self, data: bytes) -> "FCS":
        # AX.25 sends each octet least-significant bit first, and the FCS
        # (CRC-16/X.25, reflected polynomial 0x8408) is computed in that
        # same order: the same result as update_bit() for each bit, LSB
        # first, a byte at a time from a lookup table.
        crc, table = self.fcs, _CRC_TABLE
        for byte in data:
            crc = (crc >> 8) ^ table[(crc ^ byte) & 0xFF]
        self.fcs = crc
        return self

    def digest(self) -> bytes:
        return struct.pack("<H", ~self.fcs % 2**16)

    @classmethod
    def from_bytes(cls, packet: bytes) -> "FCS":
        return FCS().update(packet)

    def __bytes__(self) -> bytes:
        return self.digest()
