"""an-dips.py FILE...: per dip of the app's frame rate in harness.py --out dumps of the dips scenario (the
schedule is seeded, so every run plays the same dips): the app's rate, what the stream sent meanwhile, how
low the buffer went and how many of the runs paused there."""
import json, sys, statistics as st, collections
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import harness
LAT = 12      # ms from the app's swap to the frame's first packet at the TV, roughly
per = collections.defaultdict(list)
for path in sys.argv[1:]:
    d = json.load(open(path)); rows = d["rows"]; fr = [f for f in d["frames"] if len(f) >= 8]; off = d["offset"]; appf = d["app"]["frames"]
    under = [rows[0][0] + p[0] + off for p in harness.pauses(rows) if p[1] >= 8 and p[2] < 40]      # homelab clock
    t_first = rows[0][0] + off; t_last = rows[-1][0] + off
    segs = []; k = 1
    while k < len(appf):
        if appf[k] - appf[k-1] > 10.5:
            j = k
            while j + 1 < len(appf) and appf[j+1] - appf[j] > 10.5: j += 1
            segs.append((appf[k-1], appf[j], st.median(appf[m] - appf[m-1] for m in range(k, j + 1)))); k = j + 1
        else: k += 1
    n = 0
    for a, b, iv in segs:
        if b - a < 60 or a < t_first + 500 or b > t_last - 100: continue
        n += 1
        sent = [f for f in fr if a + LAT <= f[5] + off <= b + LAT]
        gaps = [y[5] - x[5] for x, y in zip(sent, sent[1:])]
        ah = [r[2] for r in rows if a + LAT <= r[0] + off <= b + LAT + 120]
        ah_before = [r[2] for r in rows if a - 300 <= r[0] + off <= a]
        p = sum(1 for u in under if a - 20 <= u <= b + 200)
        per[round((a - d["app"]["start"]) / 100) / 10].append((1000 / iv, b - a, 1000 * len(gaps) / max(1, sum(gaps)) if gaps else 0, max(gaps) if gaps else 0, min(ah) if ah else None, st.median(ah_before) if ah_before else None, p))
    hit = sum(1 for u in under if any(a - 20 <= u <= b + 200 for a, b, _ in segs))
    print(f"{path.split('/')[-1]}: {len(under)} underflow pauses, {hit} of them in or within 200 ms after a dip; {n} dips")
print("dip at s | app fps | lasts ms | sent fps (median of runs) | longest gap between sent frames ms | buffer before -> lowest in the dip (medians) | runs that paused")
for at in sorted(per):
    v = per[at]
    print(f"  {at:5.1f}   {st.median(x[0] for x in v):5.1f}   {st.median(x[1] for x in v):5.0f}   {st.median(x[2] for x in v):6.1f}   {st.median(x[3] for x in v):5.1f}   {st.median(x[5] for x in v if x[5] is not None):4.0f} -> {st.median(x[4] for x in v if x[4] is not None):4.0f}   {sum(x[6] for x in v)}/{len(v)}")
