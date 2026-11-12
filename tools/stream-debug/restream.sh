#!/bin/bash
# restream.sh ['{js opts}']: end the running stream from Moonlight's page, optionally set window.mseOpts
# (replacing what was there), and start the stream again. For start-only player options.
# APP picks the Sunshine app by name (default Steam; the harness uses APP=Desktop, an empty Sway session).
# Switching apps answers Moonlight's "quit running app" dialog with YES, which closes the old app on the
# homelab. Otherwise programs already running in the Sway session keep running.
# Needs the inspector on 127.0.0.1:9333.
APP=${APP:-Steam}
I="python3 -I $(dirname "$0")/insp.py"
$I 'try { if (typeof _mse !== "undefined" && _mse) Module.stopStream(); "stopped" } catch (e) { "stop failed: " + e }'
for n in $(seq 1 30); do [ "$($I '!!(typeof _mse !== "undefined" && _mse)')" = false ] && break; sleep 1; done
[ -n "$1" ] && $I "window.mseOpts = $1; JSON.stringify(window.mseOpts)"
sleep 3
$I 'if (typeof _mse !== "undefined" && _mse) mseStop(); 1' >/dev/null   # a stream that died leaves its player behind
for n in $(seq 1 20); do
  r=$($I '(function(){var t=Array.from(document.querySelectorAll(".game-container")).find(function(e){return /'"$APP"'/.test(e.innerText)}); if(t && t.offsetParent!==null){t.click(); return "clicked"} var h=document.querySelector("[id^=host-container-]"); if(h && h.offsetParent!==null){h.click(); return "host"} return "wait"})()')
  [ "$r" = clicked ] && break; sleep 2
done
sleep 3
$I '(function(){var q=document.querySelector("#quitAppDialog"); if(q && q.open){document.querySelector("#continueQuitApp").click(); return "quit the running app"} return ""})()'
for n in $(seq 1 40); do [ "$($I '!!(typeof _mse !== "undefined" && _mse && _mse.playing)')" = true ] && break; sleep 1; done
sleep 8
$I '_mse ? JSON.stringify({worker: !!_mse.worker, wake: _mse.opts.wake, opts: window.mseOpts||null}) : "no stream"'
