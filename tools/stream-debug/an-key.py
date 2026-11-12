"""an-key.py FILE...: what each keyframe cost, in saved m.py measurements.
One block per file: the keyframes' sizes, how late each keyframe and the frames behind it reached the player
against their host timestamps, and how far the buffer dipped around it. The same two figures at frames far
from any keyframe stand beside them as the stream's own noise. Last, the biggest frames that are no keyframes:
a larger nvenc_vbv_increase lets those grow too."""
import bisect, json, os, statistics as st, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import m

def q(v, p):
    v = sorted(v); return round(v[min(len(v) - 1, int(p * len(v)))]) if v else None
def three(v): return f"{q(v, .5)}/{q(v, .9)}/{q(v, 1)}"

for f in sys.argv[1:]:
    j = json.load(open(f)); d = j["data"]; res = j["res"]
    rows = [d["rows"][k:k+4] for k in range(0, len(d["rows"]), 4)]; rows = [r for r in rows if None not in r]
    rt = [r[0] for r in rows]; t0 = rows[0][0]
    fr = m.detrend(d["frames"])
    lat = sorted(x[3] - x[1] for x in fr); base = lat[len(lat) // 20]
    late = [x[3] - x[1] - base for x in fr]
    med = st.median(x[2] for x in fr if not x[8])
    keys = [k for k, x in enumerate(fr) if x[8] and 10 <= k < len(fr) - 10]
    far = [k for k in range(60, len(fr) - 60, 97) if all(abs(k - i) > 40 for i in keys)]

    def tail(k): return max(late[k:k + 9])
    def dip(k):
        T = fr[k][3]
        before = [r[2] for r in rows[bisect.bisect_left(rt, T - 600):bisect.bisect_left(rt, T - 100)]]
        around = [r[2] for r in rows[bisect.bisect_left(rt, T - 40):bisect.bisect_left(rt, T + 150)]]
        return (st.median(before) - min(around), min(around)) if before and around else None
    kd = [x for x in map(dip, keys) if x]; fd = [x for x in map(dip, far) if x]
    under = j.get("under", []); kt = [fr[k][3] for k in keys]
    near = sum(1 for u in under if any(-100 <= t0 + u[0] - t <= 300 for t in kt))
    big = [k for k, x in enumerate(fr) if not x[8] and x[2] >= 2 * med]
    mins = (fr[-1][3] - fr[0][3]) / 60000

    print(f"{os.path.basename(f)}  [{res['label']}]  {res['secs']:.0f} s  {res['mbps']} Mbit/s  sent {res['sentFps']} fps  floor {res['floorMs']}  ahead p50 {res['aheadP50']}")
    if not keys: print("  no keyframes in the window"); continue
    ks = [fr[k][2] / 1000 for k in keys]
    print(f"  keyframes {len(keys)} ({len(keys) / mins:.0f} a minute)  KB p10/p50/max {q(ks, .1)}/{q(ks, .5)}/{q(ks, 1)}  = {st.median(ks) * 1000 / med:.1f}x a median frame of {med / 1000:.0f} KB"
          f"  | reassembly ms p50/p90/max {three([fr[k][6] for k in keys])}")
    print(f"  late ms p50/p90/max: keyframe {three([late[k] for k in keys])}, worst of it and the 8 frames behind {three([tail(k) for k in keys])}  | far from a keyframe {three([tail(k) for k in far])}")
    print(f"  buffer dip ms p50/p90/max {three([x[0] for x in kd])}, lowest level p50/p10/min {q([x[1] for x in kd], .5)}/{q([x[1] for x in kd], .1)}/{q([x[1] for x in kd], 0)}"
          f"  | far from a keyframe: dip {three([x[0] for x in fd])}, lowest {q([x[1] for x in fd], .5)}/{q([x[1] for x in fd], .1)}/{q([x[1] for x in fd], 0)}")
    print(f"  underflow pauses {res['underflow']} ({near} within 300 ms of a keyframe)  late >=12 ms {res['late12']} (>=25: {res['late25']})  receive stalls {res['recvStallsPer30']}/30 s"
          f"  | other frames over 2x median: {len(big)}, biggest {max((fr[k][2] for k in big), default=0) / 1000:.0f} KB, late p90 {q([late[k] for k in big], .9)}")
