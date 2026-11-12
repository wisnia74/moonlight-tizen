"""tv.py status | up [APP] [--opts '{js}'] [--js 'code'] [--fresh] | down | relaunch | shot FILE

Fail-fast control of Moonlight on the TV. Every step polls a state against a deadline of seconds and
prints a timestamped line, so a stuck step is named at once instead of a silent timeout.

  status    inspector, page, stream and visible UI text, in under 4 s
  up APP    make sure a stream of APP (default Desktop) is playing. Reuses a playing stream of the same
            app unless --fresh or --opts (start-only player options, replaces window.mseOpts) is given.
            A page that does not answer, or a stream that does not start, gets one relaunch and retry.
            --js runs on the page just before the app is clicked (forces a fresh start).
  down      end the stream
  shot FILE the page as the inspector sees it, as a JPEG (video is on a separate hardware layer)
  relaunch  restart Moonlight in debug mode (deploy.sh --no-install)
Exit code 0 = done, 2 = failed, with the reason on the last line."""
import json, os, socket, subprocess, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import harness

T0 = time.time()
LOG = os.path.expanduser("~/.config/sunshine/sunshine.log")
def log(*a): print(f"[+{time.time() - T0:5.1f}s]", *a, flush=True)
class Hung(Exception): pass

def ev(expr, timeout=3):
    """One evaluation on the page on a fresh connection; Hung if the page does not answer in time."""
    try:
        i = harness.Insp(9333); i.sock.settimeout(timeout); return i.ev(expr)
    except (socket.timeout, TimeoutError): raise Hung("the page's main thread does not answer")
    except (OSError, EOFError, IndexError) as e: raise Hung(f"inspector not reachable: {e}")

STATE = """(function () { var s = typeof _mse !== 'undefined' && _mse ? _mse : null, q = document.querySelector('#quitAppDialog');
  var vis = function (e) { return e && e.offsetParent !== null };
  var ev = document.querySelector('#wasm_module'), emss = !s && window.mseActive === false && ev && ev.readyState >= 3 && !ev.paused && ev.currentTime > 0.3;
  return JSON.stringify({ stream: !!s || !!emss, playing: !!(s && s.playing) || !!emss, player: s ? 'mse' : emss ? 'emss' : null, app: window._tvApp || null, rate: s ? s.video.playbackRate : null,
    ahead: s && s.newest !== undefined ? Math.round((s.newest - s.video.currentTime) * 1000) : null, quitDialog: !!(q && q.open), spin: ((document.querySelector('#loadingSpinnerMessage') || {}).innerText || '').trim(),
    apps: Array.from(document.querySelectorAll('.game-container')).filter(vis).map(function (e) { return e.innerText.trim() }),
    host: vis(document.querySelector('[id^=host-container-]')),
    text: Array.from(document.querySelectorAll('dialog[open], .mdl-snackbar--active, #loadingSpinner, #loadingMessage, #naclSpinnerMessage, #connection-warnings')).filter(vis).map(function (e) { return e.innerText.trim().slice(0, 160) }).filter(Boolean) }) })()"""
def state(): return json.loads(ev(STATE))

def sunshine_new(pos):
    try:
        with open(LOG, "rb") as f: f.seek(pos); return f.read().decode(errors="replace").splitlines()
    except OSError: return []

