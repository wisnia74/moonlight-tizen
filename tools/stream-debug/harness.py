"""harness.py SCENARIO SECS [--pad none|taps|play] [--set '{live opts}'] [--out FILE] [--marker flat|freeze|none]
              [--every SECS] [--detail X] [--rumble HZ] [--no-app] [--overlay show|hide] [--label TEXT]

One automated run of the stream: starts testapp.py in the Sway session with the scenario's frame-rate
schedule, scripts a fake pad on the TV, and measures smoothness and input delay up to the moment the
player's clock presents a frame. Needs Moonlight in debug mode with the inspector on 127.0.0.1:9333 and
a stream running.

Scenarios: s120, s60, swing (steps between 40 and 120 every 3 s), dips (120 with short random dips to
40-70), over (122 fps), or a literal testapp.py schedule. --pad: 'taps' presses the marker button every
--every seconds, 'play' adds moving sticks and two more buttons being mashed, 'none' leaves the real pad
alone (no input delay figures).

A flat marker frame is about 1 KB and the frame after it twice the usual size, which reaches the TV about
7 ms late, so markers are sparse and pauses right after one are counted apart. --marker freeze does not
work on this stream: the encoder spends a repeated frame's bits on refining the picture, so it is no
smaller than its neighbours. --marker none keeps the pad activity and draws no markers.

Installing the worker hook over a broken or older one can stop the player; the stream then needs a new
Moonlight launch (deploy.sh --no-install), since ending it leaves the app's native side stuck.

Per press the chain is: scripted edge on the TV -> Sunshine's virtual pad (evdev time) -> the app's marker
frame swapped -> that frame reaching the player's worker (found by its size) -> currentTime reaching its
media time. TV times are moved to the homelab's clock with the inspector round-trip sync."""
import argparse, collections, importlib.util, json, os, random, statistics as st, struct, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
SUNSHINE_TOOLS = os.environ.get("SUNSHINE_TOOLS", os.path.join(HERE, "..", "..", "..", "Sunshine", "scripts", "stream-debug"))
spec = importlib.util.spec_from_file_location("gl", __import__("os").path.join(__import__("os").path.dirname(__import__("os").path.abspath(__file__)), "..", "gamepad-latency.py"))
gl = importlib.util.module_from_spec(spec); spec.loader.exec_module(gl)

class Insp(gl.Inspector):
    def __init__(self, port):
        super().__init__(port); self.events = []; self.sock.settimeout(120)
    def call(self, method, params=None, session=None):
        self.next_id += 1
        msg = {"id": self.next_id, "method": method, "params": params or {}}
        if session: msg["sessionId"] = session
        data = json.dumps(msg).encode(); mask = os.urandom(4); n = len(data)
        head = bytes([0x81]) + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack(">H", n) if n < 65536
                                else bytes([0x80 | 127]) + struct.pack(">Q", n))
        self.sock.sendall(head + mask + bytes(b ^ mask[k % 4] for k, b in enumerate(data)))
        while True:
            m = self._recv()
            if m.get("id") == self.next_id: return m.get("result", m)
            self.events.append(m)
    def ev(self, expr):
        r = self.call("Runtime.evaluate", {"expression": expr, "returnByValue": True})
        if "exceptionDetails" in r: raise RuntimeError(r["exceptionDetails"])
        return r["result"].get("value")

SCEN = {"s120": "120:10", "s60": "60:10", "over": "122:10",
        "swing": "120:3,60:3,90:3,45:3,120:3,75:3,60:3,100:3,40:3"}
def schedule(name):
    if name in SCEN: return SCEN[name]
    if name == "dips":
        r = random.Random(7); out = []
        for _ in range(40): out += [f"120:{r.uniform(1.5, 4):.2f}", f"{r.uniform(40, 70):.0f}:{r.uniform(0.1, 0.5):.2f}"]
        return ",".join(out)
    return name  # a literal schedule

