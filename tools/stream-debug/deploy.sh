#!/bin/bash
# deploy.sh [--no-install]: install the repo's artifacts/Moonlight.wgt on the TV, start it in debug mode, forward
# the inspector to 127.0.0.1:9333 and open the host's app list. Ends any running stream.
[ -f "$(dirname "$0")/.env" ] && . "$(dirname "$0")/.env"
: "${TV_IP:?set TV_IP in .env}" "${SDB:?set SDB in .env}" "${APPS2SAMSUNG_CLI:?set APPS2SAMSUNG_CLI in .env}"
WGT=$(cd "$(dirname "$0")/../.." && pwd)/artifacts/Moonlight.wgt
T=$TV_IP:26101
$SDB connect $T </dev/null >/dev/null
$SDB -s $T shell 0 kill MoonLightS </dev/null
if [ "$1" != --no-install ]; then
  (cd "$(dirname "$APPS2SAMSUNG_CLI")" && "$APPS2SAMSUNG_CLI" install "$WGT" --tv $TV_IP --partner </dev/null) || exit 1
fi
$SDB connect $T </dev/null >/dev/null; sleep 1   # the installer restarts the sdb server
out=$($SDB -s $T shell 0 debug MoonLightS.MoonlightWasm </dev/null); echo "$out"
port=$(echo "$out" | grep -oE 'port: *[0-9]+' | grep -oE '[0-9]+' | tail -1); [ -n "$port" ] || exit 1
$SDB -s $T forward --remove tcp:9333 </dev/null >/dev/null 2>&1
$SDB -s $T forward tcp:9333 tcp:$port </dev/null
for n in $(seq 1 20); do curl -s -m 2 http://127.0.0.1:9333/json | grep -q webSocketDebuggerUrl && break; sleep 1; done
sleep 4
