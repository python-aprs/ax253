#!/usr/bin/env python3
"""
Regenerate direwolf_frames.json from packets.txt using Dire Wolf.

Each monitor-format packet is modulated to audio with Dire Wolf's
``gen_packets`` and demodulated with ``atest -h``, which prints the exact
AX.25 bytes (without FCS) and Dire Wolf's own monitor-format rendering.
Those bytes are what a Dire Wolf TNC puts on the air and hands to KISS
clients, so ax253 must decode them, and should encode the same bytes.

Requires ``gen_packets`` and ``atest`` on PATH (``apt install direwolf``).
"""

import json
import pathlib
import re
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).parent
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
HEX_LINE = re.compile(r"^\s+[0-9a-f]{3}:\s+((?:[0-9a-f]{2} ?)+)")


def direwolf_version() -> str:
    out = subprocess.run(
        ["direwolf", "-v"], capture_output=True, text=True, check=False
    ).stdout
    match = re.search(r"Dire Wolf (?:DEVELOPMENT )?version [^\s]+", ANSI.sub("", out))
    return match.group(0) if match else "unknown"


def encode(packet: str, tmp: pathlib.Path) -> dict:
    txt = tmp / "p.txt"
    wav = tmp / "p.wav"
    # no trailing newline: gen_packets would include it in the info field
    txt.write_bytes(packet.encode("latin1"))
    subprocess.run(
        ["gen_packets", "-o", str(wav), str(txt)],
        capture_output=True,
        check=True,
    )
    out = ANSI.sub(
        "",
        subprocess.run(
            ["atest", "-h", str(wav)], capture_output=True, text=True, check=True
        ).stdout,
    )
    monitor = None
    hex_bytes = []
    for line in out.splitlines():
        if line.startswith("[0] ") and monitor is None:
            monitor = line[4:]
        m = HEX_LINE.match(line)
        if m:
            hex_bytes.append(m.group(1).strip())
    if monitor is None or not hex_bytes:
        raise ValueError(f"Dire Wolf did not decode {packet!r}:\n{out}")
    return {
        "input": packet,
        "direwolf_monitor": monitor,
        "ax25": " ".join(hex_bytes),
    }


def main() -> int:
    packets = [
        line
        for line in (HERE / "packets.txt").read_text("latin1").splitlines()
        if line and not line.startswith("#")
    ]
    with tempfile.TemporaryDirectory() as tmp:
        frames = [encode(p, pathlib.Path(tmp)) for p in packets]
    result = {
        "generator": direwolf_version() + " (gen_packets | atest -h)",
        "frames": frames,
    }
    (HERE / "direwolf_frames.json").write_text(json.dumps(result, indent=1) + "\n")
    print(f"wrote {len(frames)} frames")
    return 0


if __name__ == "__main__":
    sys.exit(main())
