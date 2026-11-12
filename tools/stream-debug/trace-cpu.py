import json, sys, collections
d = json.load(open(sys.argv[1])); ev = d["events"]
names = {(e["pid"], e["tid"]): e["args"]["name"] for e in ev if e.get("name") == "thread_name"}
per = collections.defaultdict(list)
for e in ev:
    if e.get("ph") == "X" and "tdur" in e: per[(e["pid"], e["tid"])].append(e)
span = 20.0; LIM = float(sys.argv[2]) if len(sys.argv) > 2 else 5
print(f"tasks using >= {LIM} ms of CPU in one go, per thread (count/s, share of them >= 10 ms):")
for k, v in sorted(per.items(), key=lambda kv: -sum(1 for e in kv[1] if e["name"] in ("ThreadControllerImpl::RunTask", "ThreadPool_RunTask") and e["tdur"] >= LIM * 1000)):
    v.sort(key=lambda e: e["ts"])
    top = [e for e in v if e["name"] in ("ThreadControllerImpl::RunTask", "ThreadPool_RunTask") and e["tdur"] >= LIM * 1000]
    if not top: continue
    lab = collections.Counter()
    for t in top:
        kids = [c for c in v if c["ts"] >= t["ts"] and c["ts"] + c["dur"] <= t["ts"] + t["dur"] and c is not t and c["tdur"] >= t["tdur"] * 0.5 and c["name"] not in ("RunTask", "AsyncTask Run", "ThreadControllerImpl::RunTask", "ThreadPool_RunTask", "ThreadController active", "WorkerThread active")]
        a = t.get("args", {}); src = a.get("src_file", "?").rsplit("/", 1)[-1] + ":" + a.get("src_func", "?")
        nm = kids[-1]["name"] if kids else "-"
        if kids and kids[-1]["name"] == "FunctionCall": nm += " " + str((kids[-1].get("args", {}).get("data") or {}).get("functionName"))
        lab[(src, nm)] += 1
    print(f"\n{names.get(k, '?')}/{k[1]}: {len(top)} ({len(top)/span:.1f}/s), >=10 ms: {sum(1 for t in top if t['tdur'] >= 10000)}, max {max(t['tdur'] for t in top)/1000:.0f} ms")
    for key, c in lab.most_common(6): print(f"   {c:4d}  {key[0][:50]:50s} {key[1][:60]}")
