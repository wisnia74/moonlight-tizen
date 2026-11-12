"""an-start.py [SECS] FILE...: how each saved stream got going, over its first SECS seconds (default 3).
One line per file: when the playback clock first moved by itself, every seek, every stretch of 50 ms or more
in which the clock stood still, and the buffer level at fixed times. All times are ms after the first frame.
Reads --out files of starts.py and m.py."""
import json, sys

def stops(rows, t0, lim=50):
    """Stretches in which the playback clock stood still: [(start, ms)]."""
    out = []; k = 0
    while k < len(rows) - 1:
        j = k
        while j + 1 < len(rows) and rows[j + 1][3] == rows[k][3]: j += 1
        if rows[j][0] - rows[k][0] >= lim: out.append((round(rows[k][0] - t0), round(rows[j][0] - rows[k][0])))
        k = max(j, k + 1)
    return out

def first_motion(rows, t0, seeks):
    """The first sample from which the clock advances for 100 ms with no seek in it."""
    for k in range(len(rows) - 11):
        w = rows[k:k + 11]
        if all(0 < b[3] - a[3] < 40 for a, b in zip(w, w[1:])) and not any(w[0][0] <= s <= w[-1][0] for s in seeks): return round(w[0][0] - t0)
    return None

a = sys.argv[1:]
secs = 3.0
if a and a[0].replace(".", "").isdigit(): secs = float(a.pop(0))
if not a: raise SystemExit(__doc__)
for f in a:
    d = json.load(open(f))["data"]; t0 = d["frames"][0][3]; end = t0 + secs * 1000
    rows = [d["rows"][k:k+4] for k in range(0, len(d["rows"]), 4)]; rows = [r for r in rows if None not in r and r[0] <= end]
    ev = [e for e in d["events"] if e[0] <= end]
    seeks = [e[0] for e in ev if e[1] == "seek"]; moved = first_motion(rows, t0, seeks)
    def level(ms):
        near = [r for r in rows if abs(r[0] - t0 - ms) <= 30]; return round(near[len(near) // 2][2]) if near else None
    st = stops(rows, t0); late = [s for s in st if moved is not None and s[0] > moved]
    kinds = {k: sum(1 for e in ev if e[1] == k) for k in ("waiting", "playing", "idr")}
    rate = [f"+{e[0] - t0:.0f}:{e[2]}" for e in ev if e[1] == "rate"]
    print(f"{f}: play() +{rows[0][0] - t0:.0f}, clock moves from +{moved}, seeks {[round(s - t0) for s in seeks]}, waiting {kinds['waiting']}, keyframes {kinds['idr']}, "
          f"clock stops {st} ({len(late)} after it first moved), ahead at 0.5/1/1.5/2/{secs:g} s: {level(500)}/{level(1000)}/{level(1500)}/{level(2000)}/{level(secs * 1000 - 40)}, rate {rate}")
