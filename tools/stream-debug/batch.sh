#!/bin/bash
# batch.sh NAME: run harness.py once per line of stdin (each line is its arguments), one after another.
# The readable reports go to $STREAM_DEBUG_OUT/NAME.txt (default /tmp/stream-debug) and the RESULT lines, one JSON
# object per run, to NAME.jsonl beside it.
cd "$(dirname "$0")"; O=${STREAM_DEBUG_OUT:-/tmp/stream-debug}; mkdir -p "$O"; : > "$O/$1.txt"; : > "$O/$1.jsonl"
while IFS= read -r line; do
  [ -z "$line" ] && continue
  eval "timeout 300 python3 -I harness.py $line" </dev/null 2>&1 | tee -a "$O/$1.raw" | grep -v '^RESULT ' >> "$O/$1.txt"
  grep '^RESULT ' "$O/$1.raw" | tail -1 | cut -c8- >> "$O/$1.jsonl"; rm -f "$O/$1.raw"
done
echo BATCH-DONE >> "$O/$1.txt"
