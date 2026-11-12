"""prof-all.py SECS: CPU-profile Moonlight's main thread and every worker on the TV at once.
Prints, per thread, the functions with the most self time. Saves all profiles to /tmp/prof-all.json."""
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
secs = float(sys.argv[1]); i = I(9333); i.sock.settimeout(300); time.sleep(2)
i.call("Target.setAutoAttach", {"autoAttach": True, "waitForDebuggerOnStart": False, "flatten": True})
time.sleep(1); i.call("Runtime.evaluate", {"expression": "1"})
ws = [m["params"] for m in i.events if m.get("method") == "Target.attachedToTarget"]
sess = [(None, "main")] + [(w["sessionId"], ("mse-worker" if w["targetInfo"]["url"].startswith("blob:") else "pthread") + "#" + str(k)) for k, w in enumerate(ws)]
for sid, _ in sess:
    i.call("Profiler.enable", session=sid); i.call("Profiler.setSamplingInterval", {"interval": 1000}, sid)
t_start = i.evaluate("performance.now()")
for sid, _ in sess: i.call("Profiler.start", session=sid)
time.sleep(secs)
out = {"t_start": t_start, "threads": {}}
for sid, nm in sess: out["threads"][nm] = i.call("Profiler.stop", session=sid)["profile"]
out["events"] = json.loads(i.evaluate(f"mseEvents({t_start})"))
json.dump(out, open("/tmp/prof-all.json", "w"))
for nm, p in out["threads"].items():
    nodes = {n["id"]: n for n in p["nodes"]}
    ts = []; t = p["startTime"]
    for d in p["timeDeltas"]: t += d; ts.append(t)
    S = p["samples"]; tot = (p["endTime"] - p["startTime"]) / 1000; c = collections.Counter()
    for k in range(len(S) - 1): c[nodes[S[k]]["callFrame"]["functionName"] or "(anon)"] += (ts[k+1] - ts[k]) / 1000
    top = c.most_common(6)
    if not S or (top and top[0][1] / tot > 0.985): 
        print(f"{nm:14} ~all in {top[0][0] if top else '-'}"); continue
    print(f"{nm:14} " + "; ".join(f"{n[:38]} {100*v/tot:.1f}%" for n, v in top))
