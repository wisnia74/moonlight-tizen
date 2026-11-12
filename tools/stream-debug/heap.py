"""heap.py SECS [OUT]: sample allocations on Moonlight's main thread and in its MSE worker for SECS seconds
(HeapProfiler sampling) and print the functions that allocate the most, as bytes per second."""
import sys, os, time, json, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness
secs = float(sys.argv[1]); i = harness.Insp(9333)
i.call("Target.setAutoAttach", {"autoAttach": True, "waitForDebuggerOnStart": False, "flatten": True})
time.sleep(0.5); i.ev("1")
ws = [m["params"] for m in i.events if m.get("method") == "Target.attachedToTarget" and m["params"]["targetInfo"]["url"].startswith("blob:")]
sess = [(None, "main")] + [(w["sessionId"], "mse-worker") for w in ws]
m0 = i.ev("performance.memory ? performance.memory.usedJSHeapSize : 0")
for sid, _ in sess:
    i.call("HeapProfiler.enable", session=sid); print(i.call("HeapProfiler.startSampling", {"samplingInterval": 8192, "includeObjectsCollectedByMajorGC": True, "includeObjectsCollectedByMinorGC": True}, sid))
time.sleep(secs)
out = {}
for sid, nm in sess:
    p = i.call("HeapProfiler.stopSampling", session=sid); out[nm] = p
    if "profile" not in p: print(nm, p); continue
    tot = collections.Counter()
    def walk(n, path):
        f = n["callFrame"]; name = f"{f['functionName'] or '(anon)'} {f['url'].rsplit('/', 1)[-1]}:{f['lineNumber']}"
        if n["selfSize"]: tot[name] += n["selfSize"]
        for c in n.get("children", []): walk(c, path + [name])
    walk(p["profile"]["head"], [])
    print(f"== {nm}: allocated {sum(tot.values())/1e3/secs:.0f} KB/s")
    for k, v in tot.most_common(14): print(f"   {v/1e3/secs:9.1f} KB/s  {k}")
print("main usedJSHeapSize before/after MB:", m0 / 1e6, i.ev("performance.memory ? performance.memory.usedJSHeapSize : 0") / 1e6)
if len(sys.argv) > 2: json.dump(out, open(sys.argv[2], "w"))
