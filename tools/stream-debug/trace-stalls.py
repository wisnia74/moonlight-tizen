import json, sys, collections, random, bisect
d = json.load(open(sys.argv[1])); ev = d["events"]; H = json.load(open(sys.argv[2])); fr = H["frames"]
names = {(e["pid"], e["tid"]): e["args"]["name"] for e in ev if e.get("name") == "thread_name"}
sync = [e for e in ev if e.get("name") == "TimeStamp"]; off = sync[0]["ts"] / 1000 - d["t_page"]
sl = []; stack = collections.defaultdict(list)
for e in sorted((e for e in ev if e.get("ph") in ("X", "B", "E") and "ts" in e), key=lambda e: e["ts"]):
    k = (e["pid"], e["tid"]); th = names.get(k, "?") + "/" + str(k[1])
    if e["ph"] == "X": sl.append((e["ts"]/1000 - off, e.get("dur", 0)/1000, th, e["name"]))
    elif e["ph"] == "B": stack[k].append(e)
    elif stack[k]:
        b = stack[k].pop(); sl.append((b["ts"]/1000 - off, (e["ts"] - b["ts"])/1000, th, b["name"]))
tr0 = min(s[0] for s in sl) + 200; tr1 = max(s[0] for s in sl) - 200
print("gc:", collections.Counter((s[2], s[3]) for s in sl if "GC" in s[3] or "Scaven" in s[3]).most_common(8))
def base(v): v = sorted(v); return v[len(v)//20]
bn = base(f[5]-f[1] for f in fr)
stalls = []
for k in range(1, len(fr)):
    f, p = fr[k], fr[k-1]; net = f[5]-f[1]-bn
    if not tr0 < f[5] < tr1: continue
    if f[6] >= 10: stalls.append((f[5], f[5]+f[6], "reasm"))
    elif net >= 8 and f[5]-(p[5]+p[6]) >= 8: stalls.append((p[5]+p[6], f[5], "net"))
print("window s", (tr1-tr0)/1000, "stalls", len(stalls), collections.Counter(s[2] for s in stalls), "total stalled ms", round(sum(b-a for a, b, _ in stalls)))
# busy = leaf-ish CPU slices: exclude the blocking waits
skip = ("ThreadController active", "WorkerThread active")
busy = [s for s in sl if s[1] >= 1.5 and s[3] not in skip]
# drop slices of threads that are just blocked: mse worker's wait shows as long FunctionCall; keep but report
def overlaps(iv):
    c = collections.Counter()
    for a, b in iv:
        seen = set()
        for s in busy:
            if s[0] < b and s[0] + s[1] > a:
                key = (s[2].split("/")[0], s[3])
                if key not in seen: seen.add(key); c[key] += 1
    return c
real = overlaps([(a, b) for a, b, _ in stalls])
r = random.Random(1); rnd = collections.Counter(); R = 5
for _ in range(R):
    rnd += overlaps([(x, x + (b - a)) for a, b, _ in stalls for x in [r.uniform(tr0, tr1)]])
print(f"{'stalls':>6} {'chance':>6}  thread, slice (>=1.5 ms)")
for k, v in real.most_common(45): print(f"{v:6d} {rnd[k]/R:6.1f}  {k}")
