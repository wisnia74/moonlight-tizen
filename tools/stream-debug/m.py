"""m.py SECS [--label TEXT] [--full] [--out FILE]   wait SECS, then measure them
   m.py SECS --last [--full] [--out FILE]           measure the SECS already recorded, even after the stream ended
   m.py why FILE                                    explain each pause in a saved measurement
   m.py ab SECS REPS --a JS --b JS [--label TEXT]   interleaved A/B on one stream: JS runs on the page before each window
   m.py arm | pad play|idle|off
   m.py press 'a, wait 400, down, down, a 800, lx+ 1500'   presses on the scripted pad, one after another

Fast measurement of the MSE stream, for whatever is on it (a game, the test scene, the desktop).
'arm' installs always-on recorders once per Moonlight launch; every stream that starts afterwards records
itself, and the last 10 minutes stay readable after a stream ends (until Moonlight is relaunched). The
player's worker logs every frame and posts the log to the page once a second; the page samples the playback
clock, buffer, audio and decoder counters inside the player's own 10 ms control tick, so it adds no timer.
A measurement is then just: sleep SECS, read the rings. Nothing touches the TV during the window.

'pad play' scripts a pad on the TV in place of the real one: circling sticks and three buttons being
mashed, the load of someone playing. 'pad idle' is a connected pad at rest, 'pad off' gives the real pad back.

'press' queues presses on the scripted pad (it switches a real pad to 'pad idle' first). Names: a b x y lb rb lt rt
back start ls rs up down left right guide, and lx+ lx- ly+ ly- rx+ rx- ry+ ry- for a stick held to one side
(ly- is up). A number after a name is how long it is held in ms (default 90); 'wait N' pauses. 250 ms pass between
presses. It returns when the last press has been released.

--full (and 'why') add one line per underflow pause with what led up to it: the longest gap between frames
reaching the player, how late the worst frame was against its host timestamp and in which stage, keyframes and
buffer trims nearby, long tasks on the main thread or the worker, and the buffer level before it.

Every step has a deadline of seconds and says what it waited for. Needs the inspector on 127.0.0.1:9333."""
import collections, json, os, socket, statistics as st, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import harness

T0 = time.time()
def log(*a): print(f"[+{time.time() - T0:5.1f}s]", *a, flush=True)
def die(msg): log("FAILED:", msg); raise SystemExit(2)

# Appended to the worker's source for every new stream, and evaluated in a worker that is already running:
# logs every frame and posts the log to the page once a second.
WORKER_HOOK = """(function () { try { var V = 3; if (typeof _mseReceiveFrame !== 'function') return 'other'; if (self._rec && self._rec.v === V) return 'armed';
  var orig = (self._rec && self._rec.orig) || _mseReceiveFrame, F = 9, CH = 120, R = self._rec = { v: V, orig: orig, buf: new Float64Array(CH * F), n: 0 };
  _mseReceiveFrame = function (ptr, len, idr, fn, pts, recv, re, cq) {
    var n = performance.now();
    orig(ptr, len, idr, fn, pts, recv, re, cq);
    var s = _mse; if (!s) return;
    var sh = s.diag ? s.diag.shift : 0, k = R.n * F, b = R.buf;
    b[k] = fn; b[k + 1] = pts; b[k + 2] = len; b[k + 3] = n - sh; b[k + 4] = s.waitIdr ? -1 : s.lastT / 90; b[k + 5] = recv - sh; b[k + 6] = re; b[k + 7] = cq; b[k + 8] = idr ? 1 : 0;
    if (++R.n === CH) { postMessage({ type: 'rec', buf: b.buffer }, [b.buffer]); R.buf = new Float64Array(CH * F); R.n = 0; }
  }; return 'installed'; } catch (e) { return 'failed ' + e; } })()"""

