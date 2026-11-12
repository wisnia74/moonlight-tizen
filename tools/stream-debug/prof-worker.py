"""prof-worker.py SECS: CPU-profile the MSE worker on the TV for SECS; prints its self-time ranking and
what ran before each late frame (wlate >= 30 ms)."""
import importlib.util, json, os, struct, sys, time, collections
spec = importlib.util.spec_from_file_location("gl", __import__("os").path.join(__import__("os").path.dirname(__import__("os").path.abspath(__file__)), "..", "gamepad-latency.py"))
gl = importlib.util.module_from_spec(spec); spec.loader.exec_module(gl)
class I(gl.Inspector):
    events = []
    def call(self, method, params=None, session=None):
        self.next_id += 1
        msg = {"id": self.next_id, "method": method, "params": params or {}}
        if session: msg["sessionId"] = session
        data = json.dumps(msg).encode(); mask = os.urandom(4); n = len(data)
        head = bytes([0x81]) + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack(">H", n))
        self.sock.sendall(head + mask + bytes(b ^ mask[k % 4] for k, b in enumerate(data)))
        while True:
            m = self._recv()
            if m.get("id") == self.next_id: return m.get("result", m)
            self.events.append(m)
secs = float(sys.argv[1]); i = I(9333); i.sock.settimeout(120); time.sleep(2)
i.call("Target.setAutoAttach", {"autoAttach": True, "waitForDebuggerOnStart": False, "flatten": True})
time.sleep(1); i.call("Runtime.evaluate", {"expression": "1"})
workers = [m["params"] for m in i.events if m.get("method") == "Target.attachedToTarget"]
print("workers:", [(w["targetInfo"]["type"], w["targetInfo"]["url"][:60]) for w in workers])
mse = [w for w in workers if w["targetInfo"]["url"].startswith("blob:")]
sid = mse[-1]["sessionId"]
r = i.call("Runtime.evaluate", {"expression": "typeof _mseReceiveFrame + ' ' + performance.timeOrigin", "returnByValue": True}, sid); print("worker check:", r["result"].get("value"))
i.call("Profiler.enable", session=sid); i.call("Profiler.setSamplingInterval", {"interval": 500}, sid)
t_start = i.evaluate("performance.now()")
w0 = i.call("Runtime.evaluate", {"expression": "performance.now()", "returnByValue": True}, sid)["result"]["value"]
i.call("Profiler.start", session=sid); time.sleep(secs)
p = i.call("Profiler.stop", session=sid)["profile"]
ev = json.loads(i.evaluate(f"mseEvents({t_start})"))
json.dump({"profile": p, "events": ev, "t_start": t_start}, open("/tmp/prof-worker.json", "w"))
nodes = {n["id"]: n for n in p["nodes"]}; parent = {c: n["id"] for n in p["nodes"] for c in n.get("children", [])}
def name(nid):
    cf = nodes[nid]["callFrame"]; return f'{cf["functionName"] or "(anon)"}:{cf["lineNumber"]}'
def stack(nid, d=4):
    o = []
    while nid in nodes and len(o) < d: o.append(name(nid)); nid = parent.get(nid)
    return " < ".join(o)
ts = []; t = p["startTime"]
for d in p["timeDeltas"]: t += d; ts.append(t)
S = p["samples"]; tot = (p["endTime"] - p["startTime"]) / 1000
c = collections.Counter()
for k in range(len(S) - 1): c[stack(S[k])] += (ts[k+1] - ts[k]) / 1000
print(f"worker profile {tot:.0f} ms")
for n, v in c.most_common(12): print(f"  {v:8.1f} ms {100*v/tot:5.1f}%  {n}")
late = [(round(x[0] - t_start), x[2][0]) for x in ev if x[1] == 'wlate' and x[2][0] >= 30]
print("late frames >=30 ms:", late)
print("trim:", [round(x[0]-t_start) for x in ev if x[1]=='trim'], "mhb:", [(round(x[0]-t_start), x[2]) for x in ev if x[1]=='mhb'])
seen = -1e9
for X, L in late:
    if X - seen < 100: continue
    seen = X; w = collections.Counter()
    for k in range(len(S) - 1):
        ms = (ts[k] - p["startTime"]) / 1000
        if X - L - 12 <= ms <= X + 1: w[stack(S[k])] += (ts[k+1] - ts[k]) / 1000
    print(f"--- before late frame at {X} ({L} ms late): sampled {sum(w.values()):.0f} ms")
    for n, v in w.most_common(5): print(f"   {v:5.1f}  {n}")
