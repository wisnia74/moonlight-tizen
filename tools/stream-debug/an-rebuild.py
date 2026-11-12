"""an-rebuild.py FILE...: how a stream's player rebuild went (the one at rebuildAt, page age 63 s).
Per file: the player events in the 3 s after the 'rebuild' event, every stretch of 50 ms or more in which the
playback clock stood still around it, the stretch with no player at all, the buffer level at fixed times after
it, and each second in which an audio counter changed. Times are ms after the 'rebuild' event, which the new
player logs when it starts, 250 ms after the old one is dropped.
Reads --out files of starts.py and m.py that were read while the stream was still running."""
import json, sys

QUIET = ("wtask", "frames", "mhb", "idr", "wlate", "agap", "clk")

if len(sys.argv) < 2: raise SystemExit(__doc__)
for f in sys.argv[1:]:
    d = json.load(open(f))["data"]; ev = d["events"]; t0 = d["frames"][0][3]
    print(f"{f}: first frame at page age {t0 / 1000:.1f} s")
    rb = [e for e in ev if e[1] == "rebuild"]
    if rb:
        r = rb[0][0]
        print(f"   rebuild event at page age {r / 1000:.2f} s; events after it:", [(e[0] - r, e[1]) for e in ev if 0 <= e[0] - r <= 3000 and e[1] not in QUIET])
        rows = [d["rows"][k:k+4] for k in range(0, len(d["rows"]), 4)]
        rows = [x for x in rows if None not in x and r - 1500 <= x[0] <= r + 4000]
        k = 0; stops = []
        while k < len(rows) - 1:
            j = k
            while j + 1 < len(rows) and rows[j + 1][3] == rows[k][3]: j += 1
            if rows[j][0] - rows[k][0] >= 50: stops.append((round(rows[k][0] - r), round(rows[j][0] - rows[k][0])))
            k = max(j, k + 1)
        print("   clock stops >=50 ms [at, ms]:", stops)
        print("   no player (gap in the samples) [at, ms]:", [(round(a[0] - r), round(b[0] - a[0])) for a, b in zip(rows, rows[1:]) if b[0] - a[0] > 100])
        def level(ms):
            near = [x for x in rows if abs(x[0] - r - ms) <= 30]; return round(near[len(near) // 2][2]) if near else None
        print("   ahead at 0.5/1/1.5/2/3 s:", [level(ms) for ms in (500, 1000, 1500, 2000, 3000)])
    else:
        print("   no rebuild event in this file")
    sec = [d["sec"][k:k+12] for k in range(0, len(d["sec"]), 12)]
    for a, b in zip(sec, sec[1:]):
        du, dr, df = b[3] - a[3], b[4] - a[4], b[5] - a[5]
        if du or dr or df: print(f"   audio in the second up to page age {b[0] / 1000:.1f} s: underruns +{du:.0f}, resyncs +{dr:.0f}, dropped on a full buffer +{df:.0f}")
