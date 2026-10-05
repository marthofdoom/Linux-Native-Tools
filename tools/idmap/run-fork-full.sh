#!/bin/sh
# Full-fork run: every id literal of the MIT CommonLib fork (+ MFO/APMF own ids) mapped to 1.7.104. One process, pinned, low priority; needs >=16 GB free.
set -e
HERE=$(dirname "$0")
P=/mnt/gaming/modlists/Projects
OUT=$P/_research/1.7.104-idmap
FORK_TIP=fde0f3ae
mkdir -p "$OUT/cache" "$OUT/forksrc"
git -C $P/_commonlib/mit-3.7-fork archive $FORK_TIP | tar -x -C "$OUT/forksrc"
python3 "$HERE/collect_fork_ids.py" --fork "$OUT/forksrc" --own $P/marth-follower-overhaul/native $P/ai-package-management-framework/native \
    --merge "$OUT/ids.json" -o "$OUT/ids-fork-full.json"
taskset -c 0-7 nice -n 10 python3 "$HERE/idmap.py" --ids "$OUT/ids-fork-full.json" --out-dir "$OUT" --cache "$OUT/cache" --tag 1.7.104-fork-full
taskset -c 0-7 nice -n 10 python3 "$HERE/se_bridge.py" --out-dir "$OUT" --idmap-csv "$OUT/idmap-1.7.104-fork-full.csv" --cache "$OUT/cache"
