"""winsp.py 'js': evaluate JavaScript in the MSE worker on the TV (insp.py does the page)."""
import importlib.util, json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness
i = harness.Insp(9333)
i.call("Target.setAutoAttach", {"autoAttach": True, "waitForDebuggerOnStart": False, "flatten": True})
time.sleep(0.5); i.ev("1")
ws = [m["params"] for m in i.events if m.get("method") == "Target.attachedToTarget" and m["params"]["targetInfo"]["url"].startswith("blob:")]
for wk in ws:
    r = i.call("Runtime.evaluate", {"expression": sys.argv[1], "returnByValue": True}, wk["sessionId"])
    print(json.dumps(r.get("result", r).get("value", r))[:1500])
    i.call("Target.detachFromTarget", {"sessionId": wk["sessionId"]})