# Once per Moonlight launch: rings on window (they outlive a stream), and wrappers that put the recorders into
# every stream that starts from now on. Returns 'hook' when the stream already running still needs its worker hooked.
PAGE_ARM = """(function (HOOK) { var R = window._rec, fresh = !R;
  if (!R) {
    var N = 100 * 600, M = 600, FN = 120 * 600;
    R = window._rec = { rows: new Float64Array(N * 4), n: 0, N: N, sec: new Float64Array(M * 12), m: 0, M: M, fr: new Float64Array(FN * 9), f: 0, FN: FN, tick: 0, streams: 0, lastEvents: [], lastFloor: null, lastOpts: null };
    if (typeof _audResetStatistics === 'function') {
      window._audTot = {}; var origAud = _audResetStatistics;
      _audResetStatistics = function () { var a = _audStats, T = window._audTot; if (a) for (var k in a) if (typeof a[k] === 'number' && isFinite(a[k])) T[k] = (T[k] || 0) + a[k]; origAud(); };
    }
    R.aud = function (k) { var a = typeof _audStats !== 'undefined' && _audStats, T = window._audTot || {}; return (T[k] || 0) + (a && isFinite(a[k]) ? a[k] : 0); };
    R.onMsg = function (e) { var m = e.data; if (!m || m.type !== 'rec') return; var b = new Float64Array(m.buf), n = b.length / 9, i, j, k;
      for (i = 0; i < n; i++) { k = (R.f % R.FN) * 9; for (j = 0; j < 9; j++) R.fr[k + j] = b[i * 9 + j]; R.f++; } };
    R.attach = function (s) {
      if (!s || s._recOn) return; s._recOn = true; R.streams++; R.at = performance.now();
      if (s.worker) s.worker.addEventListener('message', R.onMsg);
      var w = 0, last = performance.now(), v = s.video;
      clearInterval(s.timer);
      s.timer = setInterval(function () {
        _mseControl();
        if (_mse !== s || !s.playing) return;
        var n = performance.now(); w += (n - last) * v.playbackRate; last = n;
        var ct = v.currentTime * 1000, k = (R.n % R.N) * 4, r = R.rows;
        r[k] = n; r[k + 1] = ct - w; r[k + 2] = s.newest * 1000 - ct; r[k + 3] = ct; R.n++;
        if (++R.tick % 100 === 0) {
          var q = v.getVideoPlaybackQuality(), a = typeof _audStats !== 'undefined' && _audStats, j = (R.m % R.M) * 12, c = R.sec, aud = R.aud;
          c[j] = n; c[j + 1] = q.totalVideoFrames; c[j + 2] = q.droppedVideoFrames; c[j + 3] = aud('underruns'); c[j + 4] = aud('resyncs'); c[j + 5] = aud('droppedFull');
          c[j + 6] = aud('droppedEmpty'); c[j + 7] = aud('decoderGaps'); c[j + 8] = aud('received'); c[j + 9] = aud('scheduled'); c[j + 10] = a && isFinite(a.headroomMin) ? a.headroomMin : -1;
          // Sunshine's capture-to-send time for the last second, from the stats overlay's text (needs the stats setting on)
          var hp = R.ov || (R.ov = document.querySelector('#performance-stats')), hm = hp && /min.max.average: ([0-9.]+).([0-9.]+).([0-9.]+) ms/.exec(hp.statsText || hp.textContent);
          c[j + 11] = hm ? +hm[2] : -1; R.m++;
        }
      }, 10);
    };
    var origSrc = _mseWorkerSource; _mseWorkerSource = function () { return origSrc() + '\\n' + HOOK + ';\\n'; };
    var origStart = mseStart; mseStart = function () { var r = origStart.apply(this, arguments); try { R.attach(_mse); if (_mse) _mse._recHooked = true; } catch (e) { console.error('[rec] ' + e); } return r; };
    var origStop = mseStop; mseStop = function () { var s = _mse; if (s) { R.lastEvents = s.events; R.lastFloor = s.floorMs; R.lastOpts = s.opts; R.endedAt = performance.now(); } return origStop.apply(this, arguments); };
  }
  var s = typeof _mse !== 'undefined' && _mse;
  if (s) R.attach(s);
  return (fresh ? 'installed' : 'armed') + (s && !s._recHooked ? ' hook' : '') + (s ? '' : ' idle'); })(__HOOK__)"""

