"""The examples run and print what their docstrings say."""

import pathlib
import subprocess
import sys

EXAMPLES = pathlib.Path(__file__).parent.parent / "examples"
TNC2 = "N0CALL>APRS,WIDE1-1:>hello"
HEX = "82 a0 a4 a6 40 40 60 9c 60 86 82 98 98 60 ae 92 88 8a 62 40 63 03 f0 3e 68 65 6c 6c 6f"


def run(*args, stdin=None):
    return subprocess.run(
        [sys.executable, str(EXAMPLES / "ax25_convert.py"), *args],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_ax25_convert_both_ways():
    assert run(TNC2).stdout.strip() == HEX
    assert run(HEX).stdout.strip() == TNC2


def test_ax25_convert_matches_its_docstring():
    doc = (EXAMPLES / "ax25_convert.py").read_text()
    assert HEX in doc and TNC2 in doc


def test_ax25_convert_stdin_explain_and_errors():
    result = run("--explain", stdin=f"{TNC2}\nnot a packet\n")
    assert result.returncode == 1
    assert "path 0       ae 92 88 8a 62 40 63  WIDE1-1" in result.stdout
    assert "error: 'not a packet'" in result.stderr
