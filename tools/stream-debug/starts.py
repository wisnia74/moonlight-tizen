"""starts.py N [--app Desktop] [--secs 20] [--first] [--out PREFIX] [--opts '{js}'] [--js 'code']

N fresh starts of a stream of APP, each measured over its first SECS seconds from the recorders (m.py arm).
--first relaunches Moonlight before every start, so each stream is the first of its launch.
--out PREFIX saves each start's data as PREFIX-<n>.json, readable with 'm.py why'.
--opts sets the player options for these streams (window.mseOpts), and --js runs on the page before each click.
Start the test scene first (scene.sh --schedule ...) when the stream should show more than the idle desktop."""
import json, os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import m, tv
log = m.log

CLICK = """(function (app) { var t = Array.from(document.querySelectorAll('.game-container')).find(function (e) { return e.offsetParent !== null && e.innerText.trim() === app });
  if (t) { t.click(); window._tvApp = app; return 'app' } var h = document.querySelector('[id^=host-container-]'); if (h && h.offsetParent !== null) h.click(); return 'wait' })(%s)"""

def connected(pos): return any("CLIENT CONNECTED" in l for l in tv.sunshine_new(pos))

def start(app, opts=None, js=None):
    """Click APP and return once Sunshine reports the client, without asking the page whether it plays:
    an inspector evaluation during the first seconds of a stream is a load on the thing being measured."""
    tv.down(); tv.ev("window.mseOpts = %s; window._tvApp = null; 1" % (opts or "undefined"))
    if js: log("js:", tv.ev(js))
    end = time.time() + 15
    while app not in tv.state()["apps"]:
        if time.time() > end: m.die(f"{app} never appeared in the app list")
        tv.ev(CLICK % json.dumps("\0")); time.sleep(0.4)
    # A click right after the app list appears can land before its handler is ready.
    time.sleep(2.5)
    pos = os.path.getsize(tv.LOG)
    for attempt in (1, 2, 3):
        if tv.ev(CLICK % json.dumps(app)) != "app": m.die(f"{app} left the app list before the click")
        end = time.time() + 5
        while time.time() < end:
            if connected(pos): return time.time()
            time.sleep(0.1)
        log(f"click {attempt} started nothing")
    m.die("Sunshine saw no client after three clicks")

def one(n, app, secs, first, out, opts=None, js=None):
    if first: tv.relaunch()
    t = start(app, opts, js); log(f"start {n}: client connected; silent for {secs:.0f} s")
    time.sleep(secs + 2)
    d = m.read(time.time() - t + 1)
    res, under = m.analyse(d, f"start {n}{' (first stream)' if first else ''}")
    rows = [d["rows"][k:k+4] for k in range(0, len(d["rows"]), 4)]; rows = [r for r in rows if None not in r]
    res["pauseAt"] = [[round(p[0] / 1000, 1), p[1], p[2]] for p in m.pauses(rows)]
    m.show(res, under)
    print("pauses [at s, lost ms, min ahead]:", res["pauseAt"])
    for line in m.context(d, under): print(line)
    if out: json.dump({"res": res, "under": under, "data": d}, open(f"{out}-{n}.json", "w"))
    return res

def main():
    a = sys.argv[1:]
    if not a: raise SystemExit(__doc__)
    def opt(name, default=None): return a[a.index(name) + 1] if name in a else default
    app, secs, first = opt("--app", "Desktop"), float(opt("--secs", 20)), "--first" in a
    try: res = [one(n + 1, app, secs, first, opt("--out"), opt("--opts"), opt("--js")) for n in range(int(a[0]))]
    except tv.Hung as e: m.die(str(e))
    for r in res:
        au = r.get("audio") or {}
        print(f"{r['label']}: pauses {r['pauses']} (underflow {r['underflow']}, {r['underflowMs']} ms) at {[p[0] for p in r['pauseAt']]} s  late>=25 {r['late25']} (worst {r['lateWorst']})"
              f"  receive stalls {r['recvStallsPer30']}/30 s  audio underruns {au.get('underruns')}  ahead p50 {r['aheadP50']}  fps {r['sentFps']}  {r['mbps']} Mbit/s")
    print("RESULT " + json.dumps(res))

if __name__ == "__main__": main()