PAGE_READ = """(function (secs) { var R = window._rec; if (!R) return JSON.stringify({ err: 'not armed since Moonlight was launched' });
  var s = typeof _mse !== 'undefined' && _mse, now = s ? performance.now() : (R.endedAt || performance.now()), since = now - secs * 1000, rows = [], sec = [], fr = [], i, k, j;
  for (i = Math.max(0, R.n - R.N); i < R.n; i++) { k = (i % R.N) * 4; if (R.rows[k] >= since) rows.push(Math.round(R.rows[k] * 10) / 10, Math.round(R.rows[k + 1] * 10) / 10, Math.round(R.rows[k + 2]), Math.round(R.rows[k + 3] * 10) / 10); }
  for (i = Math.max(0, R.m - R.M); i < R.m; i++) { k = (i % R.M) * 12; if (R.sec[k] >= since - 1000) for (j = 0; j < 12; j++) sec.push(Math.round(R.sec[k + j] * 10) / 10); }
  for (i = Math.max(0, R.f - R.FN); i < R.f; i++) { k = (i % R.FN) * 9; if (R.fr[k + 3] >= since) for (j = 0; j < 9; j++) fr.push(Math.round(R.fr[k + j] * 10) / 10); }
  var ov = document.querySelector('#performance-stats'), evs = s ? s.events : R.lastEvents;
  return JSON.stringify({ now: now, since: since, live: !!s, endedAgo: s ? 0 : Math.round((performance.now() - now) / 1000), rows: rows, sec: sec, fr: fr, events: evs.filter(function (e) { return e[0] >= since && e[1] !== 'frames'; }),
    floorMs: s ? s.floorMs : R.lastFloor, opts: s ? s.opts : R.lastOpts, overlay: ov ? getComputedStyle(ov).display : 'absent', pad: window._padMode || 'real', app: window._tvApp || null, audio: typeof _audStats !== 'undefined' && !!_audStats }); })(__ARG__)"""

PAD = """(function (mode) { var H = window._padH = window._padH || {};
  if (!H.orig) H.orig = navigator.getGamepads.bind(navigator);
  window._padMode = mode === 'off' ? 'real' : mode;
  if (mode === 'off') { navigator.getGamepads = H.orig; return 'real pad'; }
  var btn = []; for (var i = 0; i < 17; i++) btn.push({ pressed: false, touched: false, value: 0 });
  var pad = { id: 'Harness pad (STANDARD GAMEPAD Vendor: 045e Product: 028e)', index: 0, connected: true, mapping: 'standard', axes: [0, 0, 0, 0], buttons: btn, timestamp: performance.now(),
              vibrationActuator: { playEffect: function () { return Promise.resolve('complete'); }, reset: function () { return Promise.resolve(); } } };
  var list = [pad, null, null, null], t0 = performance.now(); H.pad = pad; H.keys = H.keys || [];
  navigator.getGamepads = function () {
    var n = performance.now(), r = n - t0, m = window._padMode, k = H.keys, i;
    for (i = 0; i < 17; i++) { btn[i].pressed = false; btn[i].value = 0; }
    if (m === 'play') {
      var a = r / 1000 * 2 * Math.PI * 0.7;
      pad.axes[0] = Math.cos(a) * 0.8; pad.axes[1] = Math.sin(a) * 0.8; pad.axes[2] = Math.sin(a * 0.37) * 0.6; pad.axes[3] = Math.cos(a * 0.53) * 0.6;
      if ((r % 230) < 70) { btn[2].pressed = true; btn[2].value = 1; } if ((r % 410) < 120) { btn[7].pressed = true; btn[7].value = 1; } if ((r % 1900) < 90) { btn[0].pressed = true; btn[0].value = 1; }
    } else { pad.axes[0] = pad.axes[1] = pad.axes[2] = pad.axes[3] = 0; }
    // queued presses from pad.py: [button or 'ax0+'.., from ms, until ms]
    for (i = k.length - 1; i >= 0; i--) if (n > k[i][2]) k.splice(i, 1);
    for (i = 0; i < k.length; i++) if (n >= k[i][1]) { var b = k[i][0]; if (typeof b === 'number') { btn[b].pressed = true; btn[b].value = 1; } else pad.axes[+b[2]] = b[3] === '+' ? 1 : -1; }
    pad.timestamp = n; return list;
  };
  return 'scripted pad: ' + mode; })(__ARG__)"""

def insp(timeout=6):
    try:
        i = harness.Insp(9333); i.sock.settimeout(timeout); return i
    except (OSError, EOFError, IndexError) as e: die(f"inspector not reachable: {e} (tv.py relaunch)")

def ev(i, expr):
    try: return i.ev(expr)
    except (socket.timeout, TimeoutError): die("the page's main thread does not answer (tv.py up will relaunch it)")

