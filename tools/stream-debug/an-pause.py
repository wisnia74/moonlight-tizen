"""an-pause.py [--late MS] FILE...: what led up to each underflow pause (and, with --late, to each cluster of
frames late by MS or more) in a harness.py --out dump. Per event: the buffer around it, the app's frame
intervals on the host, and every frame that reached the player from 150 ms before to 60 ms after, with its
host timestamp step, size and how late it was in each stage."""
import json, sys, os, statistics as st
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import harness

a = sys.argv[1:]; late_lim = None
if a and a[0] == "--late": late_lim = float(a[1]); a = a[2:]

def base(vals):
    v = sorted(vals); return v[len(v) // 20]

for path in a:
    d = json.load(open(path)); rows = d["rows"]; fr = [f for f in d["frames"] if len(f) >= 8]; ev = d["events"]; off = d["offset"]
    bn = base(f[5] - f[1] for f in fr); bw = base(f[3] - f[5] - f[6] - f[7] for f in fr); br = base(f[6] for f in fr); bq = base(f[7] for f in fr)
    med = st.median(f[2] for f in fr)
    marks = {x[2][5] for x in ev if x[1] == "mark"}; idrs = [x[0] for x in ev if x[1] == "idr"]
    appf = d["app"]["frames"]
    under = [(rows[0][0] + p[0], p[1], p[2]) for p in harness.pauses(rows) if p[1] >= 8 and p[2] < 40]
    events = [("PAUSE", t, f"lost {lost} ms, min ahead {mn}") for t, lost, mn in under]
    if late_lim:
        _, _, rws = harness.late_stages(d["frames"], ev, d["t0"], late_lim)
        for r in rws:
            t = d["t0"] + r[0]
            if not any(abs(t - u[0]) < 200 for u in under): events.append(("LATE", t, f"worst {r[1]:.0f} ms over {r[2]} frames"))
    events.sort(key=lambda e: e[1])
    print(f"== {os.path.basename(path)}: {len(under)} underflow pauses, {len(events) - len(under)} other late clusters; median frame {med/1000:.0f} KB; baselines net {bn:.0f} reasm {br} worker {bw:.1f}")
    for kind, t, what in events:
        print(f"-- {kind} at {(t - rows[0][0])/1000:.2f} s: {what}")
        ah = [(round(r[0] - t), r[2]) for r in rows if -160 <= r[0] - t <= 80]
        print("   ahead, every 20 ms from -160:", " ".join(str(min(v for x, v in ah if lo <= x < lo + 20)) if any(lo <= x < lo + 20 for x, v in ah) else "-" for lo in range(-160, 80, 20)))
        th = t + off
        ai = [(round(b - th), round(b - a_, 1)) for a_, b in zip(appf, appf[1:]) if -200 <= b - th <= 60]
        slow = [f"{x}:{g}" for x, g in ai if g > 10.5]
        print(f"   app frames in -200..+60 ms: {len(ai)}, intervals over 10.5 ms (at:ms): {' '.join(slow) or 'none'}")
        near = [f"{x[1]}@{round(x[0] - t)}" + (f"({x[2]})" if x[1] in ('rate', 'mhb', 'wtask') else "") for x in ev if x[1] in ("idr", "idrReq", "trim", "rate", "mhb", "wtask", "gridShift", "waiting", "resume", "agap") and -200 <= x[0] - t <= 80]
        print("   events:", " ".join(near) or "none")
        prev = None
        for f in fr:
            if not (-150 <= f[3] - t <= 60): prev = f; continue
            net = f[5] - f[1] - bn; re = f[6] - br; wk = f[3] - f[5] - f[6] - f[7] - bw; tot = net + re + (f[7] - bq) + wk
            flag = ("IDR " if any(abs(f[3] - i) < 3 for i in idrs) else "") + ("MARK " if f[0] in marks else "") + ("BIG " if f[2] > 1.6 * med else "") + ("small " if f[2] < 0.4 * med else "")
            dp = f[1] - prev[1] if prev else 0; da = f[3] - prev[3] if prev else 0; dn = f[0] - prev[0] if prev else 0
            print(f"   {f[3] - t:7.1f}  fn+{dn}  pts+{dp:5.1f}  arr+{da:5.1f}  {f[2]/1000:5.1f} KB  net {net:5.1f} reasm {re:5.1f} worker {wk:5.1f}  late {tot:5.1f}  {flag}")
            prev = f
