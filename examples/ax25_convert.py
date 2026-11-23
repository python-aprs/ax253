#!/usr/bin/env python3
"""
Convert packets between TNC2 text and AX.25 bytes (as hex).

Useful for checking what a TNC will put on the air for a given packet, or
for reading a hex dump from a capture. Each argument (or each line of
stdin) is converted: TNC2 text becomes hex, and hex becomes TNC2 text.

    $ python ax25_convert.py "N0CALL>APRS,WIDE1-1:>hello"
    82 a0 a4 a6 40 40 60 9c 60 86 82 98 98 60 ae 92 88 8a 62 40 63 03 f0 3e 68 65 6c 6c 6f

    $ python ax25_convert.py "82 a0 a4 a6 40 40 60 9c 60 86 82 98 98 60 ae 92 88 8a 62 40 63 03 f0 3e 68 65 6c 6c 6f"
    N0CALL>APRS,WIDE1-1:>hello

Add --explain to also show how each address byte was decoded.
"""

import argparse
import string
import sys

from ax253 import Frame


def is_hex(text: str) -> bool:
    if ">" in text:  # every TNC2 packet has one
        return False
    digits = text.replace(" ", "").replace(":", "")
    return len(digits) % 2 == 0 and all(c in string.hexdigits for c in digits)


def explain(frame: Frame) -> str:
    rows = [
        ("destination", frame.destination),
        ("source", frame.source),
        *((f"path {i}", p) for i, p in enumerate(frame.path)),
    ]
    lines = [
        f"  {name:<12} {bytes(address).hex(' ')}  {address}"
        f"{'  (repeated)' if address.digi and name.startswith('path') else ''}"
        for name, address in rows
    ]
    lines.append(
        f"  {'control':<12} {bytes(frame.control).hex()}  {frame.control.ftype.name}"
    )
    lines.append(f"  {'info':<12} {frame.info!r}")
    return "\n".join(lines)


def convert(text: str, show_details: bool = False) -> str:
    if is_hex(text):
        frame = Frame.from_bytes(bytes.fromhex(text.replace(":", " ")))
        out = str(frame)
    else:
        frame = Frame.from_str(text)
        out = bytes(frame).hex(" ")
    return f"{out}\n{explain(frame)}" if show_details else out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("packets", nargs="*", help="TNC2 text or hex (default: stdin)")
    parser.add_argument("--explain", action="store_true", help="show each field")
    args = parser.parse_args()
    packets = args.packets or [line.strip() for line in sys.stdin if line.strip()]
    status = 0
    for packet in packets:
        try:
            print(convert(packet, args.explain))
        except ValueError as exc:
            print(f"error: {packet!r}: {exc}", file=sys.stderr)
            status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
