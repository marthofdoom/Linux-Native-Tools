#!/usr/bin/env python3
"""se_bridge.py -- locate APMF seats that only carry 1.5.97 (SE) ids in the 1.6.1170 (AE) and 1.7.104 images, starting from the 1.5.97 binary.

Rows: spec.json rows whose construct is `kPathsSE {...}` (EquipSink.Path.*).  Steps per row:
 1. locate the function in the 1.5.97 image by its masked signature from the spec (must hit exactly once);
 2. map it SE -> AE with the idmap Mapper (source = 1.5.97 image, target = 1.6.1170 image): identical-bytes / xref / fuzzy ladder;
 3. independent constrained pairing: the AE candidates are the spec's kPathsAE functions of the SAME NAME; a pair is accepted only when
    the mnemonic-sequence similarity is the unique best by a clear margin, the size ratio is sane, referenced strings agree and (when the
    ladder produced one) it agrees with step 2.  The AE id is the reverse lookup of the AE RVA in versionlib-1-6-1170-0.bin;
 4. AE -> 1.7.104 through the full idmap result (idmap CSV) or, for an RVA not in it, a fresh ladder run.
Writes se-seats-1.7.104.csv.
"""
import sys, os, re, json, csv, collections, argparse, time, difflib
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
sys.path.insert(0, '/mnt/gaming/modlists/Projects/Linux-Native-Tools/tools/steamstub-rtti')
import idmap, addrlib, pe_index
from idmap import Side, Mapper, hx, AE_EXE, V17_EXE, AE_LIB_DEFAULT, analyze

SE_EXE = '/mnt/gaming/modlists/Projects/marth-follower-overhaul/binaries/1.5.97/SkyrimSE.unpacked.exe'
SPEC = '/mnt/gaming/modlists/Projects/ai-package-management-framework/tools/verified_addresses/spec.json'

class EmptyLib:
    map = {}

def sig_regex(sig):
    parts = []
    for b in sig.split():
        parts.append(b'.' if b == '??' else re.escape(bytes([int(b, 16)])))
    return re.compile(b''.join(parts), re.S)

def scan(side, sig):
    rx = sig_regex(sig); hits = []; pos = 0
    while len(hits) < 8:
        m = rx.search(side.tb, pos)
        if not m: break
        hits.append(side.pe.text_rva + m.start()); pos = m.start() + 1
    return hits

