"""prof.py SECS: CPU-profile Moonlight's main thread on the TV for SECS, then show what ran inside
each main-thread stall (mhb event) and the overall self-time ranking."""
import importlib.util, json, os, struct, sys, time, collections
spec = importlib.util.spec_from_file_location("gl", __import__("os").path.join(__import__("os").path.dirname(__import__("os").path.abspath(__file__)), "..", "gamepad-latency.py"))
gl = importlib.util.module_from_spec(spec); spec.loader.exec_module(gl)
class I(gl.Inspector):
    def call(self, method, params=None):
        self.next_id += 1
        data = json.dumps({"id": self.next_id, "method": method, "params": params or {}}).encode()
        mask = os.urandom(4); n = len(data)
        head = bytes([0x81]) + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack(">H", n))
        self.sock.sendall(head + mask + bytes(b ^ mask[k % 4] for k, b in enumerate(data)))
        while True:
            m = self._recv()
            if m.get("id") == self.next_id: return m.get("result", m)
secs = float(sys.argv[1]); i = I(9333); i.sock.settimeout(120); time.sleep(2)
i.call("Profiler.enable"); i.call("Profiler.setSamplingInterval", {"interval": 500})
t_start = i.evaluate("performance.now()")
i.call("Profiler.start"); time.sleep(secs)
p = i.call("Profiler.stop")["profile"]
ev = json.loads(i.evaluate(f"mseEvents({t_start})"))
nodes = {n["id"]: n for n in p["nodes"]}
parent = {c: n["id"] for n in p["nodes"] for c in n.get("children", [])}
def name(nid):
    cf = nodes[nid]["callFrame"]; return f'{cf["functionName"] or "(anon)"}@{cf["url"].split("/")[-1]}:{cf["lineNumber"]}'
def stack(nid, depth=4):
    out = []
    while nid in nodes and len(out) < depth: out.append(name(nid)); nid = parent.get(nid)
    return " < ".join(out)
ts = []; t = p["startTime"]
for d in p["timeDeltas"]: t += d; ts.append(t)
samples = p["samples"]
self_t = collections.Counter()
for k in range(len(samples) - 1): self_t[name(samples[k])] += ts[k+1] - ts[k]
tot = (p["endTime"] - p["startTime"]) / 1000
print(f"profile {tot:.0f} ms, {len(samples)} samples"); 
for n, v in self_t.most_common(14): print(f"  {v/1000:8.1f} ms {100*v/1000/tot:5.1f}%  {n}")
# sampling gaps: stretches with no sample at all mean the thread itself was not running JS and not sampled
gaps = sorted(((ts[k+1]-ts[k])/1000, k) for k in range(len(ts)-1))[-12:]
print("longest single samples (ms, what was on the stack):")
for g, k in reversed(gaps): print(f"  {g:6.1f}  at {(ts[k]-p['startTime'])/1000:8.0f}  {stack(samples[k])}")
print("mhb events (ms into profile, gap):", [(round(x[0]-t_start), x[2]) for x in ev if x[1]=='mhb'])
print("trim:", [round(x[0]-t_start) for x in ev if x[1]=='trim'], "idr:", [round(x[0]-t_start) for x in ev if x[1]=='idr'])
json.dump(p, open("/tmp/prof.json", "w"))