# Runs in the MSE worker: reports frames far smaller than the running average (the app's marker frames)
WORKER_HOOK = """(function () { var V = 6; if (self._hk && self._hk.v === V) return 'already';
  var orig = (self._hk && self._hk.orig) || _mseReceiveFrame, h = self._hk = { v: V, orig: orig, avg: 0, buf: [] };
  _mseReceiveFrame = function (ptr, len, idr, fn, pts, recv, re, cq, blk, prc, emp) {
    var n = performance.now();
    orig(ptr, len, idr, fn, pts, recv, re, cq, blk, prc, emp);
    var s = _mse; if (!s) return;
    var sh = s.diag.shift;
    // every frame: [frame number, host pts, bytes, worker arrival, media time ms, first packet, reassembly ms,
    // hand-off ms, longest receive call ms, longest stretch between receive calls ms], times on the main thread's clock
    h.buf.push(fn, pts, len, Math.round((n - sh) * 10) / 10, s.waitIdr ? -1 : Math.round(s.lastT / 9) / 10, Math.round((recv - sh) * 10) / 10, re, cq, (blk || 0) / 10, (prc || 0) / 10, emp || 0);
    if (h.buf.length >= 1320) { postMessage({ type: 'ev', kind: 'frames', info: h.buf }); h.buf = []; }
    if (idr) return;
    if (h.avg && len < h.avg / 6) { if (!s.waitIdr) postMessage({ type: 'ev', kind: 'mark', info: [len, s.lastT / 90, n - sh, recv - sh, pts, fn] }); }
    else h.avg = h.avg ? h.avg * 0.95 + len * 0.05 : len;
  }; return 'hooked' })()"""

# Runs on the page: the scripted pad and the playback clock sampler. Both stop by themselves.
PAGE = """(function (cfg) {
  var H = window._hz = window._hz || {};
  if (H.timer) clearInterval(H.timer);
  if (!H.orig) H.orig = navigator.getGamepads.bind(navigator);
  navigator.getGamepads = H.orig;
  var t0 = performance.now() + cfg.lead, len = cfg.secs * 1000, v = _mse.video;
  H.t0 = t0; H.edges = []; H.rows = []; H.polls = 0; H.rumbles = 0; H.done = false;
  var w = 0, last = performance.now();
  H.timer = setInterval(function () {
    var n = performance.now(); w += (n - last) * v.playbackRate; last = n;
    if (n < t0) return;
    if (n > t0 + len) { clearInterval(H.timer); H.timer = 0; H.done = true; return; }
    var ct = v.currentTime * 1000;
    H.rows.push(Math.round(n * 10) / 10, Math.round((ct - w) * 10) / 10, Math.round(_mse.newest * 1000 - ct), Math.round(ct * 10) / 10);
  }, cfg.sample);
  if (cfg.pad === 'none') return t0;
  var btn = []; for (var i = 0; i < 17; i++) btn.push({ pressed: false, touched: false, value: 0 });
  var pad = { id: 'Harness pad (STANDARD GAMEPAD Vendor: 045e Product: 028e)', index: 0, connected: true, mapping: 'standard',
              axes: [0, 0, 0, 0], buttons: btn, timestamp: performance.now(),
              vibrationActuator: { playEffect: function () { H.rumbles++; return Promise.resolve('complete'); }, reset: function () { return Promise.resolve(); } } };
  var pr = cfg.presses, pi = 0, down = false, list = [pad, null, null, null];
  navigator.getGamepads = function () {
    var n = performance.now(), r = n - t0;
    if (r > len + 300) { navigator.getGamepads = H.orig; return H.orig(); }
    H.polls++;
    var live = r >= 0 && r < len, d = false;
    if (live) { while (pi < pr.length && r >= pr[pi][1]) pi++; d = pi < pr.length && r >= pr[pi][0]; }
    if (d !== down) { down = d; H.edges.push([n, d ? 1 : 0, pi]); }
    btn[cfg.button].pressed = d; btn[cfg.button].value = d ? 1 : 0;
    if (cfg.pad === 'play' && live) {
      var a = r / 1000 * 2 * Math.PI * 0.7;
      pad.axes[0] = Math.cos(a) * 0.8; pad.axes[1] = Math.sin(a) * 0.8; pad.axes[2] = Math.sin(a * 0.37) * 0.6; pad.axes[3] = Math.cos(a * 0.53) * 0.6;
      var m1 = (r % 230) < 70, m2 = (r % 410) < 120;
      btn[4].pressed = m1; btn[4].value = m1 ? 1 : 0; btn[7].pressed = m2; btn[7].value = (r % 410) < 120 ? Math.min(1, (r % 410) / 60) : 0;
    } else { pad.axes[0] = pad.axes[1] = pad.axes[2] = pad.axes[3] = 0; btn[4].pressed = btn[7].pressed = false; btn[4].value = btn[7].value = 0; }
    pad.timestamp = n;
    return list;
  };
  return t0 })"""