def worker_eval(i, expr):
    """Evaluate in the MSE worker; the other workers are native threads that never answer."""
    i.events.clear()
    i.call("Target.setAutoAttach", {"autoAttach": True, "waitForDebuggerOnStart": False, "flatten": True})
    end = time.time() + 3; ws = []
    while time.time() < end and not ws:
        i.ev("1"); ws = [m["params"] for m in i.events if m.get("method") == "Target.attachedToTarget" and m["params"]["targetInfo"]["url"].startswith("blob:")]
        if not ws: time.sleep(0.1)
    out = None
    for wk in ws:
        try: r = i.call("Runtime.evaluate", {"expression": expr, "returnByValue": True}, wk["sessionId"])
        except (socket.timeout, TimeoutError): continue
        v = r.get("result", {}).get("value")
        if v not in (None, "other"): out = v
    i.call("Target.setAutoAttach", {"autoAttach": False, "waitForDebuggerOnStart": False, "flatten": True})
    for wk in ws:
        try: i.call("Target.detachFromTarget", {"sessionId": wk["sessionId"]})
        except Exception: pass
    if out is None: die("the player's worker did not answer")
    return out

def arm(quiet=False):
    """True when a recorder had to be put into the running stream (which disturbs it; the caller's window starts after the settle)."""
    i = insp(); p = ev(i, PAGE_ARM.replace("__HOOK__", json.dumps(WORKER_HOOK)))
    if "hook" not in p:
        if p.startswith("installed"): log("recorders installed for every stream from now on" + (" (no stream is running)" if "idle" in p else ""))
        return False
    w = worker_eval(i, WORKER_HOOK)
    if str(w).startswith("failed"): die("worker recorder: " + w)
    ev(i, "_mse._recHooked = true; 1")
    log(f"recorder put into the running stream (worker {w}); settling")
    del i
    end = time.time() + 12; calm = 0
    while time.time() < end:
        time.sleep(0.4); i = insp()
        calm = calm + 1 if ev(i, "_mse && _mse.video.playbackRate === 1 && _mse.newest - _mse.video.currentTime < 0.2") else 0
        del i
        if calm >= 4: break
    time.sleep(1.5)
    return True

def pct(v, q):
    v = sorted(v); return v[min(len(v) - 1, int(q * len(v)))] if v else None

def pauses(rows, W=10):
    """A lasting drop of the playback clock against wall time (10 ms samples, medians over W of them)."""
    t = [r[0] for r in rows]; off = [r[1] for r in rows]; ah = [r[2] for r in rows]; out = []; k = W
    def drop(m): return st.median(off[m-W:m]) - st.median(off[m:m+W])
    while k < len(rows) - W:
        if drop(k) > 5:
            j = max(range(max(W, k - W), min(len(rows) - W, k + W)), key=drop)
            out.append((round(t[j] - t[0]), round(drop(j), 1), min(ah[max(0, j-5):j+2]))); k = j + 2 * W
        else: k += 1
    return out

def read(secs):
    i = insp(15); d = json.loads(ev(i, PAGE_READ.replace('__ARG__', repr(float(secs)))))
    if "err" in d: die(d["err"])
    f = d.pop("fr"); d["frames"] = sorted((f[k:k+9] for k in range(0, len(f), 9)), key=lambda x: x[3])
    if not d["live"]: log(f"the stream ended {d['endedAgo']} s ago; reading its last {secs:.0f} s")
    return last_stream(d)

def last_stream(d):
    """Keep only the newest stream in the data: a window can reach back into the stream before it."""
    rows = [d["rows"][k:k+4] for k in range(0, len(d["rows"]), 4)]
    cut = max((k for k in range(1, len(rows)) if rows[k][0] - rows[k-1][0] > 2000), default=0)
    if not cut: return d
    t = rows[cut][0]; log(f"the window spans more than one stream; keeping the newest ({(rows[-1][0] - t) / 1000:.0f} s)")
    d = dict(d); d["rows"] = [x for r in rows[cut:] for x in r]
    sec = [d["sec"][k:k+12] for k in range(0, len(d["sec"]), 12)]; d["sec"] = [x for r in sec if r[0] >= t for x in r]
    d["frames"] = [f for f in d["frames"] if f[3] >= t - 50]; d["events"] = [e for e in d["events"] if e[0] >= t - 50]
    return d

