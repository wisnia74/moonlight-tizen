"""trace.py SECS OUT [CATEGORIES]: record a Chromium trace of Moonlight's renderer on the TV through the
inspector's Tracing domain and save the events as JSON. Prints the event count per thread and name."""
import sys, os, time, json, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness
secs = float(sys.argv[1]); out = sys.argv[2]
cats = sys.argv[3] if len(sys.argv) > 3 else "v8,devtools.timeline,disabled-by-default-v8.gc,toplevel,__metadata"
i = harness.Insp(9333)
r = i.call("Tracing.start", {"traceConfig": {"recordMode": "recordAsMuchAsPossible", "includedCategories": cats.split(",")}, "transferMode": "ReportEvents"})
print("start:", r)
t_page = i.ev("console.timeStamp('hzsync'); performance.now()")
time.sleep(secs)
i.sock.settimeout(60); i.next_id += 1
import struct
msg = json.dumps({"id": i.next_id, "method": "Tracing.end", "params": {}}).encode(); mask = os.urandom(4); n = len(msg)
i.sock.sendall(bytes([0x81, 0x80 | n]) + mask + bytes(b ^ mask[k % 4] for k, b in enumerate(msg)))
ev = []
while True:
    m = i._recv()
    if m.get("method") == "Tracing.dataCollected": ev += m["params"]["value"]
    elif m.get("method") == "Tracing.tracingComplete": break
    elif "error" in m: print(m); break
i2 = harness.Insp(9333); fr = json.loads(i2.ev(f"mseEvents({t_page})"))
json.dump({"t_page": t_page, "events": ev, "mse": fr}, open(out, "w"))
names = {(e["pid"], e["tid"]): e["args"]["name"] for e in ev if e.get("name") == "thread_name"}
c = collections.Counter((names.get((e["pid"], e["tid"]), str(e["tid"])), e["name"]) for e in ev)
print(len(ev), "events")
for (th, nm), k in c.most_common(60): print(f"{k:7d} {th:28s} {nm}")
