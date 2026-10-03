"""Tiny incremental JSON array reader (stdlib only): yields items of the first top-level array
without loading the whole file. Works for `[...]` and `{"key": [...]}`."""
from __future__ import annotations
import codecs
import json

_DEC = json.JSONDecoder()
_WS = " \t\r\n,"


def iter_array(fileobj, chunk=1 << 20):
    dec = codecs.getincrementaldecoder("utf-8")(errors="replace")
    buf = ""
    pos = 0
    started = False
    eof = False
    while True:
        if not started:
            i = buf.find("[", pos)
            if i >= 0:
                started, pos = True, i + 1
                continue
        else:
            while pos < len(buf) and buf[pos] in _WS:
                pos += 1
            if pos < len(buf):
                if buf[pos] == "]":
                    return
                try:
                    obj, end = _DEC.raw_decode(buf, pos)
                except json.JSONDecodeError:
                    if eof:
                        raise
                else:
                    yield obj
                    pos = end
                    continue
        if eof:
            return
        data = fileobj.read(chunk)
        if not data:
            eof = True
            buf += dec.decode(b"", final=True)
            continue
        buf = buf[pos:] + dec.decode(data) if started else buf[max(pos, len(buf) - 1):] + dec.decode(data)
        pos = 0