def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--out-dir', required=True); ap.add_argument('--idmap-csv', required=True)
    ap.add_argument('--cache', required=True); ap.add_argument('--ae-lib', default=AE_LIB_DEFAULT)
    a = ap.parse_args(); log = lambda *x: print(*x, flush=True); t0 = time.time()
    spec = json.load(open(SPEC))['rows']
    se_rows = [r for r in spec if r['seat'].startswith('EquipSink.Path.') and r['construct'].startswith('kPathsSE')]
    ae_rows = [r for r in spec if r['seat'].startswith('EquipSink.Path.') and r['construct'].startswith('kPathsAE')]
    log('SE rows %d, AE rows %d' % (len(se_rows), len(ae_rows)))
    lib = addrlib.DB(a.ae_lib); assert lib.id2rva(38048) == 0x6A35F0
    pe_index.load_or_build(SE_EXE, os.path.join(a.cache, 'se.funcs.pkl'), log)
    se = Side('se', SE_EXE, os.path.join(a.cache, 'se.funcs.pkl'))
    ae = Side('ae', AE_EXE, os.path.join(a.cache, 'ae.funcs.pkl'))
    v17 = Side('v17', V17_EXE, os.path.join(a.cache, 'v17.funcs.pkl'))
    log('indexed %.0fs' % (time.time() - t0))
    # ---- 1. SE locations by signature
    se_loc = {}
    for r in se_rows:
        h = scan(se, r['sig']['1.5.97'])
        se_loc[r['seat'] + '#' + str(r['ids']['1.5.97'])] = h
    # ---- AE candidate set (spec AE rows), with their AE RVAs
    ae_cand = []
    for r in ae_rows:
        i = r['ids']['1.6.1170']; rva = lib.id2rva(i)
        fa = ae.by_start.get(rva)
        ae_cand.append(dict(seat=r['seat'], name=r['seat'].split('.')[2], id=i, rva=rva, f=fa, sig=r['sig']['1.6.1170']))
    # ---- 2. ladder SE -> AE (anchors L1-unique between the two images)
    M = Mapper(se, ae, EmptyLib(), log)
    rt_a = se.rtti()['by_key']; rt_v = ae.rtti()['by_key']; nsl = 0
    for key, avs in rt_a.items():
        vvs = rt_v.get(key, [])
        if len(avs) == 1 and len(vvs) == 1:
            sa = se.vt_slots(avs[0]); sv = ae.vt_slots(vvs[0])
            for x, y, p in M.slot_anchor(sa, sv):
                if x not in M.Aall: M.Aall[x] = y; nsl += 1
    log('SE->AE: anchors %d, derived slot anchors %d' % (len(M.A), nsl))
    # ---- 4. AE -> 1.7 results
    csvmap = {}
    for r in csv.DictReader(open(a.idmap_csv)):
        if r['rva_1_6_1170']: csvmap.setdefault(int(r['rva_1_6_1170'], 16), r)
    out = []
    taken = collections.defaultdict(list)
    for r in se_rows:
        key = r['seat'] + '#' + str(r['ids']['1.5.97']); name = r['seat'].split('.')[2]; sid = r['ids']['1.5.97']
        hits = se_loc[key]
        row = dict(seat=r['seat'], se_id=sid, se_rva='', ae_id='', ae_rva='', v17_rva='', method_ae='', evidence='', conf='UNRESOLVED', v17_conf='', v17_method='')
        if len(hits) != 1:
            row['evidence'] = '1.5.97 signature hits %d times (need exactly 1)' % len(hits); out.append(row); continue
        srva = hits[0]; row['se_rva'] = hx(srva)
        fs, _ = M.ae_func(srva)
        lad = M.resolve_start(fs) if fs else None
        lad_t = lad['v17'] if lad and lad['v17'] and lad['conf'] != 'UNRESOLVED' and lad['cross'] != 'fail' else None
        # constrained pairing among same-name AE candidates
        sh_s = [x.split(' ')[0] for x in analyze(se.pe, fs['start'], fs.get('hend', fs['end']))['shape']]
        scored = []
        for c in ae_cand:
            if c['name'] != name or c['f'] is None: continue
            sh_a = [x.split(' ')[0] for x in analyze(ae.pe, c['f']['start'], c['f'].get('hend', c['f']['end']))['shape']]
            rt = difflib.SequenceMatcher(None, sh_s, sh_a, autojunk=False).ratio()
            sz = c['f']['size'] / max(fs['size'], 1)
            scored.append((rt, sz, c))
        scored.sort(key=lambda x: -x[0])
        pick = None; why = ''
        if scored:
            top = scored[0]; second = scored[1][0] if len(scored) > 1 else 0.0
            if top[0] >= 0.75 and 0.6 <= top[1] <= 1.7 and top[0] - second >= 0.10:
                pick = top[2]; why = 'mnemonic-sequence similarity %.2f vs runner-up %.2f among the %d same-name AE candidates, size ratio %.2f' % (top[0], second, len(scored), top[1])
            else:
                why = 'no clear pairing (best %.2f, runner-up %.2f, %d same-name AE candidates)' % (top[0], second, len(scored))
        if lad_t is not None and (pick is None or pick['rva'] == lad_t):
            # ladder result must be a known AE candidate to count as agreement; otherwise take the ladder result alone
            cand = [c for c in ae_cand if c['rva'] == lad_t]
            if pick is None and cand: pick = cand[0]; why = 'ladder only: ' + lad['method']
            elif pick is not None: why += '; AGREES with the SE->AE ladder (%s %s)' % (lad['conf'], lad['method'])
            elif not cand:
                row['evidence'] = 'ladder maps to AE %s which is not a spec AE seat; ' % hx(lad_t) + why
        elif lad_t is not None and pick is not None and pick['rva'] != lad_t:
            row['evidence'] = 'CONFLICT: ladder -> %s, similarity pairing -> %s; ' % (hx(lad_t), hx(pick['rva'])); pick = None
        if pick is None:
            row['evidence'] += why; out.append(row); continue
        c0 = csvmap.get(pick['rva']); cand_note = ' | candidate (NOT accepted): AE id %s %s -> 1.7.104 %s' % (pick['id'], hx(pick['rva']), c0['rva_1_7_104'] if c0 else '?')
        ld = M.lockstep_delta(fs['start'])
        ls = ld is not None and pick['rva'] - fs['start'] == ld
        cxv, cxp, cxd = M.crosscheck(fs, pick['f'], lenient=True, ordered=False, miss_tol=0.1, extra_fwd=1 if ls else 0)
        if ls: cxd += '; block lock-step: the two nearest 1.5.97->1.6.1170 anchored functions on each side all moved by %+d B and this pairing sits at exactly that delta' % ld
        if ls and cxv == 'na': cxv = 'pass'
        if cxv == 'fail':
            row['evidence'] += why + ' | REJECTED: crosscheck FAIL (%s)' % cxd + cand_note; out.append(row); continue
        agree = lad_t is not None
        if not agree and cxv != 'pass':
            row['evidence'] += why + ' | only a name+similarity pairing (crosscheck %s, no ladder result): not accepted' % cxv + cand_note; out.append(row); continue
        row.update(ae_id=pick['id'], ae_rva=hx(pick['rva']), method_ae=('ladder+' if agree else '') + 'constrained-similarity' + ('+crosscheck' if cxv == 'pass' else ''), evidence=row['evidence'] + why + ' | crosscheck %s: %s' % (cxv, cxd), conf='MAPPED-AE')
        taken[pick['id']].append(key)
        c1 = csvmap.get(pick['rva'])
        if c1 and c1['rva_1_7_104']:
            row.update(v17_rva=c1['rva_1_7_104'], v17_conf=c1['confidence'], v17_method=c1['method'])
        out.append(row)
    # ---- second pass: rows the binaries alone could not confirm. The spec's SE and AE seat tables are parallel (same names, same order); in the
    # pass above the binary-derived pairing equalled the index pairing for every accepted row, so for the leftovers the index partner is a
    # corroborating fact. Accepted only with ALL of: same name, mnemonic-sequence similarity >= 0.80 and the best among the same-name AE candidates,
    # the pick IS the index partner, the AE candidate is not claimed by another SE row, and the lenient crosscheck does not fail (the spec-order
    # fact and the elimination fact count as two forward points).
    idx_agree = [(i, r) for i, r in enumerate(out) if r['ae_id'] and str(ae_rows[i]['ids']['1.6.1170']) == str(r['ae_id'])]
    nacc = sum(1 for r in out if r['ae_id'])
    claimed = {str(r['ae_id']) for r in out if r['ae_id']}
    if nacc and len(idx_agree) == nacc:
        for i, r in enumerate(out):
            if r['ae_id'] or not r['se_rva']: continue
            c = [x for x in ae_cand if x['id'] == ae_rows[i]['ids']['1.6.1170']][0]
            if str(c['id']) in claimed: continue
            fs, _ = M.ae_func(int(r['se_rva'], 16))
            sh_s = [x.split(' ')[0] for x in analyze(se.pe, fs['start'], fs.get('hend', fs['end']))['shape']]
            sh_a = [x.split(' ')[0] for x in analyze(ae.pe, c['f']['start'], c['f'].get('hend', c['f']['end']))['shape']]
            rt = difflib.SequenceMatcher(None, sh_s, sh_a, autojunk=False).ratio()
            same = [x for x in ae_cand if x['name'] == c['name'] and x['f'] is not None]
            best = max(same, key=lambda x: difflib.SequenceMatcher(None, sh_s, [y.split(' ')[0] for y in analyze(ae.pe, x['f']['start'], x['f'].get('hend', x['f']['end']))['shape']], autojunk=False).ratio())
            cxv, cxp, cxd = M.crosscheck(fs, c['f'], lenient=True, ordered=False, miss_tol=0.1, extra_fwd=2)
            if rt >= 0.80 and best['id'] == c['id'] and cxv != 'fail':
                c1 = csvmap.get(c['rva'])
                r.update(ae_id=c['id'], ae_rva=hx(c['rva']), method_ae='constrained-similarity+spec-order+elimination', conf='MAPPED-AE',
                         evidence=r['evidence'] + ' | SECOND PASS: similarity %.2f is the best among the %d same-name AE seats, it is the spec-table index partner (the binary-derived pairing equalled the index pairing for all %d accepted rows) and no other SE row claims it; crosscheck %s: %s' % (rt, len(same), nacc, cxv, cxd))
                if c1 and c1['rva_1_7_104']: r.update(v17_rva=c1['rva_1_7_104'], v17_conf=c1['confidence'], v17_method=c1['method'])
                claimed.add(str(c['id'])); taken[c['id']].append(r['seat'])
    for r in out:
        if r['ae_id'] and len(taken[r['ae_id']]) > 1:
            r['evidence'] += ' | AMBIGUOUS: AE id %s also claimed by %s' % (r['ae_id'], [k for k in taken[r['ae_id']]])
            r['conf'] = 'UNRESOLVED'
    os.makedirs(a.out_dir, exist_ok=True)
    with open(os.path.join(a.out_dir, 'se-seats-1.7.104.csv'), 'w', newline='') as fh:
        w = csv.writer(fh); w.writerow(['seat', 'se_id_1_5_97', 'se_rva_1_5_97', 'ae_id_1_6_1170', 'ae_rva_1_6_1170', 'rva_1_7_104', 'confidence_ae', 'method_ae', 'confidence_1_7', 'method_1_7', 'evidence'])
        for r in out: w.writerow([r['seat'], r['se_id'], r['se_rva'], r['ae_id'], r['ae_rva'], r['v17_rva'], r['conf'], r['method_ae'], r['v17_conf'], r['v17_method'], r['evidence']])
    c = collections.Counter(r['conf'] for r in out); log(dict(c)); log('done %.0fs' % (time.time() - t0))

if __name__ == '__main__': main()