def detrend(fr, chunk=10000):
    """The frames with their host timestamps moved onto the TV's clock, one offset per CHUNK ms.
    The two clocks drift apart, and over a window of minutes the drift alone passes the thresholds for a
    late frame, so against one offset for the whole window the frames at one end all count as late."""
    if not fr: return fr
    t0 = fr[0][3]; groups = collections.defaultdict(list)
    for f in fr: groups[int((f[3] - t0) / chunk)].append(f[5] - f[1])
    base = {k: sorted(v)[len(v) // 20] for k, v in groups.items() if len(v) >= 200}
    if len(base) < 2: return fr
    ks = sorted(base); ref = base[ks[0]]; out = []
    for f in fr:
        k = int((f[3] - t0) / chunk); k = k if k in base else min(ks, key=lambda x: abs(x - k))
        g = list(f); g[1] = f[1] + base[k] - ref; out.append(g)
    return out

def analyse(d, label=""):
    rows = [d["rows"][k:k+4] for k in range(0, len(d["rows"]), 4)]; rows = [r for r in rows if None not in r]
    sec = [d["sec"][k:k+12] for k in range(0, len(d["sec"]), 12)]; fr = d["frames"]; ev_ = d["events"]
    if len(rows) < 50 or len(fr) < 20: die(f"too little data: {len(rows)} clock samples, {len(fr)} frames (stream stalled or just restarted?)")
    span = (rows[-1][0] - rows[0][0]) / 1000; kinds = collections.Counter(e[1] for e in ev_)
    P = pauses(rows); under = [p for p in P if p[1] >= 8 and p[2] < 40]; ah = [r[2] for r in rows]; sa = sorted(ah)
    fspan = (fr[-1][3] - fr[0][3]) / 1000
    res = {"label": label, "secs": round(span, 1), "app": d["app"], "pad": d["pad"], "overlay": d["overlay"],
           "underflow": len(under), "underflowMs": round(sum(p[1] for p in under)), "pauses": len(P),
           "resume": kinds.get("resume", 0), "seeks": kinds.get("seek", 0), "waiting": kinds.get("waiting", 0), "rateChanges": kinds.get("rate", 0), "mainStalls": kinds.get("mhb", 0),
           "sentFps": round((fr[-1][0] - fr[0][0]) / fspan, 1), "playerFps": round((len(fr) - 1) / fspan, 1), "mbps": round(sum(f[2] for f in fr) * 8 / fspan / 1e6, 1),
           "aheadP10": pct(ah, .1), "aheadP50": pct(ah, .5), "aheadP90": pct(ah, .9), "aheadMin": sa[0], "floorMs": d["floorMs"] and round(d["floorMs"], 1),
           "dip": [pct(ah, .5) - sa[len(sa) // 100], pct(ah, .5) - sa[0]],
           "idr": sum(1 for f in fr if f[8]), "idrReq": dict(collections.Counter(e[2] for e in ev_ if e[1] == "idrReq")),
           "mediaVsHost": round((fr[-1][4] - fr[0][4]) - (fr[-1][1] - fr[0][1]), 1)}
    fr = detrend(fr)
    cl12, st12, _ = harness.late_stages(fr, ev_, rows[0][0], 12); cl25, st25, r25 = harness.late_stages(fr, ev_, rows[0][0], 25)
    rs = harness.recv_stalls(fr)
    res.update(late12=len(cl12), late25=len(cl25), lateWorst=round(max([r[1] for r in r25], default=0)), recvStalls=len(rs), recvStallMs=round(sum(x[1] for x in rs)), recvStalls16=sum(1 for x in rs if x[1] >= 16))
    per = 30 / span
    res["recvStallsPer30"] = round(len(rs) * per); res["late12Per30"] = round(len(cl12) * per)
    hp = [x[11] for x in sec if x[11] >= 0]
    res["hostMs"] = {"p50": pct(hp, .5), "p90": pct(hp, .9), "max": max(hp), "over20": sum(1 for x in hp if x >= 20), "secs": len(hp)} if hp else None
    if len(sec) >= 2:
        a, b = sec[0], sec[-1]
        res.update(decoderDropped=round(b[2] - a[2]), audio={"underruns": round(b[3] - a[3]), "resyncs": round(b[4] - a[4]), "droppedFull": round(b[5] - a[5]), "droppedEmpty": round(b[6] - a[6]),
                   "gaps": round(b[7] - a[7]), "received": round(b[8] - a[8]), "scheduled": round(b[9] - a[9]), "headroomMin": min((s[10] for s in sec if s[10] >= 0), default=None)} if d["audio"] or b[8] - a[8] > 0 else None)
    return res, under

def context(d, under):
    """One line per underflow: what the 400 ms before it looked like."""
    rows = [d["rows"][k:k+4] for k in range(0, len(d["rows"]), 4)]; rows = [r for r in rows if None not in r]
    fr = detrend(d["frames"]); t0 = rows[0][0]; out = []; sec = [d["sec"][k:k+12] for k in range(0, len(d["sec"]), 12)]
    v = sorted(f[5] - f[1] for f in fr); bn = v[len(v) // 20]; v = sorted(f[3] - f[1] for f in fr); ba = v[len(v) // 20]
    sizes = sorted(f[2] for f in fr); med = sizes[len(sizes) // 2]
    for at, lost, low in under:
        T = t0 + at; win = [f for f in fr if T - 400 <= f[3] <= T + 60]; evs = [e for e in d["events"] if T - 400 <= e[0] <= T + 60]
        if len(win) < 3: out.append(f"  at {at/1000:6.1f}s lost {lost:.0f} ms: no frames arrived in the 400 ms before it"); continue
        gap = max(b[3] - a[3] for a, b in zip(win, win[1:])); ptsgap = max(b[1] - a[1] for a, b in zip(win, win[1:]))
        w = max(win, key=lambda f: f[3] - f[1]); late = w[3] - w[1] - ba
        parts = {"net": w[5] - w[1] - bn, "reasm": w[6], "handoff": w[7], "worker": w[3] - w[5] - w[6] - w[7]}; stage = max(parts, key=parts.get)
        big = max(win, key=lambda f: f[2]); fps = (len(win) - 1) / max(1, win[-1][3] - win[0][3]) * 1000
        ah = [r[2] for r in rows if T - 400 <= r[0] <= T - 60]
        ek = collections.Counter(e[1] for e in evs if e[1] in ("idr", "idrReq", "trim", "rm", "wtask", "mhb", "agap", "rate", "seek", "waiting", "gridShift", "stall"))
        hs = [x[11] for x in sec if T - 1500 <= x[0] <= T + 1500 and x[11] >= 0]
        long_ = [f"{e[1]}:{e[2] if not isinstance(e[2], list) else e[2][:2]}" for e in evs if e[1] in ("wtask", "mhb", "rm", "agap")][:4]
        out.append(f"  at {at/1000:6.1f}s lost {lost:.0f} ms (ahead {min(ah) if ah else '?'}-{max(ah) if ah else '?'} before, {low} at it): arrival gap {gap:.0f} ms, host gap {ptsgap:.0f} ms, "
                   f"worst frame {late:.0f} ms late in {stage} ({parts[stage]:.0f}), biggest {big[2]/med:.1f}x median{' KEYFRAME' if any(f[8] for f in win) else ''}, {fps:.0f} fps, host {max(hs) if hs else '?'} ms | {dict(ek) or ''} {long_ or ''}")
    return out

def show(res, under, full=False):
    a = res.get("audio")
    print(f"== {res['label'] or res['app']} {res['secs']} s  [pad {res['pad']}, overlay {res['overlay']}]")
    print(f"video: underflow pauses {res['underflow']} ({res['underflowMs']} ms)  resume {res['resume']}  seeks {res['seeks']}  waiting {res['waiting']}  decoder dropped {res.get('decoderDropped')}"
          f"  | fps sent {res['sentFps']} player {res['playerFps']}  {res['mbps']} Mbit/s  keyframes {res['idr']} {res['idrReq'] or ''}")
    print(f"buffer: ahead p10/p50/p90 {res['aheadP10']}/{res['aheadP50']}/{res['aheadP90']} min {res['aheadMin']}  floor {res['floorMs']}  deepest dip {res['dip'][1]}  rate changes {res['rateChanges']}"
          f"  | late >=12 ms {res['late12']} (>=25: {res['late25']}, worst {res['lateWorst']})  receive stalls {res['recvStalls']} = {res['recvStallsPer30']}/30 s ({res['recvStalls16']} >=16 ms)")
    h = res.get("hostMs")
    print(f"host: capture-to-send, worst frame of each second: p50 {h['p50']} p90 {h['p90']} max {h['max']} ms; {h['over20']} of {h['secs']} seconds had a frame over 20 ms" if h else "host: no capture-to-send times (stats overlay setting off)")
    if a: print(f"audio: underruns {a['underruns']}  resyncs {a['resyncs']}  dropped full/empty {a['droppedFull']}/{a['droppedEmpty']}  decoder gaps {a['gaps']}  packets {a['received']}  headroom min {a['headroomMin']} ms")
    else: print("audio: no counters (audio scheduler not running)")
    if full and under: print("underflows [at ms, lost ms, min ahead]:", under[:15])
    print("RESULT " + json.dumps(res))

def measure(secs, label="", full=False, quiet=False, out=None, last=False):
    if not last:
        arm(); time.sleep(secs + 0.15)
    d = read(secs); res, under = analyse(d, label)
    if not quiet: show(res, under, False)
    if full and not quiet:
        for line in context(d, under): print(line)
    if out: json.dump({"res": res, "under": under, "data": d}, open(out, "w"))
    return res

def main():
    a = sys.argv[1:]
    if not a: raise SystemExit(__doc__)
    def opt(name, default=None): return a[a.index(name) + 1] if name in a else default
    if a[0] == "arm": arm(); print("armed")
    elif a[0] == "pad":
        i = insp(); print(ev(i, PAD.replace('__ARG__', json.dumps(a[1]))))
    elif a[0] == "press":
        names = "a b x y lb rb lt rt back start ls rs up down left right guide".split(); t = 150.0; q = []
        for item in a[1].split(","):
            w = item.split()
            if not w: continue
            name = w[0].lower(); ms = float(w[1]) if len(w) > 1 else 90.0
            if name == "wait": t += ms; continue
            if name in names: key = names.index(name)
            elif len(name) == 3 and name[0] in "lr" and name[1] in "xy" and name[2] in "+-": key = "ax%d%s" % ("lr".index(name[0]) * 2 + "xy".index(name[1]), name[2])
            else: die(f"unknown button {name!r}")
            q.append([key, t, t + ms]); t += ms + 250
        i = insp()
        if ev(i, "window._padMode || 'real'") == "real": ev(i, PAD.replace('__ARG__', '"idle"')); time.sleep(1.5); log("scripted pad connected")
        ev(i, "(function (q) { var n = performance.now(), H = window._padH; q.forEach(function (k) { H.keys.push([k[0], n + k[1], n + k[2]]); }); H.keys.sort(function (x, y) { return x[1] - y[1]; }); return H.keys.length })(%s)" % json.dumps(q))
        time.sleep(t / 1000); print(f"pressed {len(q)} in {t / 1000:.1f} s")
    elif a[0] == "ab":
        secs, reps = float(a[1]), int(a[2]); arms = {"A": opt("--a"), "B": opt("--b")}; out = {"A": [], "B": []}
        arm()
        for r in range(reps):
            for name in (("A", "B") if r % 2 == 0 else ("B", "A")):
                i = insp(); ev(i, arms[name] + "; 1"); del i
                time.sleep(1.5)
                res = measure(secs, f"{opt('--label', '')} {name}{r + 1}", quiet=True); out[name].append(res)
                log(f"{name}{r + 1}: receive stalls {res['recvStallsPer30']}/30 s, late>=12 {res['late12Per30']}/30 s, underflow {res['underflow']}, ahead p50 {res['aheadP50']}, dip {res['dip'][1]}")
        for name in ("A", "B"):
            v = out[name]; tot = sum(x["secs"] for x in v)
            print(f"{name}: receive stalls {round(sum(x['recvStalls'] for x in v) * 30 / tot)}/30 s {[x['recvStallsPer30'] for x in v]}  late>=12 {round(sum(x['late12'] for x in v) * 30 / tot)}/30 s"
                  f"  late>=25 {sum(x['late25'] for x in v)}  underflow {sum(x['underflow'] for x in v)}  ahead p50 {st.median(x['aheadP50'] for x in v)}  deepest dip {max(x['dip'][1] for x in v)}   <- {arms[name][:70]}")
        print("RESULT " + json.dumps(out))
    elif a[0] == "why":
        d = last_stream(json.load(open(a[1]))["data"]); res, under = analyse(d, a[1]); show(res, under)
        for line in context(d, under): print(line)
    else:
        measure(float(a[0]), opt("--label", ""), "--full" in a, out=opt("--out"), last="--last" in a)

if __name__ == "__main__": main()
