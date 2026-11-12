"""aud.py SECS [--stall MS [--at S]] [--every S]

Samples the audio scheduler once a second for SECS: how far ahead of the audio clock the schedule is
(headroom), the playback speed, and the counters (underruns, resyncs, packets dropped, decoder gaps).
--stall blocks the page's main thread for MS at second S (default 3), the way a long task or a late burst
of packets does; --every repeats it. Needs the recorders (m.py arm) and a running stream."""
import json, sys, time
import os
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import m

SAMPLE = """(function () { var R = window._rec, c = _audContext; if (!R || !c || !_audStats) return 'null';
  var h = (_audNextTime - c.currentTime) * 1000;
  // The audio clock moves in steps of tens of ms, so one reading of it is off by up to a step. A build that
  // steers the schedule keeps the last polls against the page clock: read the level from those.
  if (typeof _audClockLeads !== 'undefined' && _audLevelCount >= _audLevels.length) {
    var l = Array.from(_audLevels).sort(function (x, y) { return x - y; }), k = Array.from(_audClockLeads);
    h = (l[l.length >> 1] - (Math.min.apply(null, k) + Math.max.apply(null, k)) / 2) * 1000;
  }
  return JSON.stringify([Math.round(h), R.aud('underruns'), R.aud('resyncs'), R.aud('droppedFull'), R.aud('decoderGaps'), R.aud('received'), R.aud('scheduled'),
    typeof _audSpeed === 'undefined' ? 1 : _audSpeed, window._mlAudioTargetMs || 0]); })()"""
STALL = "(function (ms) { var t = performance.now(); while (performance.now() - t < ms); return 1; })(%d)"

def main():
    a = sys.argv[1:]
    if not a: raise SystemExit(__doc__)
    def opt(name, default=None): return a[a.index(name) + 1] if name in a else default
    secs = int(a[0]); stall = int(opt("--stall", 0)); at = int(opt("--at", 3)); every = int(opt("--every", 0))
    i = m.insp(); prev = None; first = None; head = []
    for n in range(secs + 1):
        t = time.time(); v = json.loads(m.ev(i, SAMPLE))
        if v is None: m.die("no audio scheduler is running")
        first = first or v; note = ""
        if prev: note = "  ".join(f"{k} +{v[j] - prev[j]:.0f}" for j, k in ((1, "underrun"), (2, "resync"), (3, "dropped"), (4, "gaps")) if v[j] != prev[j])
        print(f"{n:3d} s  headroom {v[0]:4d} ms  speed {v[7]:.3f}  packets +{v[5] - (prev or v)[5]:.0f}  {note}", flush=True)
        head.append(v[0]); prev = v
        if stall and n >= at and (n == at or (every and (n - at) % every == 0)):
            m.ev(i, STALL % stall); print(f"      main thread blocked for {stall} ms", flush=True)
        time.sleep(max(0, 1 - (time.time() - t)))
    print(f"target {prev[8]} ms; headroom first {head[0]}, last {head[-1]}, max {max(head)}; underruns {prev[1] - first[1]:.0f}, resyncs {prev[2] - first[2]:.0f}, dropped {prev[3] - first[3]:.0f}, gaps {prev[4] - first[4]:.0f}")

if __name__ == "__main__": main()
