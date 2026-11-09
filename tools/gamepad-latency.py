#!/usr/bin/env python3
"""Measures the delay from the TV's gamepad to Sunshine's virtual pad on the homelab.

Moonlight must run on the TV in debug mode with its inspector port forwarded to localhost
(sdb forward), and a stream must be running. Press buttons on the TV's pad while it records.

    gamepad-latency.py <inspector port> [seconds]
    gamepad-latency.py --reanalyse <saved json>

Each recording is saved to /tmp/gamepad-latency.json.
"""

import base64
import fcntl
import json
import os
import select
import socket
import struct
import sys
import time
import urllib.request

CLOCK_MONOTONIC = 1
EVIOCSCLOCKID = 0x400445A0
EVENT = struct.Struct("llHHi")
EV_KEY, EV_ABS = 1, 3

# Standard Gamepad API button index for each evdev key of Sunshine's Xbox 360 pad.
KEYS = {304: 0, 305: 1, 307: 2, 308: 3, 310: 4, 311: 5, 314: 8, 315: 9, 317: 10, 318: 11, 316: 16}
TRIGGERS = {2: 6, 5: 7}
HATS = {17: (12, 13), 16: (14, 15)}


class Inspector:
    def __init__(self, port):
        page = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=10))[0]
        path = page["webSocketDebuggerUrl"].split(f":{port}", 1)[1]
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=30)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(
            f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode()
        )
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.sock.recv(4096)
        if b" 101 " not in buf.split(b"\r\n", 1)[0]:
            raise SystemExit(buf.decode(errors="replace"))
        self.buf = buf.split(b"\r\n\r\n", 1)[1]
        self.next_id = 0

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise EOFError
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _recv(self):
        payload = b""
        while True:
            b0, b1 = self._read(2)
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            payload += self._read(n)
            if b0 & 0x80:
                return json.loads(payload)

    def evaluate(self, expr):
        self.next_id += 1
        data = json.dumps({"id": self.next_id, "method": "Runtime.evaluate",
                           "params": {"expression": expr, "returnByValue": True}}).encode()
        mask = os.urandom(4)
        n = len(data)
        head = bytes([0x81]) + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack(">H", n))
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))
        while True:
            msg = self._recv()
            if msg.get("id") == self.next_id:
                res = msg["result"]
                if "exceptionDetails" in res:
                    raise RuntimeError(res["exceptionDetails"])
                return res["result"].get("value")


def host_ms():
    return time.clock_gettime(CLOCK_MONOTONIC) * 1000


def sync_clock(insp, samples=200):
    best = None
    for _ in range(samples):
        t0 = host_ms()
        tv = insp.evaluate("performance.now()")
        t1 = host_ms()
        if best is None or t1 - t0 < best[0]:
            best = (t1 - t0, (t0 + t1) / 2 - tv)
    return best


def find_pad():
    block = {}
    for line in open("/proc/bus/input/devices").read().split("\n") + [""]:
        if line.startswith("I:"):
            block = {"id": line}
        elif line.startswith("N:"):
            block["name"] = line.split("=", 1)[1].strip('"')
        elif line.startswith("H:"):
            block["handlers"] = line.split("=", 1)[1].split()
        elif not line and block:
            if "Vendor=045e" in block.get("id", "") and any(h.startswith("js") for h in block.get("handlers", [])):
                ev = next(h for h in block["handlers"] if h.startswith("event"))
                return f"/dev/input/{ev}", block["name"]
            block = {}
    raise SystemExit("Sunshine's virtual Xbox 360 pad not found; is a stream running?")


def record_host(path, seconds):
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    fcntl.ioctl(fd, EVIOCSCLOCKID, struct.pack("i", CLOCK_MONOTONIC))
    edges, hat = [], {16: 0, 17: 0}
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not select.select([fd], [], [], 0.2)[0]:
            continue
        data = os.read(fd, EVENT.size * 64)
        for i in range(0, len(data), EVENT.size):
            sec, usec, etype, code, value = EVENT.unpack_from(data, i)
            ts = sec * 1000 + usec / 1000
            if etype == EV_KEY and code in KEYS:
                edges.append((ts, KEYS[code], 1 if value else 0))
            elif etype == EV_ABS and code in TRIGGERS:
                pressed = 1 if value > 0 else 0
                if not edges or (edges[-1][1], edges[-1][2]) != (TRIGGERS[code], pressed):
                    edges.append((ts, TRIGGERS[code], pressed))
            elif etype == EV_ABS and code in HATS:
                neg, pos = HATS[code]
                old = hat[code]
                if old:
                    edges.append((ts, neg if old < 0 else pos, 0))
                if value:
                    edges.append((ts, neg if value < 0 else pos, 1))
                hat[code] = value
    os.close(fd)
    return edges


def pct(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(q * len(values)))]


def main():
    if sys.argv[1] == "--reanalyse":
        saved = json.load(open(sys.argv[2]))
        analyse(saved["tv"], [tuple(h) for h in saved["host"]])
        return
    port = int(sys.argv[1])
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 30
    insp = Inspector(port)
    rtt, offset = sync_clock(insp)
    print(f"clock sync: best round trip {rtt:.2f} ms, so TV times are within ±{rtt / 2:.2f} ms")
    path, name = find_pad()
    insp.evaluate(f"window.hostOffsetMs = {offset}; padLog(true)")
    print(f"recording {name} ({path}) for {seconds:.0f} s; press buttons on the TV's pad now")
    host = record_host(path, seconds)
    tv = json.loads(insp.evaluate("padLogDump()"))["events"]
    insp.evaluate("padLog(false)")
    json.dump({"rtt": rtt, "tv": tv, "host": host}, open("/tmp/gamepad-latency.json", "w"))
    analyse(tv, host)


def match(tv, host, ref):
    used, out = set(), []
    for ev in tv:
        t, button, pressed = ev[ref], ev[3], ev[4]
        for i, (ts, b, p) in enumerate(host):
            if i not in used and b == button and p == pressed and t - 10 <= ts <= t + 300:
                used.add(i)
                out.append((ts - t, button, pressed))
                break
    return out


def analyse(tv, host):
    print(f"TV edges {len(tv)}, homelab edges {len(host)}")
    for ref, label in ((1, "TV received the input (gamepad.timestamp)"), (0, "TV logger noticed it (poll)")):
        m = match(tv, host, ref)
        d = [x[0] for x in m]
        if not d:
            continue
        print(f"from {label} to Sunshine's virtual pad, {len(d)} matched, ms: "
              f"min {min(d):.1f}  p50 {pct(d, .5):.1f}  p90 {pct(d, .9):.1f}  p99 {pct(d, .99):.1f}  max {max(d):.1f}")
        slow = sorted(m, reverse=True)[:5]
        print("  slowest (ms, button, pressed):", ", ".join(f"{x[0]:.0f}/{x[1]}/{x[2]}" for x in slow))

if __name__ == "__main__":
    main()
