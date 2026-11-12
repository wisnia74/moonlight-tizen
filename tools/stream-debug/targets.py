"""targets.py: what runs in Moonlight's page on the TV: its worker threads and a few page-level counters."""
import sys, os, time, json, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness
i = harness.Insp(9333)
i.call("Target.setAutoAttach", {"autoAttach": True, "waitForDebuggerOnStart": False, "flatten": True})
time.sleep(1); i.ev("1")
ws = [m["params"] for m in i.events if m.get("method") == "Target.attachedToTarget"]
print("workers:", dict(collections.Counter((w["targetInfo"]["type"], w["targetInfo"]["url"].rsplit("/", 1)[-1][:40]) for w in ws)))
# Native threads block inside wasm and never answer an evaluate, so the workers are only counted
print(i.ev("""JSON.stringify({ videos: document.querySelectorAll('video').length, audioCtx: typeof audioCtx, hostsPolling: typeof activePolls !== 'undefined' ? Object.keys(activePolls).length : null,
  running: typeof PThread !== 'undefined' ? PThread.runningWorkers.length : null, unused: typeof PThread !== 'undefined' ? PThread.unusedWorkers.length : null,
  mem: performance.memory ? Math.round(performance.memory.usedJSHeapSize / 1e6) : null, anim: document.getAnimations ? document.getAnimations().length : null })"""))