def pct(v, q):
    v = sorted(v); return v[min(len(v) - 1, int(q * len(v)))] if v else None
def dist(v):
    return "n/a" if not v else f"p50 {pct(v, .5):.0f}  p90 {pct(v, .9):.0f}  max {max(v):.0f}"

def sway_env():
    pid = subprocess.run(["pgrep", "-x", "sunshine"], capture_output=True, text=True).stdout.split()[0]
    env = dict(os.environ)
    for kv in open(f"/proc/{pid}/environ", "rb").read().split(b"\0"):
        k, _, v = kv.decode(errors="replace").partition("=")
        if k in ("WAYLAND_DISPLAY", "DISPLAY", "XDG_RUNTIME_DIR", "SWAYSOCK", "DBUS_SESSION_BUS_ADDRESS"): env[k] = v
    return env

def pauses(rows):
    """A lasting drop of the clock against wall time; an underflow pause lost >= 8 ms
    with under 40 ms buffered just before"""
    t = [r[0] for r in rows]; off = [r[1] for r in rows]; ah = [r[2] for r in rows]
    W = 25; out = []; k = W
    def drop(m): return st.median(off[m-W:m]) - st.median(off[m:m+W])
    while k < len(rows) - W:
        if drop(k) > 5:
            j = max(range(max(W, k - W), min(len(rows) - W, k + W)), key=drop)
            out.append((round(t[j] - t[0]), round(drop(j), 1), min(ah[max(0, j-12):j+3]))); k = j + 2 * W
        else: k += 1
    return out

def steps(rows):
    off = [r[1] for r in rows]; W = 3; back = fwd = 0; k = W
    while k < len(off) - W:
        d = st.median(off[k:k+W]) - st.median(off[k-W:k])
        if d <= -6: back += 1; k += W
        elif d >= 6: fwd += 1; k += W
        else: k += 1
    return back, fwd

def late_clusters(e):
    late = [(x[0], x[2][0]) for x in e if x[1] == "wlate" and x[2][0] >= 25]; cl = []
    for t, lag in late:
        if cl and t - cl[-1][0] < 150: cl[-1] = (cl[-1][0], max(lag, cl[-1][1]))
        else: cl.append((t, lag))
    return cl

