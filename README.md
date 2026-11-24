# ax253

`ax253` (pronounced Ay-Ex-twenty-five-three) encodes and decodes AX.25 frames
and the TNC2 text format used by APRS-IS. It is the bottom layer of
[kiss3](https://github.com/python-aprs/kiss3) (KISS TNCs),
[aprs3](https://github.com/python-aprs/aprs3) (APRS) and
[base-aprs-client](https://github.com/python-aprs/base-aprs-client).

```
pip install ax253
```

```python
>>> from ax253 import Frame
>>> frame = Frame.from_str("N0CALL-7>APRS,WIDE1-1*:>On the air")
>>> str(frame.source), frame.info
('N0CALL-7', b'>On the air')
>>> Frame.from_bytes(bytes(frame)) == frame  # AX.25 bytes, as a KISS TNC sends
True

```

`Frame.ui()` builds new UI frames, `Address` handles callsigns and SSIDs, and
`TNC2Decode` / `AX25BytestreamDecoder` split a byte stream into frames.
[`examples/ax25_convert.py`](./examples/ax25_convert.py) converts between
TNC2 text and AX.25 hex, which helps when reading a TNC capture.

## Connected mode

`ax253.link` runs AX.25 connections (SABM/UA, I frames, RR/RNR/REJ, T1/T3
retries) over any frame protocol, such as a KISS TNC from kiss3. Connections
are asyncio streams, like `asyncio.open_connection` and `asyncio.start_server`:

```python
import kiss
from ax253.link import LinkEndpoint

_, tnc = await kiss.create_tcp_connection("localhost", 8001)
endpoint = LinkEndpoint(tnc, "N0CALL-1")

# call another station
reader, writer = await endpoint.connect("N0CALL-2", via=["WIDE1-1"])
writer.write(b"hello\r")
print(await reader.readuntil(b"\r"))
writer.close()
await writer.wait_closed()


# or answer calls to N0CALL-1
async def echo(reader, writer):
    while line := await reader.readuntil(b"\r"):
        writer.write(line)


endpoint.serve(echo)
```

APRS UI frames still arrive through `tnc.read()`. Connections are modulo-8
(AX.25 2.0); an AX.25 2.2 station such as Dire Wolf falls back to that
automatically. The tests run against Dire Wolf when it's installed.

Documentation: <https://python-aprs.org>

## Development

```
uv sync
uv run pytest        # tests, plus the examples in docstrings and this README
uv run ruff check && uv run ruff format --check
uv run ty check
```

## Author

Masen Furer KF7HVM kf7hvm@0x26.net

### Based on work by

Greg Albrecht W2GMD oss@undef.net and contributors

* https://github.com/ampledata/kiss
* https://github.com/ampledata/aprs
* http://ampledata.org/

## Copyright

Copyright 2022 Masen Furer and Contributors

Copyright 2017 Greg Albrecht and Contributors

[Automatic Packet Reporting System (APRS)](http://www.aprs.org/) is Copyright Bob Bruninga WB4APR wb4apr@amsat.org

[`fcs.py`](./src/ax253/fcs.py) - Copyright (c) 2013 Christopher H. Casebeer. All rights reserved.

## License

Apache License, Version 2.0. See [`LICENSE`](./LICENSE) for details.