def relaunch():
    log("relaunching Moonlight in debug mode")
    r = subprocess.run(["bash", f"{HERE}/deploy.sh", "--no-install"], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    end = time.time() + 15
    while time.time() < end:
        try:
            if ev("document.readyState", 2) == "complete":
                log("page answers")
                # the stream recorders live in the page, so every launch needs them again
                r = subprocess.run([sys.executable, "-I", f"{HERE}/m.py", "arm"], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20)
                log("recorders armed" if r.returncode == 0 else f"recorders NOT armed: {r.stdout[-150:]}")
                return
        except Hung: pass
        time.sleep(0.5)
    raise SystemExit(fail(f"Moonlight did not come back after a relaunch: {r.stdout[-200:]} {r.stderr[-200:]}"))

def fail(msg):
    log("FAILED:", msg); return 2

def down():
    st = state()
    if not st["stream"]: return
    log("ending the stream"); ev("try { Module.stopStream() } catch (e) {} 1")
    end = time.time() + 8
    while time.time() < end:
        if not state()["stream"]: time.sleep(1.0 if st["player"] == "emss" else 0); return
        time.sleep(0.25)
    ev("if (typeof _mse !== 'undefined' && _mse) mseStop(); 1"); log("stream did not end by itself; player removed")

def start(app, opts, js=None):
    pos = os.path.getsize(LOG) if os.path.exists(LOG) else 0
    down()
    ev("window.mseOpts = %s; window._tvApp = null; 1" % (opts if opts else "undefined"))
    if js: log("js:", ev(js))
    click = """(function () { var t = Array.from(document.querySelectorAll('.game-container')).find(function (e) { return e.offsetParent !== null && e.innerText.trim() === %s });
      if (t) { t.click(); window._tvApp = %s; return 'app' } var h = document.querySelector('[id^=host-container-]'); if (h && h.offsetParent !== null) { h.click(); return 'host' } return 'wait' })()""" % (json.dumps(app), json.dumps(app))
    end = time.time() + 15; last = None
    while True:
        r = ev(click)
        if r != last: log("app list:", {"app": f"clicked {app}", "host": "opened the host", "wait": "waiting for the app list"}[r]); last = r
        if r == "app": break
        if time.time() > end: return f"{app} never appeared in the app list; visible: {state()}"
        time.sleep(0.4)
    end = time.time() + 25; seen = 0; quit_done = False; spin = None; clicked = time.time(); clicks = 1
    while time.time() < end:
        st = state()
        lines = sunshine_new(pos)
        for l in lines[seen:]:
            if any(k in l for k in ("Error", "Fatal", "CLIENT CONNECTED", "Executing [", "Hang detected")): log("sunshine:", l[27:150])
        seen = len(lines)
        if st["spin"] and st["spin"] != spin: spin = st["spin"]; log("moonlight:", spin)
        if st["quitDialog"] and not quit_done:
            ev("document.querySelector('#continueQuitApp').click(); 1"); quit_done = True; end += 8; log("another app was running on the host: quitting it")
        if st["playing"]: log("playing"); break
        # a click right after the app list appears can land before its handler is ready
        if not st["stream"] and not st["spin"] and not quit_done and st["apps"] and time.time() - clicked > 2.5 and clicks < 4:
            ev(click); clicked = time.time(); clicks += 1; log(f"nothing started; clicked {app} again ({clicks})")
        time.sleep(0.3)
    else:
        return f"stream did not start in time; moonlight: {st['spin'] or st['text'] or st['apps']}; sunshine since the click: {[l[27:120] for l in lines[-4:]]}"
    if st["player"] == "emss": log("Samsung's low-latency player is running (no buffer to settle)"); return None
    end = time.time() + 12; calm = 0
    while time.time() < end:
        st = state(); calm = calm + 1 if st["rate"] == 1 and st["ahead"] is not None and st["ahead"] < 200 else 0
        if calm >= 5: log(f"settled, {st['ahead']} ms buffered"); return None
        time.sleep(0.3)
    log("not settled yet (rate %s, ahead %s); going on" % (st["rate"], st["ahead"])); return None

def up(app, opts, fresh, js=None):
    for attempt in (1, 2):
        try:
            st = state()
            if st["playing"] and st["app"] == app and not fresh and not opts and not js: log(f"{app} already playing"); return 0
            err = start(app, opts, js)
        except Hung as e: err = str(e)
        if not err: return 0
        log("problem:", err)
        if attempt == 1: relaunch()
    return fail(err)

def main():
    a = sys.argv[1:]; cmd = a[0] if a else "status"
    if cmd == "status":
        try: print(json.dumps(state()))
        except Hung as e: raise SystemExit(fail(str(e)))
    elif cmd == "relaunch": relaunch()
    elif cmd == "down":
        try: down()
        except Hung as e: raise SystemExit(fail(str(e)))
    elif cmd == "up":
        opts = a[a.index("--opts") + 1] if "--opts" in a else None
        js = a[a.index("--js") + 1] if "--js" in a else None
        rest = [x for k, x in enumerate(a[1:]) if not x.startswith("--") and a[k] not in ("--opts", "--js")]
        raise SystemExit(up(rest[0] if rest else "Desktop", opts, "--fresh" in a, js))
    elif cmd == "shot":
        import base64
        i = harness.Insp(9333); i.sock.settimeout(8)
        r = i.call("Page.captureScreenshot", {"format": "jpeg", "quality": 60})
        open(a[1], "wb").write(base64.b64decode(r["data"])); print("saved", a[1])
    else: raise SystemExit(__doc__)

if __name__ == "__main__": main()
