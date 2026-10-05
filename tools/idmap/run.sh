#!/bin/sh
# Reproduce the 1.7.104 id map. One process, pinned to cores 0-7, low priority. Needs >=16 GB free, capstone, pefile, numpy.
set -e
OUT=/mnt/gaming/modlists/Projects/_research/1.7.104-idmap
HERE=$(dirname "$0")
mkdir -p "$OUT/cache"
python3 "$HERE/collect_ids.py" -o "$OUT/ids.json"
# build the per-binary function indexes once (about 3 minutes each, cached)
taskset -c 0-7 nice -n 10 python3 - <<PY
import sys; sys.path.insert(0, "$HERE")
import pe_index
B='/mnt/gaming/modlists/Projects/marth-follower-overhaul/binaries/'
for n,p in (('ae',B+'1.6.1170/SkyrimSE.unpacked.exe'),('v17',B+'1.7.104/SkyrimSE.exe')):
    pe_index.load_or_build(p,"$OUT/cache/"+n+".funcs.pkl",print)
PY
taskset -c 0-7 nice -n 10 python3 "$HERE/idmap.py" --ids "$OUT/ids.json" --out-dir "$OUT" | tee "$OUT/run.log"
