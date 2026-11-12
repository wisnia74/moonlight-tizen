# Stream debugging scripts

Tools for measuring Moonlight's MSE video backend on a Samsung Tizen TV while it streams. They drive the app through the Chrome DevTools inspector of a debug launch, which `deploy.sh` forwards to `127.0.0.1:9333`, and read the player's state through the functions in `wasm/platform/mse.js`. The host-side tools, including the test scene, are in the Sunshine fork under `scripts/stream-debug/`, and `harness.py` starts the test scene from there.

## Setup

Copy `.env.example` to `.env` and fill it in. Git ignores `.env`.

Two environment variables override defaults:

- `STREAM_DEBUG_OUT`: where the scripts write their output. Default `/tmp/stream-debug`.
- `SUNSHINE_TOOLS`: the Sunshine fork's `scripts/stream-debug` folder. Default: the sibling clone, `../../../Sunshine/scripts/stream-debug` from here.

Run the Python scripts with `python3 -I`. The debug launch is for measuring. For playing, install the app and launch it normally.

## Scripts

| Script | What it does |
|---|---|
| `deploy.sh` | Installs `artifacts/Moonlight.wgt`, starts it in debug mode and forwards the inspector. With `--no-install` it only restarts the app. Ends a running stream. |
| `tv.py` | `up APP` makes sure a stream of that app is playing, `down` ends it, `relaunch` restarts Moonlight in debug mode, and `status` reports what is running. |
| `insp.py`, `winsp.py` | Evaluate JavaScript in the page, or in the MSE worker. |
| `m.py` | The main measurement tool. Its recorders keep the last ten minutes of frames, playback clock, buffer and audio counters in the page. `m.py SECS` reports on the next SECS seconds and `--last` on seconds already recorded. `why` explains each pause in a saved run, `ab` compares two settings on one stream, and `pad` puts a scripted pad in place of the real one. |
| `harness.py` | Runs one longer scenario with the test scene and a scripted pad, and adds the delay from a button press to the frame that shows it. |
| `batch.sh`, `show.py` | Run several harness scenarios in a row, and print one line per run. |
| `starts.py` | Starts several streams in a row and measures the first seconds of each. |
| `restream.sh` | Ends the stream and starts it again, optionally with player options that apply only at the start. |
| `aud.py` | Prints the audio queue and playback speed once a second. It can also block the page for a moment to force a gap in the sound. |
| `targets.py` | Lists what runs in the page: its worker threads and a few page counters. |
| `trace.py` | Records a Chromium trace of every thread in the app. |
| `trace-cpu.py` | Lists the long CPU tasks per thread in a saved trace. |
| `trace-stalls.py` | Lines a saved trace up against a harness run and shows what ran during each stall. |
| `prof.py`, `prof-worker.py`, `prof-all.py` | CPU-profile the main thread, the MSE worker, or every thread at once. |
| `heap.py` | Samples which functions allocate memory in the page and in the MSE worker. |
| `an-start.py` | Reads saved runs and shows how each stream got going: each jump to the newest frame and each moment the picture stood still. |
| `an-rebuild.py` | Reads saved runs and shows how the swap to new threads at 63 s went: how long there was no player, and how many audio packets were dropped. |
| `an-key.py`, `an-refine.py` | Read saved runs and show what each keyframe cost (size, delay, buffer dip) and how long the picture took to recover after it. |
| `an-pause.py` | Reads a saved harness run and shows what led up to each pause. |
| `an-dips.py` | Reads saved runs of the dips scenario and shows, for each short drop of the game's frame rate, what the stream sent and how far the buffer fell. |