def late_stages(fr, ev, t0, lim=12):
    """Late frames against their host timestamps, clustered (150 ms), each cluster filed under the stage that
    held its worst frame: net (first packet late), reasm, handoff (C++ queue), worker. Baseline per stage is
    its 5th percentile. Returns (clusters, {stage: count}, rows) with rows = (at ms, worst, frames, net, reasm,
    handoff, worker)."""
    fr = [f for f in fr if len(f) >= 8]
    if len(fr) < 100: return [], {}, []
    def base(vals):
        v = sorted(vals); return v[len(v) // 20]
    bn = base(f[5] - f[1] for f in fr); bw = base(f[3] - f[5] - f[6] - f[7] for f in fr); br = base(f[6] for f in fr); bq = base(f[7] for f in fr)
    late = []
    for f in fr:
        net = f[5] - f[1] - bn; re = f[6] - br; cq = f[7] - bq; wk = f[3] - f[5] - f[6] - f[7] - bw
        if net + re + cq + wk >= lim: late.append((f[3] - t0, net + re + cq + wk, net, re, cq, wk))
    cl = []
    for x in late:
        if cl and x[0] - cl[-1][-1][0] < 150: cl[-1].append(x)
        else: cl.append([x])
    stage = collections.Counter(); rows = []
    for c in cl:
        w = max(c, key=lambda x: x[1]); parts = {"net": w[2], "reasm": w[3], "handoff": w[4], "worker": w[5]}
        stage[max(parts, key=parts.get)] += 1; rows.append((c[0][0], w[1], len(c), w[2], w[3], w[4], w[5]))
    return cl, dict(stage), rows

def recv_stalls(fr, lim=8):
    """Stalls of the TV's receive path: a frame whose packets took lim ms longer than usual to be read
    (reassembly), or whose first packet was read lim ms late with nothing read for lim ms before it.
    Returns [(start, ms)], on the frame log's clock."""
    fr = [f for f in fr if len(f) >= 8]
    if len(fr) < 100: return []
    v = sorted(f[5] - f[1] for f in fr); bn = v[len(v) // 20]; v = sorted(f[6] for f in fr); br = v[len(v) // 2]; out = []
    for p, f in zip(fr, fr[1:]):
        if f[6] - br >= lim: out.append((f[5], f[6] - br))
        elif f[5] - f[1] - bn >= lim and f[5] - (p[5] + p[6]) >= lim: out.append((p[5] + p[6], f[5] - p[5] - p[6]))
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario"); ap.add_argument("secs", type=float)
    ap.add_argument("--pad", default="taps", choices=["none", "taps", "play"])
    ap.add_argument("--set"); ap.add_argument("--out")
    ap.add_argument("--marker", default="flat", choices=["flat", "freeze", "none"]); ap.add_argument("--detail", default="1.0")
    ap.add_argument("--every", type=float, default=2.0, help="mean seconds between marker presses")
    ap.add_argument("--rumble", default="0"); ap.add_argument("--button", type=int, default=0)
    ap.add_argument("--no-app", action="store_true", help="measure whatever is on the stream; no markers")
    ap.add_argument("--label", default="")
    ap.add_argument("--restream", help="first restart the stream (APP, default Desktop) with these start-only player options, e.g. '{wake:\"spin\"}'")
    ap.add_argument("--js", help="JavaScript to run on the page before the run, for one-off experiments")
    ap.add_argument("--sample", type=int, default=4, help="ms between the page's playback clock samples")
    ap.add_argument("--overlay", choices=["show", "hide"], help="show or hide Moonlight's stats overlay (default: leave it)")
    a = ap.parse_args()
    KEY = {0: 304, 1: 305, 2: 307, 3: 308, 10: 317, 11: 318}[a.button]

    if a.restream is not None:
        subprocess.run(["bash", f"{HERE}/restream.sh"] + ([a.restream] if a.restream else []), env=dict(os.environ, APP=os.environ.get("APP", "Desktop")),
                       stdout=subprocess.DEVNULL, stdin=subprocess.DEVNULL, timeout=200)
    i = Insp(9333); time.sleep(2)
    if a.js: i.ev(a.js)
    if i.ev("!!(typeof _mse !== 'undefined' && _mse && _mse.playing)") is not True: raise SystemExit("no stream playing")
    if a.set: i.ev(f"mseSetOpts({a.set})")
    if a.overlay: i.ev("document.querySelector('#performance-stats').style.display = '%s'; 1" % ("block" if a.overlay == "show" else "none"))
    rtt, offset = gl.sync_clock(i, 150)
    # hook the worker once per stream
    i.call("Target.setAutoAttach", {"autoAttach": True, "waitForDebuggerOnStart": False, "flatten": True})
    time.sleep(0.5); i.ev("1")
    ws = [m["params"] for m in i.events if m.get("method") == "Target.attachedToTarget" and m["params"]["targetInfo"]["url"].startswith("blob:")]
    hook = "no worker"
    for wk in ws:
        r = i.call("Runtime.evaluate", {"expression": "typeof _mseReceiveFrame === 'function' ? " + WORKER_HOOK + " : 'other'", "returnByValue": True}, wk["sessionId"])
        v = r.get("result", {}).get("value")
        if v in ("hooked", "already"): hook = v
    i.call("Target.setAutoAttach", {"autoAttach": False, "waitForDebuggerOnStart": False, "flatten": True})
    for wk in ws: i.call("Target.detachFromTarget", {"sessionId": wk["sessionId"]})

    app = None; applog = f"/tmp/testapp-{os.getpid()}.json"
    if not a.no_app:
        app = subprocess.Popen(["python3", "-I", f"{SUNSHINE_TOOLS}/testapp.py", "--schedule", schedule(a.scenario), "--secs", str(a.secs + 40),
                                "--log", applog, "--marker", "flat" if a.marker == "none" else a.marker, "--button", str(KEY if a.marker != "none" else 999), "--detail", a.detail, "--rumble", a.rumble],
                               env=sway_env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        time.sleep(3)
    r = random.Random(11); presses = []; t = 300.0
    while t < a.secs * 1000 - 400:
        presses.append([round(t, 1), round(t + 80, 1)]); t += r.uniform(0.75, 1.25) * a.every * 1000
    cfg = {"lead": 2500, "secs": a.secs, "pad": a.pad, "presses": presses, "button": a.button, "sample": a.sample}
    # attaching and hooking stall the player; wait until it has caught up and played at rate 1 for 3 s
    calm = 0
    for _ in range(60):
        calm = calm + 1 if i.ev("_mse.video.playbackRate === 1 && _mse.newest - _mse.video.currentTime < 0.2") else 0
        if calm >= 6: break
        time.sleep(0.5)
    t0 = i.ev(PAGE + "(" + json.dumps(cfg) + ")")
    i.ev("mseSummary(true); 1")
    del i
    time.sleep(2.5 + a.secs + 0.5)
    i = Insp(9333)
    summ = json.loads(i.ev("mseSummary(true)"))
    H = json.loads(i.ev("JSON.stringify({edges: _hz.edges, rows: _hz.rows, polls: _hz.polls, rumbles: _hz.rumbles, done: _hz.done})"))
    ev = [x for x in json.loads(i.ev(f"mseEvents({t0})")) if x[0] <= t0 + a.secs * 1000]
    opts = json.loads(i.ev("JSON.stringify(_mse.opts)"))
    overlay = i.ev("(function(){var e=document.querySelector('#performance-stats'); return e ? getComputedStyle(e).display : 'absent'})()")
    del i
    appd = None
    if app:
        app.terminate(); out = app.communicate(timeout=30)[0]
        try: appd = json.load(open(applog)); os.remove(applog)
        except OSError: print("app failed:", out[-400:])

    rows = [H["rows"][k:k+4] for k in range(0, len(H["rows"]), 4)]
    rows = [r_ for r_ in rows if None not in r_]   # no buffer reading while a stream restarts
    res = {"label": a.label, "scenario": a.scenario, "secs": a.secs, "pad": a.pad, "opts": opts, "overlay": overlay, "rtt": rtt}
    print(f"== {a.scenario} {a.secs:.0f}s pad={a.pad} {a.label}  [worker hook {hook}; clock sync ±{rtt/2:.1f} ms; stats overlay {overlay}]")
    # smoothness
    P = pauses(rows); under = [p for p in P if p[1] >= 8 and p[2] < 40]; back, fwd = steps(rows)
    ah = [r_[2] for r_ in rows]; cl = late_clusters(ev); kinds = collections.Counter(x[1] for x in ev)
    gaps = sorted(b[0] - a_[0] for a_, b in zip(rows, rows[1:]))
    hostfps = None
    if appd:
        h0 = t0 + offset; n = sum(1 for f in appd["frames"] if h0 <= f <= h0 + a.secs * 1000); hostfps = n / a.secs
    # Sunshine numbers the frames it sends, and the player logs every 120th: [pts, first packet, worker time]
    fr = [v for x in ev if x[1] == "frames" for v in x[2]]
    fr = [fr[k:k+11] for k in range(0, len(fr), 11)]      # [frame number, host pts, bytes, arrival, media ms, first packet, reassembly ms, hand-off ms]
    sentfps = None
    if len(fr) > 2:
        span = fr[-1][3] - fr[0][3]; summ["inFps"] = round((len(fr) - 1) / span * 1000, 1)
        sentfps = round((fr[-1][0] - fr[0][0]) / span * 1000, 1)
        res["mediaVsPts"] = round((fr[-1][4] - fr[0][4]) - (fr[-1][1] - fr[0][1]), 1)   # media time gained over host time, ms
        if appd:
            h0 = fr[0][3] + offset - 12
            hostfps = sum(1 for f in appd["frames"] if h0 <= f < h0 + span) / span * 1000
    res["sentFps"] = sentfps
    res.update(underflow=len(under), underflowMs=sum(p[1] for p in under), pauses=len(P), resume=kinds.get("resume", 0), seeks=summ["seeks"],
               waiting=summ["waiting"], inFps=summ["inFps"], appFps=hostfps, aheadP50=pct(ah, .5), aheadP10=pct(ah, .1), aheadMin=min(ah),
               floorMs=summ["floorMs"], catchUpPct=summ["catchUpPct"], lateClusters=len(cl), stepsBack=back, stepsFwd=fwd, mbps=summ["mbps"],
               rateChanges=kinds.get("rate", 0), mainStalls=kinds.get("mhb", 0), idr=summ["idr"], dropped=summ["dropped"])
    print(f"smooth: underflow pauses {len(under)} ({sum(p[1] for p in under):.0f} ms)  resume {res['resume']}  seeks {summ['seeks']}  waiting {summ['waiting']}"
          f"  | clock steps back/fwd {back}/{fwd}  rate changes {res['rateChanges']}  catch-up {summ['catchUpPct']}%")
    if under:
        mk = [x[2][2] - rows[0][0] for x in ev if x[1] == "mark"]
        near = sum(1 for p in under if any(-20 <= p[0] - m <= 150 for m in mk))
        res["underflowNearMarker"] = near
        print(f"   underflows [at ms, lost ms, min ahead] ({near} within 150 ms after a marker frame):", under[:12])
    print(f"fps: app {hostfps and round(hostfps, 1)}  sent {sentfps}  player {summ['inFps']}  media-vs-host time {res.get('mediaVsPts')} ms  | {summ['mbps']} Mbit/s, dropped {summ['dropped']}, idr {summ['idr']}")
    sa = sorted(ah); res["dip"] = [pct(ah, .5) - sa[len(sa) // 100], pct(ah, .5) - sa[len(sa) // 1000], pct(ah, .5) - sa[0]]
    print(f"buffer: dips below the median, p1/p0.1/deepest {res['dip'][0]}/{res['dip'][1]}/{res['dip'][2]} ms")
    print(f"buffer: ahead p10/p50/p90 {pct(ah, .1)}/{pct(ah, .5)}/{pct(ah, .9)} min {min(ah)}  floor {summ['floorMs']}  | late clusters {len(cl)} ({60*len(cl)/a.secs:.0f}/min) worst {max([c[1] for c in cl], default=0)}"
          f"  main stalls>30ms {res['mainStalls']}  sampler gap p99/max {gaps[int(len(gaps)*.99)]:.0f}/{gaps[-1]:.0f}")
    cl12, st12, _ = late_stages(fr, ev, t0, 12); cl25, st25, rows25 = late_stages(fr, ev, t0, 25)
    res.update(late12=len(cl12), late12Stages=st12, late25=len(cl25), late25Stages=st25, lateWorst=max([r_[1] for r_ in rows25], default=0))
    print(f"late frames vs host time: >=12 ms {len(cl12)} clusters ({60*len(cl12)/a.secs:.0f}/min) {st12}  | >=25 ms {len(cl25)} ({60*len(cl25)/a.secs:.0f}/min) {st25} worst {res['lateWorst']:.0f}")
    rs = recv_stalls(fr); res.update(recvStalls=len(rs), recvStallMs=round(sum(x[1] for x in rs)), recvStalls16=sum(1 for x in rs if x[1] >= 16))
    print(f"receive stalls >=8 ms: {len(rs)} ({60*len(rs)/a.secs:.0f}/min), {res['recvStallMs']} ms in all, {res['recvStalls16']} of 16 ms or more")
    res.update(appendMs=summ["appendMs"], muxMs=summ["muxMs"])
    print(f"worker: append ms p50/p90/max {summ['appendMs']['p50']}/{summ['appendMs']['p90']}/{summ['appendMs']['max']}  mux max {summ['muxMs']['max']}")
    # input delay
    if a.pad != "none" and appd and a.marker != "none":
        marks = [x[2] for x in ev if x[1] == "mark"]          # [len, media ms, arrival, first packet, pts, frame no], TV clock
        downs = [e for e in H["edges"] if e[1] == 1]
        hp = appd["presses"]                                   # [evdev ms, read ms, swap done ms, frame index], homelab clock
        ct = [(r_[0], r_[3]) for r_ in rows]
        def present(media):
            lo, hi = 0, len(ct) - 1
            if not ct or ct[hi][1] < media: return None
            while lo < hi:
                mid = (lo + hi) // 2
                if ct[mid][1] >= media: hi = mid
                else: lo = mid + 1
            if lo == 0: return ct[0][0]
            (ta, ca), (tb, cb) = ct[lo-1], ct[lo]
            return tb if cb == ca else ta + (tb - ta) * (media - ca) / (cb - ca)
        chain = []; used = set(); usedm = set()
        for e in downs:
            sched_tv = t0 + presses[e[2]][0]; sched_h = sched_tv + offset
            j = next((k for k, p in enumerate(hp) if k not in used and sched_h - 5 <= p[0] <= sched_h + 250), None)
            if j is None: chain.append(None); continue
            used.add(j); p = hp[j]
            m = next((k for k, mk in enumerate(marks) if k not in usedm and p[2] - 3 <= mk[3] + offset <= p[2] + 200), None)
            row = {"padToHost": p[0] - sched_h, "poll": e[0] - sched_tv, "hostToSwap": p[2] - p[0]}
            if m is not None:
                usedm.add(m); mk = marks[m]; pres = present(mk[1])
                row.update(swapToFirstPacket=mk[3] + offset - p[2], packetToWorker=mk[2] - mk[3], bytes=mk[0])
                if pres is not None: row.update(buffer=pres - mk[2], total=pres - sched_tv)
            chain.append(row)
        ok = [c for c in chain if c and "total" in c]
        res.update(presses=len(downs), hostPresses=len(hp), marks=len(marks), matched=len(ok), padEvents=appd["padEvents"], polls=H["polls"],
                   rumblesHost=appd["rumbles"], rumblesTv=H["rumbles"])
        print(f"input: {len(downs)} presses on the TV, {len(hp)} seen by the app, {len(marks)} marker frames at the player, {len(ok)} complete chains"
              f"  | pad polls {H['polls']} ({H['polls']/a.secs:.0f}/s), host pad events {appd['padEvents']}, rumbles host/TV {appd['rumbles']}/{H['rumbles']}")
        for key, label in (("padToHost", "TV edge -> host pad"), ("hostToSwap", "host pad -> marker swapped"), ("swapToFirstPacket", "swap -> first packet at TV"),
                           ("packetToWorker", "first packet -> player worker"), ("buffer", "player buffer wait"), ("total", "TOTAL edge -> presented")):
            v = [c[key] for c in chain if c and key in c]; res[key] = {"p50": pct(v, .5), "p90": pct(v, .9), "max": max(v, default=None)}
            print(f"   {label:30s} {dist(v)}")
        if ok: print(f"   marker frame bytes p50 {pct([c['bytes'] for c in ok], .5)}")
    if a.out:
        json.dump({"res": res, "summary": summ, "events": [x for x in ev if x[1] != "frames"], "rows": rows, "edges": H["edges"], "frames": fr, "app": appd, "t0": t0, "offset": offset, "presses": presses}, open(a.out, "w"))
    print("RESULT " + json.dumps(res))

if __name__ == "__main__": main()
