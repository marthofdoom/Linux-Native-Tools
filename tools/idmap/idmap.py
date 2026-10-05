#!/usr/bin/env python3
"""idmap.py -- map Address Library ids used by MFO/APMF from 1.6.1170 (AE library) to 1.7.104 RVAs by disassembly.

No 1.7.104 Address Library is used or needed.  Method ladder per id (first that holds wins):
  EXACT       RTTI/vtable/TypeDescriptor matched by exact mangled class name (+ COL this-offset) in the 1.7.104 image
  UNIQUE-SIG  function body with rel32 branch targets and rip-relative displacements wildcarded hits exactly once in the
              1.7.104 .text (and once in the 1.6.1170 .text); shape / prefix signatures only with a passing crosscheck
  XREF        call-graph / vtable-slot / string / global-reference anchoring from already mapped neighbours
  UNRESOLVED  anything weaker.  Never guessed.
Every mapping gets a CROSSCHECK (callees, callers, strings, vtable slots mapped consistently) -> pass/fail/na.
A candidate whose crosscheck FAILS is rejected (UNRESOLVED, candidate noted in evidence).

Usage: idmap.py --ids ids.json --ae-lib <versionlib-1-6-1170-0.bin> --out-dir DIR [--truth ADDRESS-TABLE.md]
"""
import sys, os, re, json, csv, pickle, struct, bisect, collections, argparse, time
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, '/mnt/gaming/modlists/Projects/Linux-Native-Tools/tools/steamstub-rtti')
from pe_index import PE, analyze, BASE, h8
import addrlib
import symname

BIN = '/mnt/gaming/modlists/Projects/marth-follower-overhaul/binaries/'
AE_EXE = BIN + '1.6.1170/SkyrimSE.unpacked.exe'
V17_EXE = BIN + '1.7.104/SkyrimSE.exe'
AE_LIB_DEFAULT = '/mnt/gaming/modlists/Tuxborn/mods/Address Library for SKSE Plugins/SKSE/Plugins/versionlib-1-6-1170-0.bin'

def hx(v): return '0x%X' % v if v is not None else ''
_anon = re.compile(rb'\?A0x[0-9a-f]{8}')
def norm_anon(b): return _anon.sub(b'?A0x', b)

# ------------------------------------------------------------------ side (one binary)
class Side:
    def __init__(self, name, exe, cache):
        self.name = name
        self.pe = PE(exe)
        self.funcs = pickle.load(open(cache, 'rb'))
        self.by_start = {f['start']: f for f in self.funcs}
        self.starts = sorted(self.by_start)
        self.ends = [self.by_start[s]['end'] for s in self.starts]
        self.l1 = collections.defaultdict(list)
        self.l2 = collections.defaultdict(list)
        self.pre = collections.defaultdict(list)
        self.callers = collections.defaultdict(list)    # target -> [(func_start, call_off)]
        self.refs_in = collections.defaultdict(list)    # target -> [(func_start, off)]
        for f in self.funcs:
            self.l1[f['l1']].append(f['start'])
            self.l2[f['l2']].append(f['start'])
            if f['npre'] >= 10: self.pre[f['pre']].append(f['start'])
            for off, t, mn in f['calls']: self.callers[t].append((f['start'], off))
            for off, t in f['refs']: self.refs_in[t].append((f['start'], off))
        self.tb = self.pe.tbytes
        self._rtti = None
        self._strs = {}
        self._strmap = None
        self._e9 = None
        self.rdata = [s for s in self.pe.secs if s[0] == '.rdata'][0]

    def in_text(self, rva): return self.pe.text_rva <= rva < self.pe.tend

    def func_containing(self, rva):
        i = bisect.bisect_right(self.starts, rva) - 1
        if i < 0: return None
        s = self.starts[i]
        if rva < self.ends[i]: return self.by_start[s]
        return None

    # ---- raw masked-pattern scan over .text
    def count_pattern(self, raw, mask, limit=3):
        parts = []; i = 0; n = len(raw)
        while i < n:
            if mask[i]:
                j = i
                while j < n and mask[j]: j += 1
                parts.append(re.escape(raw[i:j])); i = j
            else:
                j = i
                while j < n and not mask[j]: j += 1
                parts.append(b'.{%d}' % (j - i)); i = j
        rx = re.compile(b''.join(parts), re.S)
        hits = []; pos = 0
        while len(hits) < limit:
            m = rx.search(self.tb, pos)
            if not m: break
            hits.append(self.pe.text_rva + m.start()); pos = m.start() + 1
        return hits

    # ---- strings referenced by a function
    def cstr_at(self, tgt):
        if tgt in self._strs: return self._strs[tgt]
        r = None
        n, va, vs, ro, rs = self.rdata
        if va <= tgt < va + rs:
            b = self.pe.cstr(tgt, 160)
            if len(b) >= 5 and all(0x20 <= c < 0x7f for c in b): r = bytes(b)
        self._strs[tgt] = r
        return r

    def strings_of(self, f):
        out = set()
        for off, t in f['refs']:
            s = self.cstr_at(t)
            if s: out.add(s)
        return out

    def strmap(self):
        if self._strmap is None:
            m = collections.defaultdict(set)
            for f in self.funcs:
                for off, t in f['refs']:
                    s = self.cstr_at(t)
                    if s: m[s].add(f['start'])
            self._strmap = m
        return self._strmap

    # ---- RTTI index
    def rtti(self):
        if self._rtti: return self._rtti
        pe = self.pe; d = pe.d
        td_by_name = collections.defaultdict(list); td_name = {}
        for n, va, vs, ro, rs in pe.secs:
            if n not in ('.data', '.rdata'): continue
            sec = d[ro:ro + rs]
            for m in re.finditer(rb'\.\?A[VUWT][\x21-\x7e]+\x00', sec):
                tdoff = m.start() - 0x10
                if tdoff < 0: continue
                if struct.unpack_from('<Q', sec, tdoff + 8)[0] != 0: continue
                rva = va + tdoff; nm = m.group(0)[:-1]
                td_by_name[nm].append(rva); td_name[rva] = nm
        n, va, vs, ro, rs = self.rdata
        a32 = np.frombuffer(d[ro:ro + (rs // 4) * 4], dtype='<u4')
        cand = np.flatnonzero(a32[:-5] == 1)
        cand = cand[a32[cand + 5] == (va + 4 * cand).astype(np.uint32)]
        tdset = set(td_name)
        cols = {}
        for i in cand:
            td = int(a32[i + 3])
            if td in tdset:
                cols[va + 4 * int(i)] = (td_name[td], int(a32[i + 1]), int(a32[i + 2]), td)
        a64 = np.frombuffer(d[ro:ro + (rs // 8) * 8], dtype='<u8')
        colva = np.array([BASE + c for c in cols], dtype=np.uint64)
        vt_by_col = collections.defaultdict(list)
        if len(colva):
            idx = np.flatnonzero(np.isin(a64, colva))
            for j in idx:
                vt_by_col[int(a64[j]) - BASE].append(va + 8 * int(j) + 8)
        by_key = collections.defaultdict(list)   # (class name, base-subobject name at this-offset) -> [vtable rva]
        col_key = {}
        for c, (nm, off, cd, td) in cols.items():
            bn = self._base_at(c, off, td_name)
            col_key[c] = (nm, bn if bn is not None else b'?off%d' % off)
            for v in vt_by_col.get(c, []): by_key[col_key[c]].append(v)
        # anonymous-namespace classes carry a per-build hash (?A0x74e2f8f3): the same class has a different hash in each image.
        td_by_name_n = collections.defaultdict(list); by_key_n = collections.defaultdict(list)
        for nm, l in td_by_name.items(): td_by_name_n[norm_anon(nm)].extend(l)
        for (nm, bn), l in by_key.items(): by_key_n[(norm_anon(nm), norm_anon(bn))].extend(l)
        self._rtti = dict(td_by_name=td_by_name, td_name=td_name, cols=cols, vt_by_col=vt_by_col, by_key=by_key, col_key=col_key,
                          td_by_name_n=td_by_name_n, by_key_n=by_key_n)
        return self._rtti

    def _base_at(self, col, off, td_name):
        """name of the first base-class descriptor whose subobject displacement == off (non-virtual)"""
        try:
            pe = self.pe
            chd = pe.u32(col + 16); n = pe.u32(chd + 8); arr = pe.u32(chd + 12)
            for i in range(min(n, 128)):
                bcd = pe.u32(arr + 4 * i)
                td = pe.u32(bcd); mdisp, pdisp, vdisp = struct.unpack('<iii', pe.read(bcd + 8, 12))
                if mdisp == off and pdisp == -1:
                    return td_name.get(td)
        except Exception:
            pass
        return None

    def vt_slots(self, vt, maxn=600):
        out = []
        for i in range(maxn):
            try: p = self.pe.u64(vt + 8 * i)
            except Exception: break
            r = p - BASE
            if not self.in_text(r): break
            out.append(r)
        return out

    # ---- import thunks: jmp qword ptr [rip+IAT]
    def iat_names(self):
        if getattr(self, '_iat', None) is None:
            import pefile
            pe = pefile.PE(self.pe.path, fast_load=True)
            pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_IMPORT']])
            m = {}
            for ent in getattr(pe, 'DIRECTORY_ENTRY_IMPORT', []):
                dll = ent.dll.decode().lower()
                for imp in ent.imports:
                    nm = imp.name.decode() if imp.name else 'ord%d' % imp.ordinal
                    m[imp.address - BASE] = (dll, nm)
            self._iat = m
            self._ff25 = None
        return self._iat

    def ff25_stubs(self):
        """IAT slot rva -> [text rva of FF25 stubs that sit in a contiguous stub run]"""
        self.iat_names()
        if self._ff25 is None:
            a = np.frombuffer(self.tb, dtype=np.uint8)
            idx = np.flatnonzero((a[:-6] == 0xFF) & (a[1:-5] == 0x25))
            rel = (a[idx + 2].astype(np.int64) | (a[idx + 3].astype(np.int64) << 8) | (a[idx + 4].astype(np.int64) << 16) | (a[idx + 5].astype(np.int64) << 24))
            rel = np.where(rel >= 1 << 31, rel - (1 << 32), rel)
            tgt = self.pe.text_rva + idx + 6 + rel
            isset = set(int(x) for x in idx)
            m = collections.defaultdict(list)
            for k in range(len(idx)):
                t = int(tgt[k])
                if t in self._iat:
                    p = int(idx[k])
                    if (p - 6) in isset or (p + 6) in isset: m[t].append(self.pe.text_rva + p)
            self._ff25 = m
        return self._ff25

    # ---- E9 stubs (5-byte jmp thunks), 16-aligned
    def e9_stubs(self):
        if self._e9 is None:
            a = np.frombuffer(self.tb, dtype=np.uint8)
            idx = np.flatnonzero(a[:-5] == 0xE9)
            idx = idx[(idx % 16) == 0] if False else idx
            rel = (a[idx + 1].astype(np.int64) | (a[idx + 2].astype(np.int64) << 8) | (a[idx + 3].astype(np.int64) << 16) |
                   (a[idx + 4].astype(np.int64) << 24))
            rel = np.where(rel >= 1 << 31, rel - (1 << 32), rel)
            tgt = self.pe.text_rva + idx + 5 + rel
            m = collections.defaultdict(list)
            sel = np.flatnonzero((idx % 16) == 0)
            for k in sel: m[int(tgt[k])].append(self.pe.text_rva + int(idx[k]))
            self._e9 = m
        return self._e9

def make_func(side, start, end):
    """analyze an arbitrary range (function not in .pdata) like an indexed function"""
    a = analyze(side.pe, start, end)
    offs = a['offs']
    pre = a['masked'][:offs[10] if len(offs) > 10 else len(a['masked'])]
    f = dict(start=start, end=end, size=end - start, l1=h8(a['masked']), l2=h8('\n'.join(a['shape']).encode()),
             pre=h8(pre), npre=min(len(offs), 10), nins=len(offs), calls=a['calls'], refs=a['refs'], synthetic=True, hend=end, parts=1,
             raw=a['raw'], mask=a['mask'], offs=offs)
    return f

# ------------------------------------------------------------------ the mapper
class Mapper:
    def __init__(self, ae, v17, lib, log=print):
        self.ae, self.v, self.lib, self.log = ae, v17, lib, log
        self.A = {}            # strong anchors: AE func start -> 1.7 func start (L1 unique both sides, nins>=8)
        self.Ainv = {}
        self.Aall = {}         # strong + derived
        self.res = {}          # AE func start -> result dict
        self.libstarts = sorted(r for r in set(lib.map.values()) if ae.in_text(r))
        t = time.time()
        for f in ae.funcs:
            if f['nins'] < 8: continue
            ca = ae.l1.get(f['l1'], [])
            if len(ca) != 1: continue
            cv = v17.l1.get(f['l1'], [])
            if len(cv) != 1: continue
            self.A[f['start']] = cv[0]; self.Ainv[cv[0]] = f['start']
        self.Aall = dict(self.A)
        self._hcache = {}
        self.use_slot_check = True
        self.log('anchors (L1 unique both, nins>=8): %d of %d AE funcs  (%.0fs)' % (len(self.A), len(ae.funcs), time.time() - t))
        self.delta_pts = sorted((a, b - a) for a, b in self.A.items())
        self.delta_keys = [a for a, d in self.delta_pts]


    # ---- local-window alignment: the instruction at `off` in AE function fa sits in a context (>=3 insns each side, >=20 fixed
    # bytes) that occurs EXACTLY ONCE in the counterpart function fv -> that is the same instruction (works when the function
    # as a whole changed).  Returns the offset in fv or None.
    def _an(self, side, f):
        k = ('an', side.name, f['start'])
        if k not in self._hcache:
            a = analyze(side.pe, f['start'], f.get('hend', f['end']))
            self._hcache[k] = (a['raw'], a['mask'], a['offs'])
        return self._hcache[k]

    def window_align(self, fa, off, fv):
        ra, ma, oa = self._an(self.ae, fa); rv, mv, ov = self._an(self.v, fv)
        if off not in oa: return None
        i = oa.index(off); ovset = set(ov)
        for K in (3, 6, 12):
            lo = max(0, i - K); hi = min(len(oa) - 1, i + K)
            if hi - lo < 6: continue
            st = oa[lo]; en = oa[hi + 1] if hi + 1 < len(oa) else len(ra)
            raw = ra[st:en]; mask = ma[st:en]
            if sum(mask) < 20: continue
            parts = []; j = 0; n = len(raw)
            while j < n:
                k = j
                if mask[j]:
                    while k < n and mask[k]: k += 1
                    parts.append(re.escape(raw[j:k]))
                else:
                    while k < n and not mask[k]: k += 1
                    parts.append(b'.{%d}' % (k - j))
                j = k
            rx = re.compile(b''.join(parts), re.S)
            hits = []; pos = 0
            while len(hits) < 2:
                m = rx.search(rv, pos)
                if not m: break
                if m.start() in ovset: hits.append(m.start())
                pos = m.start() + 1
            if len(hits) == 1: return hits[0] + (off - st)
            if not hits: return None
        return None

    def build_gmap(self):
        """data global -> 1.7 global, from L1-anchored functions (identical masked bytes => same rip-relative operand slot)"""
        if getattr(self, '_gmap', None) is not None: return self._gmap
        ae, v = self.ae, self.v
        votes = collections.defaultdict(lambda: collections.defaultdict(int))
        for fs, vs in self.A.items():
            fa = ae.by_start[fs]; fv = v.by_start[vs]
            if len(fa['refs']) != len(fv['refs']): continue
            for (o1, t1), (o2, t2) in zip(fa['refs'], fv['refs']):
                if o1 == o2 and not ae.in_text(t1): votes[t1][t2] += 1
        g = {}; g1 = {}
        for t1, d in votes.items():
            if len(d) == 1:
                t2, n = next(iter(d.items()))
                if v.pe.sec_of(t2) == ae.pe.sec_of(t1):
                    g1[t1] = t2
                    if n >= 2: g[t1] = t2
        self._gmap = g; self._gmap1 = g1
        return g

    # ---- helpers
    def ae_func(self, rva):
        """AE function dict for rva (pdata or synthesized from library starts); returns (func, offset_in_func)"""
        ae = self.ae
        f = ae.func_containing(rva)
        if f: return f, rva - f['start']
        if not ae.in_text(rva): return None, 0
        i = bisect.bisect_right(self.libstarts, rva) - 1
        if i < 0: return None, 0
        s = self.libstarts[i]
        nxt = self.libstarts[i + 1] if i + 1 < len(self.libstarts) else ae.pe.tend
        end = min(nxt, s + 0x1000)
        # next pdata start bound
        j = bisect.bisect_right(ae.starts, s)
        if j < len(ae.starts): end = min(end, ae.starts[j])
        # trim trailing int3/nop padding
        tb = ae.tb
        while end > s + 1 and tb[end - 1 - ae.pe.text_rva] in (0xCC, 0x90): end -= 1
        key = ('syn', s)
        if key not in self.res:
            self.res[key] = make_func(ae, s, end)
        f = self.res[key]
        if rva >= f['end']: return None, 0
        return f, rva - s

    def v_func_full(self, f):
        return f

    def delta_hint(self, ae_start, v_start):
        i = bisect.bisect_left(self.delta_keys, ae_start)
        nb = [d for a, d in self.delta_pts[max(0, i - 4):i + 4] if a != ae_start]
        if not nb: return None
        nb.sort(); med = nb[len(nb) // 2]
        return (v_start - ae_start) - med

    # ---- crosscheck
    def crosscheck(self, fa, fv, strict_anchors=None, lenient=False, ordered=True, miss_tol=0.0, extra_fwd=0):
        """lenient (used only by the xref methods): a 1.7 body that ADDS a call to an anchored function or ADDS strings is a
        changed function, not a contradiction -- accepted only when every forward check (AE callees in order, AE callers still
        calling it, identical-caller sites, vtable slot) passes with >=3 data points; the additions are named in the detail."""
        A = self.A if strict_anchors is None else strict_anchors
        ae, v = self.ae, self.v
        pts = 0; bad = 0; det = []; fwd = 0; added = 0
        callee_ae = [t for off, t, mn in fa['calls'] if t in ae.by_start]
        callee_v = [t for off, t, mn in fv['calls'] if t in v.by_start]
        mapped = [A[t] for t in callee_ae if t in A]
        # ordered subsequence
        k = 0; miss = 0
        if ordered:
            for m in mapped:
                while k < len(callee_v) and callee_v[k] != m: k += 1
                if k < len(callee_v): k += 1
                else: miss += 1
        else:                                  # across compiler builds (1.5.97 vs 1.6.1170) call ORDER is not preserved: containment only
            sv_ = set(callee_v); miss = sum(1 for m in mapped if m not in sv_)
        tol_note = ''
        if miss and miss_tol and len(mapped) >= 8 and miss <= miss_tol * len(mapped):
            tol_note = ' (%d SE callee(s) absent from the target body: inlined or removed there)' % miss; miss = 0
        pts += len(mapped); bad += miss; fwd += len(mapped)
        det.append('callees %d/%d %s%s' % (len(mapped) - miss, len(mapped), 'ordered' if ordered else 'contained', tol_note))
        setae = set(callee_ae)
        rev = [t for t in callee_v if t in self.Ainv]
        rmiss = sum(1 for t in rev if self.Ainv[t] not in setae)
        pts += len(rev)
        if lenient and rmiss: added += rmiss
        else: bad += rmiss
        det.append('rev-callees %d/%d%s' % (len(rev) - rmiss, len(rev), ' (1.7 ADDS %d anchored call(s))' % rmiss if lenient and rmiss else ''))
        cl = 0; cm = 0
        for p, off in ae.callers.get(fa['start'], [])[:60]:
            if p in A:
                pv = v.by_start.get(A[p])
                cl += 1
                if not pv or fv['start'] not in {t for o, t, mn in pv['calls']}: cm += 1
        pts += cl; bad += cm; fwd += cl
        det.append('callers %d/%d' % (cl - cm, cl))
        sa = ae.strings_of(fa); sv = v.strings_of(fv)
        if sa or sv:
            pts += max(len(sa), 1)
            if sa != sv:
                if lenient and sa <= sv: added += 1
                else: bad += 1
            det.append('strings %s' % ('equal(%d)' % len(sa) if sa == sv else ('1.7 ADDS %d string(s)' % len(sv - sa) if lenient and sa <= sv else 'DIFFER ae=%d v=%d' % (len(sa), len(sv)))))
        iv = self.idcall_votes(fa)
        if iv:
            pts += 1; fwd += 1
            n = sum(len(x) for x in iv.values())
            if set(iv) != {fv['start']}: bad += 1; det.append('IDENTICAL-CALLER CONFLICT %s' % {hx(k): len(x) for k, x in iv.items()})
            else: det.append('identical-caller sites %d agree' % n)
        sp = self.slot_partners().get(fa['start']) if self.use_slot_check else None
        if sp:
            pts += 1; fwd += 1
            if fv['start'] not in sp: bad += 1; det.append('VTABLE-SLOT CONFLICT partner %s' % sorted(hx(x) for x in sp))
            else: det.append('vtable-slot partner agrees')
        fwd += extra_fwd; pts += extra_fwd
        if added and not bad and fwd < 3: bad += 1; det.append('additions in 1.7 body but only %d forward data point(s)' % fwd)
        verdict = 'fail' if bad else ('pass' if pts else 'na')
        return verdict, pts, '; '.join(det)

    def slot_partners(self, minpts=3):
        """AE slot function -> set of 1.7 slot functions, from RTTI-exact vtable pairs with EQUAL slot counts whose L1-anchored
        slots agree (>= minpts of them, 0 contradictions).  minpts=3 is the crosscheck/anchor-derivation grade; minpts=1 is only
        consulted as the second independent fact next to a unique >=32-fixed-byte body hit (sig:L1-body+slot)."""
        if not hasattr(self, '_spc'): self._spc = {}
        if minpts not in self._spc:
            ae, v = self.ae, self.v
            rt_a = ae.rtti()['by_key']; rt_v = v.rtti()['by_key']
            sp = collections.defaultdict(set)
            for key, avs in rt_a.items():
                vvs = rt_v.get(key, [])
                if len(avs) != 1 or len(vvs) != 1: continue
                sa = ae.vt_slots(avs[0]); sv = v.vt_slots(vvs[0])
                if len(sa) != len(sv): continue
                pts = bad = 0
                for x, y in zip(sa, sv):
                    if x in self.A: pts += 1; bad += self.A[x] != y
                if bad or pts < minpts: continue
                for x, y in zip(sa, sv): sp[x].add(y)
            self._spc[minpts] = sp
        return self._spc[minpts]

    # ---- function resolution
    def set_res(self, ae_rva, v_rva, conf, method, ev, cross, pts=0):
        r = dict(v17=v_rva, conf=conf, method=method, evidence=ev, cross=cross)
        return r

    def resolve_function(self, ae_rva):
        """returns result dict for an arbitrary AE rva inside .text"""
        fa, off = self.ae_func(ae_rva)
        if fa is None:
            return dict(v17=None, conf='UNRESOLVED', method='', evidence='AE rva not inside any known function', cross='na')
        r = self.resolve_start(fa)
        if off == 0 or r['v17'] is None: return r
        # mid-function: align by offset
        fv = self.v.by_start.get(r['v17'])
        if fv is None:
            fv = self.v.func_containing(r['v17'])
        if fa['l1'] == fv['l1']:
            r2 = dict(r); r2['v17'] = r['v17'] + off
            r2['evidence'] = 'mid-function +0x%X inside %s body (identical masked bytes, same offset); %s' % (off, hx(fa['start']), r['evidence'])
            return r2
        # instruction-index alignment when shape identical
        if fa['l2'] == fv['l2']:
            aa = analyze(self.ae.pe, fa['start'], fa['end']); av = analyze(self.v.pe, fv['start'], fv['end'])
            if off in aa['offs']:
                i = aa['offs'].index(off)
                r2 = dict(r); r2['v17'] = fv['start'] + av['offs'][i]
                r2['evidence'] = 'mid-function +0x%X (insn #%d) aligned by identical instruction shape; %s' % (off, i, r['evidence'])
                return r2
        return dict(v17=None, conf='UNRESOLVED', method='', evidence='mid-function offset +0x%X in %s: function mapped to %s but bodies differ, offset not provable' % (off, hx(fa['start']), hx(r['v17'])), cross='na')

    def resolve_start(self, fa):
        key = fa['start']
        if key in self.res and 'v17' in self.res[key] and not isinstance(key, tuple): return self.res[key]
        self.res[key] = dict(v17=None, conf='UNRESOLVED', method='', evidence='(in progress)', cross='na')   # recursion guard
        r = self._resolve_start(fa)
        self.res[key] = r
        if r['v17'] is not None and r['cross'] != 'fail' and r['conf'] != 'UNRESOLVED' and r['method'] not in ('thunk', 'import:name'):
            self.Aall.setdefault(key, r['v17'])
        return r


    def weak_slot(self, fa):
        """(set of 1.7 candidates, description) if fa sits in a vtable slot of an RTTI-exact pair with equal slot counts in which
        >=1 OTHER anchored slot agrees and none contradicts (fa itself is never counted as its own evidence)"""
        if not self.use_slot_check: return None
        if not hasattr(self, '_slotidx'):
            ae, v = self.ae, self.v
            rt_a = ae.rtti()['by_key']; rt_v = v.rtti()['by_key']
            idx = collections.defaultdict(list)
            for key, avs in rt_a.items():
                vvs = rt_v.get(key, [])
                if len(avs) != 1 or len(vvs) != 1: continue
                sa = ae.vt_slots(avs[0]); sv = v.vt_slots(vvs[0])
                if len(sa) != len(sv): continue
                for i, x in enumerate(sa): idx[x].append((sa, sv, i, key[0].decode(errors='replace')))
            self._slotidx = idx
        x0 = fa['start']; cands = set(); desc = []
        for sa, sv, i, cn in self._slotidx.get(x0, []):
            pts = bad = 0
            for k, (x, y) in enumerate(zip(sa, sv)):
                if k != i and x in self.A: pts += 1; bad += self.A[x] != y
            if bad or pts < 1: return None
            cands.add(sv[i]); desc.append('%s slot %d (%d other anchored slots agree)' % (cn[:40], i, pts))
        if not cands: return None
        return cands, 'it sits in a vtable slot of RTTI-exact class pair(s) with equal slot counts: ' + '; '.join(desc[:3]) + ' -> slot partner %s' % sorted(hx(x) for x in cands)


    # ---- block lock-step: the nearest two L1-anchored functions on EACH side of F in the 1.6.1170 image all moved by the same delta d
    # (a TU/library block that was relocated intact).  Returns d or None.  Only ever a SECOND fact next to a unique body hit.
    def lockstep_delta(self, x, exclude=()):
        ae = self.ae
        if getattr(self, '_akeys_src', None) is not self.A: self._akeys = sorted(self.A); self._akeys_src = self.A
        ks = self._akeys; i = bisect.bisect_left(ks, x)
        prev = []; j = i - 1
        while j >= 0 and len(prev) < 2:
            if ks[j] != x and ks[j] not in exclude: prev.append(ks[j])
            j -= 1
        nxt = []; j = i
        while j < len(ks) and len(nxt) < 2:
            if ks[j] != x and ks[j] not in exclude: nxt.append(ks[j])
            j += 1
        if len(prev) < 2 or len(nxt) < 2: return None
        ds = {self.A[k] - k for k in prev + nxt}
        if len(ds) != 1: return None
        if nxt[-1] - prev[-1] > 0x2000: return None          # the flanks must be close: a tight block, not a distant coincidence
        return next(iter(ds))


    def callee_disambiguate(self, fa, ha, hv, raw):
        """duplicate bodies: pair this AE copy with exactly one 1.7 copy through an anchored callee that distinguishes the copies"""
        ae, v = self.ae, self.v
        mine = {self.A[t] for o, t, m in fa['calls'] if t in self.A}
        if not mine: return None
        others = set()
        for h in ha:
            if h == fa['start']: continue
            f = ae.by_start.get(h)
            if f is None: return None
            others |= {t for o, t, m in f['calls']}
        # callees of this copy that no other AE copy calls, mapped to 1.7
        uniq = {self.A[t] for o, t, m in fa['calls'] if t in self.A and t not in others}
        if not uniq: return None
        hit = []
        for h in hv:
            f = v.by_start.get(h)
            if f is None: return None
            if uniq & {t for o, t, m in f['calls']}: hit.append(h)
        return hit[0] if len(hit) == 1 else None



    def data_block_candidate(self, g, exclude=()):
        """data lock-step: the globals that map from L1-anchored functions (>=1 voter) nearest to g on each side move by ONE delta
        (nearest on each side equal, >=3 of the 4 nearest equal, flanks <= 0x400 B apart) -> g + delta"""
        self.build_gmap(); g1 = self._gmap1
        if getattr(self, '_g1keys_src', None) is not g1: self._g1keys = sorted(g1); self._g1keys_src = g1
        ks = self._g1keys; i = bisect.bisect_left(ks, g)
        prev = [k for k in ks[max(0, i - 6):i] if k != g and k not in exclude][-2:]
        nxt = [k for k in ks[i:i + 6] if k != g and k not in exclude][:2]
        if len(prev) < 1 or len(nxt) < 1: return None
        dp, dn = g1[prev[-1]] - prev[-1], g1[nxt[0]] - nxt[0]
        if dp != dn or nxt[0] - prev[-1] > 0x400: return None
        ds = [g1[k] - k for k in prev + nxt]
        if sum(1 for d in ds if d == dp) < 3: return None
        return g + dp

    def gdelta(self, g, exclude=()):
        """nearest two gmap globals on each side (within 0x1000 B) all moved by one delta -> that delta"""
        gm = self.build_gmap()
        if getattr(self, '_gkeys_src', None) is not gm: self._gkeys = sorted(gm); self._gkeys_src = gm
        ks = self._gkeys; i = bisect.bisect_left(ks, g)
        prev = [k for k in ks[max(0, i - 3):i] if k != g][-2:]; nxt = [k for k in ks[i:i + 3] if k != g][:2]
        if len(prev) < 2 or len(nxt) < 2: return None
        ds = {gm[k] - k for k in prev + nxt}
        if len(ds) != 1 or nxt[-1] - prev[0] > 0x1000: return None
        return next(iter(ds))

    def head_raw(self, side, f):
        k = (side.name, f['start'])
        if k not in self._hcache:
            a = analyze(side.pe, f['start'], f.get('hend', f['end']))
            self._hcache[k] = (a['raw'], a['mask'])
        return self._hcache[k]

    def v_func_at(self, a, length):
        fv = self.v.by_start.get(a)
        if fv: return fv
        k = ('v-syn', a, length)
        if k not in self._hcache:
            self._hcache[k] = make_func(self.v, a, min(a + length, self.v.pe.tend))
        return self._hcache[k]

    def _resolve_start(self, fa):
        ae, v = self.ae, self.v
        tb = ae.tb; s = fa['start']; t0 = ae.pe.text_rva
        hl = fa.get('hend', fa['end']) - s
        # --- import thunk: jmp [rip+IAT]
        if hl == 6 and tb[s - t0] == 0xFF and tb[s - t0 + 1] == 0x25:
            rel = struct.unpack_from('<i', tb, s - t0 + 2)[0]; slot = s + 6 + rel
            nm = ae.iat_names().get(slot)
            if nm:
                vslot = [k for k, x in v.iat_names().items() if x == nm]
                sa = ae.ff25_stubs().get(slot, []); sv = v.ff25_stubs().get(vslot[0], []) if len(vslot) == 1 else []
                if len(sa) == 1 and len(sv) == 1:
                    return dict(v17=sv[0], conf='EXACT', method='import:name',
                                evidence='import thunk jmp [IAT] for %s!%s: the same import has exactly one thunk in each image' % nm, cross='na')
                return dict(v17=None, conf='UNRESOLVED', method='import:name', evidence='import %s!%s thunks AE %d / 1.7 %d (slots %d)' % (nm + (len(sa), len(sv), len(vslot))), cross='na')
        # --- jmp rel32 thunk
        if hl == 5 and tb[s - t0] == 0xE9:
            rel = struct.unpack_from('<i', tb, s - t0 + 1)[0]
            tgt = s + 5 + rel
            tf = ae.func_containing(tgt)
            if tf and tf['start'] == tgt:
                tr = self.resolve_start(tf)
                if tr['v17'] is not None and tr['cross'] != 'fail':
                    ae_stubs = ae.e9_stubs().get(tgt, []); v_stubs = v.e9_stubs().get(tr['v17'], [])
                    if len(ae_stubs) == 1 and len(v_stubs) == 1:
                        return dict(v17=v_stubs[0], conf='XREF', method='thunk',
                                    evidence='AE 5-byte jmp thunk -> %s (body mapped to %s as %s); exactly one 16-aligned E9 stub targets the body in AE and exactly one in 1.7' % (hx(tgt), hx(tr['v17']), tr['conf']),
                                    cross=tr['cross'])
                    return dict(v17=None, conf='UNRESOLVED', method='thunk', evidence='AE thunk -> %s mapped to %s but stubs ambiguous (AE %d, 1.7 %d)' % (hx(tgt), hx(tr['v17']), len(ae_stubs), len(v_stubs)), cross='na')
                return dict(v17=None, conf='UNRESOLVED', method='thunk', evidence='AE thunk -> %s whose body is unmapped' % hx(tgt), cross='na')
        rej = ''
        if getattr(self, 'no_sig', False):      # self-test mode: the function's own bytes may not be used
            r = self.xref(fa)
            return r or dict(v17=None, conf='UNRESOLVED', method='', evidence='(no_sig) xref too weak', cross='na')
        # --- masked head-region scan over both .text (independent of the function index)
        raw, mask = self.head_raw(ae, fa)
        nfixed = sum(mask)
        ha = ae.count_pattern(raw, mask); hv = v.count_pattern(raw, mask)
        if len(ha) == 1 and ha[0] == s and len(hv) == 1:
            fv = self.v_func_at(hv[0], len(raw))
            cx, pts, det = self.crosscheck(fa, fv)
            ev = 'masked-byte head region (%d B, %d fixed, %d insn%s) hits exactly once in 1.7.104 .text (at %s) and exactly once in 1.6.1170 .text' % (
                len(raw), nfixed, fa['nins'], '' if fa.get('parts', 1) == 1 else ', function has %d split regions' % fa['parts'], hx(hv[0]))
            if cx == 'fail':
                rej = 'REJECTED L1 candidate %s: crosscheck FAIL (%s)' % (hx(fv['start']), det)
            elif cx == 'pass' and nfixed >= 24 and fa['nins'] >= 5:
                return dict(v17=fv['start'], conf='UNIQUE-SIG', method='sig:L1-body', evidence=ev + '; ' + det, cross=cx)
            elif cx == 'na' and nfixed >= 64 and fa['nins'] >= 16:
                return dict(v17=fv['start'], conf='UNIQUE-SIG', method='sig:L1-body', evidence=ev + ' (no callers/callees/strings to crosscheck; accepted on a >=64 fixed-byte body)' + '; ' + det, cross=cx)
            elif cx == 'pass':
                return dict(v17=fv['start'], conf='UNIQUE-SIG', method='sig:L1-body(short)', evidence=ev + ' (short body, accepted only because crosscheck passes); ' + det, cross=cx)
            else:
                ld = self.lockstep_delta(s) if (nfixed >= 32 and getattr(self, 'use_lockstep', True)) else None
                if ld is not None and fv['start'] - s == ld:
                    return dict(v17=fv['start'], conf='UNIQUE-SIG', method='sig:L1-body+block', evidence=ev + ' (no callers/callees/strings; second independent fact: the two nearest L1-anchored functions on each side of it in the 1.6.1170 image all moved by %+d B, a block relocated intact, and this candidate sits at exactly that delta)' % ld, cross='pass')
                wk = self.weak_slot(fa) if nfixed >= 32 else None
                if wk and fv['start'] in wk[0]:
                    return dict(v17=fv['start'], conf='UNIQUE-SIG', method='sig:L1-body+slot', evidence=ev + ' (no callers/callees/strings; second independent fact: %s)' % wk[1], cross='pass')
                rej = 'short unique body (%d fixed bytes) at %s but no crosscheck data' % (nfixed, hx(fv['start'])) + (' and the vtable-slot partner %s disagrees' % sorted(hx(x) for x in wk[0]) if wk else '')
        elif len(ha) >= 2 and len(ha) == len(hv) and s in ha:
            ld = self.lockstep_delta(s) if getattr(self, 'use_lockstep', True) else None
            lk = [h for h in hv if ld is not None and h - s == ld]
            cs = self.callee_disambiguate(fa, ha, hv, raw)
            if len(lk) == 1 and cs is not None and cs == lk[0]:
                fv = self.v_func_at(lk[0], len(raw))
                return dict(v17=fv['start'], conf='UNIQUE-SIG', method='sig:L1-body+block+callee', evidence='masked head region (%d B, %d fixed) hits %d times in both images (not unique); of the 1.7 hits exactly one calls the counterpart of an anchored callee that only this AE copy calls, and the same hit sits at the block lock-step delta %+d B' % (len(raw), nfixed, len(ha), ld), cross='pass')
            if len(lk) == 1 and cs is None and sum(mask) >= 24:
                fv = self.v_func_at(lk[0], len(raw))
                return dict(v17=fv['start'], conf='UNIQUE-SIG', method='sig:L1-body+block', evidence='masked head region (%d B, %d fixed) hits %d times in both images (not unique); the two nearest L1-anchored functions on each side of this copy all moved by %+d B (a block relocated intact) and exactly one 1.7 copy sits at that delta' % (len(raw), nfixed, len(ha), ld), cross='pass')
            if cs is not None and ld is None and sum(mask) >= 24:
                fv = self.v_func_at(cs, len(raw))
                return dict(v17=fv['start'], conf='UNIQUE-SIG', method='sig:L1-body+callee', evidence='masked head region (%d B, %d fixed) hits %d times in both images (not unique); exactly one of the AE hits calls an L1-anchored callee that none of the other AE hits call, and exactly one 1.7 hit calls its counterpart: that pairs this copy with %s' % (len(raw), nfixed, len(ha), hx(cs)), cross='pass')
            wk = self.weak_slot(fa)
            if wk and len([h for h in hv if h in wk[0]]) == 1:
                tgt = [h for h in hv if h in wk[0]][0]
                fv = self.v_func_at(tgt, len(raw))
                return dict(v17=fv['start'], conf='UNIQUE-SIG', method='sig:L1-body+slot', evidence='masked head region (%d B, %d fixed) hits %d times in both images (not unique); %s selects exactly one of the 1.7 hits' % (len(raw), nfixed, len(ha), wk[1]), cross='pass')
            rej = 'masked head scan: AE hits %d, 1.7 hits %d' % (len(ha), len(hv))
        else:
            rej = 'masked head scan: AE hits %d, 1.7 hits %d' % (len(ha), len(hv))
        # --- whole-function masked bytes (split cold regions included) unique in both indexed images
        if s in self.A and not getattr(self, 'no_sig', False):
            fv = v.by_start[self.A[s]]
            cx, pts, det = self.crosscheck(fa, fv)
            nfw = fa['size'] - 4 * (len(fa['calls']) + len(fa['refs']))
            ev = 'whole-function masked bytes (%d B in %d region(s), ~%d fixed, %d insn) are identical and occur once among the indexed functions of each image (%s)' % (fa['size'], fa.get('parts', 1), nfw, fa['nins'], hx(fv['start']))
            if cx == 'fail': rej += ' | REJECTED whole-function candidate %s: crosscheck FAIL (%s)' % (hx(fv['start']), det)
            elif (cx == 'pass' and nfw >= 24) or (cx == 'na' and nfw >= 64 and fa['nins'] >= 16):
                return dict(v17=fv['start'], conf='UNIQUE-SIG', method='sig:L1-whole', evidence=ev + '; ' + det, cross=cx)
        # --- shape (mnemonic+operand-kind sequence), strong crosscheck required
        sa = ae.l2.get(fa['l2'], []); sv = v.l2.get(fa['l2'], [])
        if len(sa) == 1 and len(sv) == 1 and fa['nins'] >= 20:
            fv = v.by_start[sv[0]]
            if 0.8 <= fv['size'] / max(fa['size'], 1) <= 1.25:
                cx, pts, det = self.crosscheck(fa, fv)
                if cx == 'pass' and pts >= 3:
                    return dict(v17=fv['start'], conf='UNIQUE-SIG', method='sig:shape',
                                evidence='instruction-shape (mnemonic+operand kinds, %d insn) unique among the %d/%d indexed functions of AE/1.7 (struct displacements/registers differ); size %d vs %d; %s' % (fa['nins'], len(ae.funcs), len(v.funcs), fa['size'], fv['size'], det),
                                cross=cx)
                elif cx == 'fail':
                    rej += ' | REJECTED shape candidate %s: crosscheck FAIL (%s)' % (hx(fv['start']), det)
        # --- prefix hash
        pa = ae.pre.get(fa['pre'], []) if fa['npre'] >= 10 else []
        pv = v.pre.get(fa['pre'], []) if fa['npre'] >= 10 else []
        if len(pa) == 1 and len(pv) == 1:
            fv = v.by_start[pv[0]]
            if 0.7 <= fv['size'] / max(fa['size'], 1) <= 1.4:
                cx, pts, det = self.crosscheck(fa, fv)
                if cx == 'pass' and pts >= 3:
                    return dict(v17=fv['start'], conf='UNIQUE-SIG', method='sig:prefix',
                                evidence='first-10-instruction masked prefix unique in AE and 1.7; size %d vs %d; %s' % (fa['size'], fv['size'], det),
                                cross=cx)
                elif cx == 'fail':
                    rej += ' | REJECTED prefix candidate %s: crosscheck FAIL (%s)' % (hx(fv['start']), det)
        r = self.xref(fa)
        if r: return r
        return dict(v17=None, conf='UNRESOLVED', method='', evidence=rej + ' | no unique signature and xref anchoring too weak (%d insn)' % fa['nins'], cross='na')

    # ---- xref anchoring
    def xref_votes(self, fa):
        ae, v, A = self.ae, self.v, self.Aall
        votes = collections.defaultdict(list)
        for off, t, mn in fa['calls']:
            a = A.get(t)
            if a is not None:
                for cs, coff in v.callers.get(a, []):
                    votes[cs].append(('callee', t, a))
        for ps, poff in ae.callers.get(fa['start'], [])[:80]:
            pa = A.get(ps)
            if pa is None: continue
            P = ae.by_start.get(ps); P17 = v.by_start.get(pa)
            if not P or not P17 or len(P['calls']) != len(P17['calls']): continue
            ci = [i for i, (o, t, mn) in enumerate(P['calls']) if o == poff]
            if ci:
                cand = P17['calls'][ci[0]][1]
                if cand in v.by_start: votes[cand].append(('caller', ps, pa))
        gm = self.build_gmap()
        for off, t in fa['refs']:
            t2 = gm.get(t)
            if t2 is not None and len(ae.refs_in.get(t, ())) <= 12 and len(v.refs_in.get(t2, ())) <= 12:
                for cs, coff in v.refs_in.get(t2, ()): votes[cs].append(('global', t, t2))
        sm = v.strmap(); am = ae.strmap()
        for s in ae.strings_of(fa):
            if len(am.get(s, ())) <= 4 and len(sm.get(s, ())) <= 4:
                for cs in sm.get(s, ()): votes[cs].append(('str', s, None))
        return votes

    def idcall_votes(self, fa):
        """call sites inside mapped callers whose code is identical in both images (identical masked bytes, or identical
        instruction shape with identical call offsets): same instruction => same callee"""
        ae, v = self.ae, self.v
        out = collections.defaultdict(list)
        for ps, poff in ae.callers.get(fa['start'], [])[:200]:
            pa = self.Aall.get(ps)
            if pa is None: continue
            P = ae.by_start[ps]; P17 = v.by_start.get(pa)
            if P17 is None: continue
            if ps not in self.A:
                if P['l2'] != P17['l2'] or [o for o, t, m in P['calls']] != [o for o, t, m in P17['calls']]:
                    # whole caller differs between the images: align the call site by its local code window instead
                    wo = self.window_align(P, poff, P17)
                    if wo is None: continue
                    for o, t, mn in P17['calls']:
                        if o == wo:
                            out[t].append((ps, pa, poff)); break
                    continue
            for o, t, mn in P17['calls']:
                if o == poff:
                    out[t].append((ps, pa, poff)); break
        return out

    def xref(self, fa):
        ae, v = self.ae, self.v
        idv = self.idcall_votes(fa)
        votes = self.xref_votes(fa)
        rank = sorted(votes.items(), key=lambda kv: -len({(a, b) for a, b, c in kv[1]}))
        # (1) identical-caller call sites
        if idv:
            if len(idv) > 1:
                return None
            top = next(iter(idv)); sites = idv[top]
            fv = self.v_func_at(top, fa.get('hend', fa['end']) - fa['start'])
            if fv is None: return None
            if not (0.4 <= fv['size'] / max(fa['size'], 1) <= 2.5): return None
            cx, pts, det = self.crosscheck(fa, fv, lenient=True)
            if cx == 'fail': return None
            if rank and rank[0][0] != top and len({(a, b) for a, b, c in rank[0][1]}) >= 3: return None
            sg = ', '.join('%s->%s call@+0x%X' % (hx(a), hx(b), c) for a, b, c in sites[:4])
            return dict(v17=top, conf='XREF', method='xref:identical-caller',
                        evidence='%d caller(s) whose masked bytes are identical in both images (%s) make the call at the same offset, to %s; no conflicting caller; size %d vs %d; %s' % (len(sites), sg, hx(top), fa['size'], fv['size'], det),
                        cross=cx)
        # (2) graph consensus
        r = self._xref_graph(fa, rank)
        if r: return r
        return self.xref_fuzzy(fa) if getattr(self, 'use_fuzzy', True) else None

    def _xref_graph(self, fa, rank):
        v = self.v
        if not rank: return None
        top, tv = rank[0]
        nsig = len({(a, b) for a, b, c in tv})
        second = len({(a, b) for a, b, c in rank[1][1]}) if len(rank) > 1 else 0
        if nsig < 3 or second >= nsig: return None
        fv = v.by_start.get(top)
        if not fv or not (0.6 <= fv['size'] / max(fa['size'], 1) <= 1.7): return None
        cx, pts, det = self.crosscheck(fa, fv, lenient=True)
        if cx != 'pass': return None
        sig = []
        for kind, a, b in tv[:6]:
            if kind == 'callee': sig.append('callee %s->%s' % (hx(a), hx(b)))
            elif kind == 'caller': sig.append('caller %s->%s (nth-call aligned)' % (hx(a), hx(b)))
            elif kind == 'global': sig.append('global %s->%s' % (hx(a), hx(b)))
            else: sig.append('string %r' % a[:40].decode())
        return dict(v17=top, conf='XREF', method='xref:graph',
                    evidence='%d independent anchors point to the same 1.7 function (runner-up %d): %s; %s' % (nsig, second, ' | '.join(sig), det),
                    cross=cx)


    # ---- fuzzy: functions whose bodies CHANGED between the images.  Candidates come from the call/caller/global/string votes and
    # the block lock-step; accepted only with >=2 DIFFERENT kinds of independent evidence agreeing on one unique candidate, a
    # mnemonic-sequence similarity >= 0.75, a size ratio in [0.5, 2], a clear margin over the runner-up and a lenient crosscheck.
    def shape_ratio(self, fa, fv):
        import difflib
        k = ('sr', fa['start'], fv['start'])
        if k not in self._hcache:
            a = analyze(self.ae.pe, fa['start'], fa.get('hend', fa['end']))['shape']; b = analyze(self.v.pe, fv['start'], fv.get('hend', fv['end']))['shape']
            a = [x.split(' ')[0] for x in a]; b = [x.split(' ')[0] for x in b]
            self._hcache[k] = difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()
        return self._hcache[k]


    # ---- pointer-table lock-step (CRT initializer tables, callback arrays, vtables): the entries on both sides of F's slot that are
    # L1-anchored sit at the same relative slots of ONE 1.7 table; F's counterpart is the pointer found at the corresponding slot.
    def text_ptr_slots(self, side):
        if getattr(side, '_tps', None) is None:
            m = collections.defaultdict(list)
            lo = BASE + side.pe.text_rva; hi = BASE + side.pe.tend
            for n, va, vs, ro, rs in side.pe.secs:
                if n not in ('.rdata', '.data'): continue
                a64 = np.frombuffer(side.pe.d[ro:ro + (rs // 8) * 8], dtype='<u8')
                for j in np.flatnonzero((a64 >= lo) & (a64 < hi)): m[int(a64[j]) - BASE].append(va + 8 * int(j))
            side._tps = m
        return side._tps

    def table_candidate(self, fa):
        """nearest L1-anchored entry on each side of F's (unique) table slot, within 256 entries, such that the entries between them
        are code pointers in both tables and the gap between the two anchored entries is IDENTICAL in both images (so nothing was
        inserted or removed in between); F's counterpart is the 1.7 slot at the same distance from the left anchor."""
        ae, v = self.ae, self.v
        ta = self.text_ptr_slots(ae); tv = self.text_ptr_slots(v)
        slots = ta.get(fa['start'], [])
        if len(slots) != 1: return None
        p = slots[0]
        def entry(side, slot):
            try: return side.pe.u64(slot) - BASE
            except Exception: return None
        def nearest(sign):
            for k in range(1, 257):
                x = entry(ae, p + sign * 8 * k)
                if x is None or not ae.in_text(x): return None            # table run ended
                if x in self.A:
                    ys = tv.get(self.A[x], [])
                    return (k, x, self.A[x], ys[0]) if len(ys) == 1 else None
            return None
        L = nearest(-1); Rr = nearest(1)
        if L is None or Rr is None: return None
        kl, xl, yl, ql = L; kr, xr, yr, qr = Rr
        if qr - ql != (kl + kr) * 8: return None                           # something was inserted/removed between the anchors
        q = ql + 8 * kl
        for d in range(-kl, kr + 1):
            xa = entry(ae, p + 8 * d); xv = entry(v, q + 8 * d)
            if xa is None or xv is None or not ae.in_text(xa) or not v.in_text(xv): return None
        g = entry(v, q)
        return g, [(-kl, xl, yl), (kr, xr, yr)]

    def xref_table(self, fa):
        t = self.table_candidate(fa)
        if t is None: return None
        g, vs = t
        raw, mask = self.head_raw(self.ae, fa)
        if self.v.count_pattern(raw, mask, 5000).count(g) != 1: return None          # the 1.7 entry must carry the same masked head
        fv = self.v_func_at(g, 60) if g not in self.v.by_start else self.v.by_start[g]
        if g in self.v.by_start and not (0.4 <= fv['size'] / max(fa['size'], 1) <= 2.5): return None
        if g in self.v.by_start:
            cx, pts, det = self.crosscheck(fa, fv, lenient=True)
            if cx == 'fail': return None
        else: det = 'candidate is a leaf without .pdata'
        return dict(v17=g, conf='XREF', method='xref:table',
                    evidence='this function is a unique entry of one pointer table in the 1.6.1170 image; %d L1-anchored entries on both sides of it (offsets %s) map to the same relative slots of one 1.7.104 table, every slot in between is a code pointer in both, and the 1.7 slot at its position holds %s; %s' % (len(vs), ','.join('%+d' % d for d, x, y in sorted(vs)), hx(g), det),
                    cross='pass')

    def xref_fuzzy(self, fa):
        ae, v = self.ae, self.v
        tb_ = self.xref_table(fa)
        if tb_: return tb_
        votes = self.xref_votes(fa)
        for p_, o_ in ae.callers.get(fa['start'], [])[:0]: pass
        ld = self.lockstep_delta(fa['start']) if getattr(self, 'use_lockstep', True) else None
        if ld is not None and (fa['start'] + ld) in v.by_start: votes[fa['start'] + ld].append(('block', fa['start'], fa['start'] + ld))
        for off, t, mn in fa['calls']:
            pass
        idv = self.idcall_votes(fa)
        for tgt, sites in idv.items():
            if tgt in v.by_start:
                for a_, b_, c_ in sites: votes[tgt].append(('idcaller', a_, b_))
        scored = []
        for cand, vs in votes.items():
            kinds = {k for k, a_, b_ in vs}; sig = {(k, a_, b_) for k, a_, b_ in vs}
            fv = v.by_start.get(cand)
            if fv is None or not (0.5 <= fv['size'] / max(fa['size'], 1) <= 2.0): continue
            scored.append((len(kinds), len(sig), cand, kinds))
        if not scored: return None
        scored.sort(key=lambda x: (-x[0], -x[1]))
        top = scored[0]
        blk = [c for c in scored if 'block' in c[3]]
        if blk and not (top[0] >= 2 and top[1] >= 3 and 'block' not in top[3]):
            c = blk[0]; fv = v.by_start[c[2]]
            others = [x for x in scored if x[2] != c[2] and len(x[3] - {'block'}) >= 2 and x[1] >= 3]
            corro = len(c[3] - {'block'})
            if not others and 0.9 <= fv['size'] / max(fa['size'], 1) <= 1.12 and (fa['nins'] >= 20 or corro >= 1):
                rt = self.shape_ratio(fa, fv)
                if rt >= 0.9:
                    cx, pts, det = self.crosscheck(fa, fv, lenient=True)
                    if cx != 'fail':
                        return dict(v17=fv['start'], conf='XREF', method='xref:block+shape',
                                    evidence='the two nearest L1-anchored functions on each side moved by the same delta and a function starts exactly there in 1.7; mnemonic-sequence similarity %.2f, size %d vs %d, %d insn%s; no rival candidate with >=2 other kinds of evidence; %s' % (rt, fa['size'], fv['size'], fa['nins'], ('; also voted by ' + '+'.join(sorted(c[3] - {'block'}))) if corro else '', det),
                                    cross='pass' if cx == 'pass' else 'na')
        if top[0] < 2 or top[1] < 3: return None
        if len(scored) > 1 and (scored[1][0] >= top[0] or scored[1][1] >= top[1] - 1): return None
        fv = v.by_start[top[2]]
        rt = self.shape_ratio(fa, fv)
        if rt < 0.75: return None
        cx, pts, det = self.crosscheck(fa, fv, lenient=True)
        if cx == 'fail': return None
        return dict(v17=fv['start'], conf='XREF', method='xref:fuzzy',
                    evidence='candidate backed by %d independent signals of %d different kinds (%s) with the runner-up clearly behind; mnemonic-sequence similarity %.2f, size %d vs %d; %s' % (top[1], top[0], '+'.join(sorted(top[3])), rt, fa['size'], fv['size'], det),
                    cross='pass')

    def xref_expand(self, fa):
        """derive extra anchors for neighbours of an unresolved function (not reported on their own)"""
        ae = self.ae; n = 0
        seen = {fa['start']}; frontier = [fa['start']]
        for depth in range(2):                       # neighbours, then neighbours of neighbours
            nxt = []
            for fs in frontier:
                f0 = fa if fs == fa['start'] else ae.by_start.get(fs)       # the root may be a synthetic (no .pdata) function
                if not f0: continue
                nbrs = set(t for off, t, mn in f0['calls'] if t in ae.by_start) | set(p for p, o in ae.callers.get(fs, [])[:40])
                for nb in nbrs:
                    if nb in seen: continue
                    seen.add(nb); nxt.append(nb)
                    if nb in self.Aall: continue
                    f = ae.by_start.get(nb)
                    if not f: continue
                    r = self.xref(f)
                    if r:
                        self.Aall[nb] = r['v17']; n += 1
            frontier = nxt[:300]
        return n

    # ---- vtables / RTTI
    def ae_vtable_key(self, vt):
        ae = self.ae; r = ae.rtti()
        try: col = ae.pe.u64(vt - 8) - BASE
        except Exception: return None
        return r['col_key'].get(col)

    def resolve_vtable(self, vt):
        ae, v = self.ae, self.v
        key = self.ae_vtable_key(vt)
        if key is None: return dict(v17=None, conf='UNRESOLVED', method='', evidence='AE vtable at %s has no readable RTTI COL' % hx(vt), cross='na')
        name = key[0].decode(errors='replace'); base = key[1].decode(errors='replace')
        a_all = ae.rtti()['by_key'].get(key, [])
        v_all = v.rtti()['by_key'].get(key, [])
        anon_note = ''
        if (len(v_all) != 1 or len(a_all) != 1) and _anon.search(key[0] + key[1]):
            nk = (norm_anon(key[0]), norm_anon(key[1]))
            an = ae.rtti()['by_key_n'].get(nk, []); vn = v.rtti()['by_key_n'].get(nk, [])
            if len(an) == 1 and len(vn) == 1 and an[0] == vt:
                a_all, v_all = an, vn
                anon_note = '; anonymous-namespace hash differs between the images and is normalized (?A0xHHHHHHHH): the normalized class+base key is unique in both'
        if len(v_all) != 1 or len(a_all) != 1:
            return dict(v17=None, conf='UNRESOLVED', method='rtti', evidence='%s subobject %s: AE vtables %d, 1.7 vtables %d (need exactly 1 each)' % (name, base, len(a_all), len(v_all)), cross='na')
        vv = v_all[0]
        # crosscheck: class hierarchy names + slots
        hier_a = self.hier(ae, vt); hier_v = self.hier(v, vv)
        sa = ae.vt_slots(vt); sv = v.vt_slots(vv)
        pts = 0; bad = 0
        notes = []
        if len(sa) == len(sv):
            for x, y in zip(sa, sv):
                if x in self.A:
                    pts += 1
                    if self.A[x] != y: bad += 1
        else:
            # virtual slots were inserted/removed: every L1-anchored AE slot function must reappear in the 1.7 vtable, in the same order
            notes.append('SLOT LAYOUT CHANGE: slot count AE %d vs 1.7 %d' % (len(sa), len(sv)))
            pos = -1; shifts = []
            svi = {y: j for j, y in reversed(list(enumerate(sv)))}
            for i, x in enumerate(sa):
                if x in self.A:
                    y = self.A[x]; j = svi.get(y)
                    pts += 1
                    if j is None or j < pos: bad += 1
                    else: pos = j; shifts.append('%d->%d' % (i, j))
            if shifts: notes.append('anchored slots AE->1.7: ' + ' '.join(shifts[:12]))
        slot_bad = bad; bad = 0
        if hier_a is not None: hier_a = [norm_anon(x) for x in hier_a]
        if hier_v is not None: hier_v = [norm_anon(x) for x in hier_v]
        if hier_a is not None and hier_v is not None:
            if hier_a == hier_v: pts += 1; notes.append('class hierarchy identical (%d bases)' % len(hier_a))
            elif set(hier_a) <= set(hier_v):
                pts += 1; notes.append('LAYOUT CHANGE: 1.7.104 adds base(s) %s to the class hierarchy (%d -> %d bases); subobject offsets/sizeof may shift, vtable identity by base name is unaffected' % ([x.decode(errors='replace') for x in hier_v if x not in hier_a], len(hier_a), len(hier_v)))
            else: bad += 1; notes.append('CLASS HIERARCHY DIFFERS (AE-only %s; 1.7-only %s)' % ([x.decode(errors='replace') for x in hier_a if x not in hier_v], [x.decode(errors='replace') for x in hier_v if x not in hier_a]))
        if slot_bad:
            # the class IDENTITY is proven by the exact mangled name + base subobject + identical class hierarchy; anchored slots that
            # disagree mean the virtual-function semantics changed (a layout change), reported as such, not as a mis-pairing
            if hier_a is not None and hier_v is not None and not bad:
                notes.append('SLOT CONTRADICTION: %d of the %d L1-anchored AE slot functions are not at the corresponding 1.7 slot (virtual layout/semantics changed; identity rests on the exact class name and the identical hierarchy)' % (slot_bad, pts))
            else: bad += slot_bad
        cx = 'fail' if bad else ('pass' if pts else 'na')
        ev = 'exact RTTI class %s, base subobject %s (found by BaseClassDescriptor displacement, not by raw offset) unique in both images%s; COL/TD chain verified (sig=1, pSelf); L1-anchored slots consistent %d/%d%s' % (name, base if base != name else '(primary)', anon_note, max(pts - bad, 0), pts, ('; ' + '; '.join(notes)) if notes else '')
        if bad:
            return dict(v17=None, conf='UNRESOLVED', method='rtti:vtable', evidence='REJECTED candidate %s: crosscheck FAIL: ' % hx(vv) + ev, cross='fail')
        return dict(v17=vv, conf='EXACT', method='rtti:vtable', evidence=ev, cross=cx, slots=(sa, sv, pts))

    def hier(self, side, vt):
        """names of the base classes in the vtable's ClassHierarchyDescriptor"""
        try:
            pe = side.pe
            col = pe.u64(vt - 8) - BASE
            chd = pe.u32(col + 16); n = pe.u32(chd + 8); arr = pe.u32(chd + 12)
            names = []
            for i in range(min(n, 64)):
                bcd = pe.u32(arr + 4 * i); td = pe.u32(bcd)
                names.append(pe.cstr(td + 16, 1200))
            return names
        except Exception:
            return None

    def resolve_td(self, td):
        ae, v = self.ae, self.v
        r = ae.rtti()
        nm = r['td_name'].get(td)
        if nm is None: return dict(v17=None, conf='UNRESOLVED', method='', evidence='AE %s is not a TypeDescriptor' % hx(td), cross='na')
        a_all = r['td_by_name'].get(nm, []); v_all = v.rtti()['td_by_name'].get(nm, [])
        if len(a_all) == 1 and len(v_all) == 1:
            return dict(v17=v_all[0], conf='EXACT', method='rtti:typedescriptor', evidence='exact mangled name %s, one TypeDescriptor in each image' % nm.decode(), cross='na')
        if _anon.search(nm):
            an = r['td_by_name_n'].get(norm_anon(nm), []); vn = v.rtti()['td_by_name_n'].get(norm_anon(nm), [])
            if len(an) == 1 and len(vn) == 1 and an[0] == td:
                return dict(v17=vn[0], conf='EXACT', method='rtti:typedescriptor(anon)', evidence='mangled name %s differs between the images only in the anonymous-namespace hash (?A0xHHHHHHHH); the hash-normalized name is a unique TypeDescriptor in each image' % nm.decode(), cross='na')
        return dict(v17=None, conf='UNRESOLVED', method='rtti', evidence='%s: AE %d / 1.7 %d TypeDescriptors' % (nm.decode(), len(a_all), len(v_all)), cross='na')


    # ---- NiRTTI objects (struct { const char* name; const NiRTTI* base; } in .data), EXACT by name string
    def _nirtti_at(self, side, rva):
        """(name bytes, base rva or 0) if rva is a NiRTTI-shaped struct, else None"""
        try:
            pe = side.pe
            namep = pe.u64(rva) - BASE; basep = pe.u64(rva + 8)
            if pe.sec_of(namep) != '.rdata': return None
            nm = pe.cstr(namep, 120)
            if len(nm) < 3 or not re.fullmatch(rb'[A-Za-z_][\w:]*', nm): return None
            if basep != 0 and pe.sec_of(basep - BASE) not in ('.data', '.rdata'): return None
            return nm, (basep - BASE if basep else 0)
        except Exception:
            return None

    def _nirtti_index(self, side):
        if not hasattr(side, '_nir'):
            idx = collections.defaultdict(list)
            for n, va, vs, ro, rs in side.pe.secs:
                if n not in ('.data', '.rdata'): continue
                a64 = np.frombuffer(side.pe.d[ro:ro + (rs // 8) * 8], dtype='<u8')
                lo, hi = [(x[1], x[1] + x[4]) for x in side.pe.secs if x[0] == '.rdata'][0]
                m = np.flatnonzero((a64 >= BASE + lo) & (a64 < BASE + hi))
                for j in m:
                    r = va + 8 * int(j)
                    if r % 8: continue
                    st = self._nirtti_at(side, r)
                    if st and (st[1] == 0 or side.pe.sec_of(st[1]) in ('.data', '.rdata')):
                        b2 = self._nirtti_at(side, st[1]) if st[1] else ('', 0)
                        if st[1] == 0 or b2: idx[st[0]].append(r)
            side._nir = idx
        return side._nir


    # ---- NiRTTI static-init thunks: `lea r8,[base NiRTTI]; lea rdx,["Name"]; lea rcx,[this NiRTTI]; jmp NiRTTI::NiRTTI` (no .pdata,
    # so invisible to the function index).  The name string identifies the NiRTTI object exactly.
    _TH = re.compile(rb'(?:\x4c\x8d\x05(....))?\x48\x8d\x15(....)(?:\x45\x33\xc0)?\x48\x8d\x0d(....)\xe9(....)', re.S)
    def nirtti_thunks(self, side):
        if getattr(side, '_nth', None) is None:
            out = collections.defaultdict(list); t0 = side.pe.text_rva
            for m in self._TH.finditer(side.tb):
                pos = m.start(); p = pos; r8 = None
                if m.group(1): r8 = t0 + pos + 7 + struct.unpack('<i', m.group(1))[0]; p = pos + 7
                rdx = t0 + p + 7 + struct.unpack('<i', m.group(2))[0]; p += 7
                if m.group(0)[p - pos:p - pos + 3] == b'\x45\x33\xc0': p += 3
                rcx = t0 + p + 7 + struct.unpack('<i', m.group(3))[0]; p += 7
                jmp = t0 + p + 5 + struct.unpack('<i', m.group(4))[0]
                nm = side.pe.cstr(rdx, 120) if side.pe.sec_of(rdx) == '.rdata' else b''
                if re.fullmatch(rb'[A-Za-z_][A-Za-z0-9_:]*', nm): out[rcx].append((t0 + pos, nm, r8, jmp))
            byname = collections.defaultdict(list)
            for g, l in out.items():
                for th in l: byname[th[1]].append(g)
            side._nth = (out, byname)
        return side._nth

    def resolve_nirtti_thunk(self, g):
        ae, v = self.ae, self.v
        ta, na = self.nirtti_thunks(ae); tv, nv = self.nirtti_thunks(v)
        l = ta.get(g)
        if not l: return None
        if len(l) != 1: return dict(v17=None, conf='UNRESOLVED', method='nirtti:name-thunk', evidence='AE %s has %d ctor thunks' % (hx(g), len(l)), cross='na')
        th, nm, r8, jmp = l[0]
        ca = na.get(nm, []); cv = nv.get(nm, [])
        if len(ca) != 1 or len(cv) != 1 or ca[0] != g:
            return dict(v17=None, conf='UNRESOLVED', method='nirtti:name-thunk', evidence='name "%s": AE objects %d, 1.7 objects %d (need one each)' % (nm.decode(), len(ca), len(cv)), cross='na')
        tg = cv[0]; thv = tv[tg][0]
        bn_a = ta[r8][0][1] if r8 in ta else None; bn_v = tv[thv[2]][0][1] if thv[2] in tv else None
        if (r8 is None) != (thv[2] is None) or bn_a != bn_v:
            return dict(v17=None, conf='UNRESOLVED', method='nirtti:name-thunk', evidence='name "%s" matches but the base-class NiRTTI differs (AE %r vs 1.7 %r)' % (nm.decode(), bn_a, bn_v), cross='fail')
        jm = self.resolve_start(ae.by_start[jmp]) if jmp in ae.by_start else None
        jn = ' and the NiRTTI::NiRTTI ctor it tail-calls maps to %s' % hx(jm['v17']) if jm and jm['v17'] else ''
        if jm and jm['v17'] and jm['v17'] != thv[3]: jn += ' (DISAGREES with the 1.7 thunk target %s)' % hx(thv[3]); return dict(v17=None, conf='UNRESOLVED', method='nirtti:name-thunk', evidence='ctor target disagrees' + jn, cross='fail')
        return dict(v17=tg, conf='EXACT', method='nirtti:name-thunk',
                    evidence='static-init thunk (lea r8 base, lea rdx "%s", lea rcx this, jmp ctor) is the only one carrying this name in each image (AE thunk %s, 1.7 thunk %s); base-class NiRTTI name %s equal%s' % (nm.decode(), hx(th), hx(thv[0]), ('"%s"' % bn_a.decode()) if bn_a else '(none)', jn), cross='pass')

    def resolve_nirtti(self, rva):
        ae, v = self.ae, self.v
        t = self.resolve_nirtti_thunk(rva)
        if t is not None and t['v17'] is not None: return t
        sa = self._nirtti_at(ae, rva)
        if not sa: return self.resolve_data(rva)      # runtime-initialised NiRTTI object (zero in the file): a plain .data global
        nm, base = sa
        ia = self._nirtti_index(ae).get(nm, []); iv = self._nirtti_index(v).get(nm, [])
        if len(ia) != 1 or len(iv) != 1 or ia[0] != rva:
            return dict(v17=None, conf='UNRESOLVED', method='nirtti:name', evidence='NiRTTI "%s": AE structs %d, 1.7 structs %d (need one each)' % (nm.decode(), len(ia), len(iv)), cross='na')
        sv = self._nirtti_at(v, iv[0])
        ba = self._nirtti_at(ae, base)[0] if base else b''
        bv = self._nirtti_at(v, sv[1])[0] if sv[1] else b''
        cx = 'pass' if ba == bv else 'fail'
        return dict(v17=iv[0], conf='EXACT' if cx == 'pass' else 'UNRESOLVED', method='nirtti:name',
                    evidence='NiRTTI struct {name "%s", base "%s"}: the name string identifies exactly one NiRTTI object in each image; base-class NiRTTI name %s (AE "%s" / 1.7 "%s")' % (nm.decode(), ba.decode() or '-', 'equal' if cx == 'pass' else 'DIFFERS', ba.decode(), bv.decode()), cross=cx)


    # ---- ids with no 1.6.1170 library row: bind the fork's symbol name to TypeDescriptors by name (see symname.py)
    def key_indexes(self):
        if not hasattr(self, '_kidx'):
            c = getattr(self, 'cache_dir', None)
            self._kidx = (symname.td_key_index(self.ae, c and os.path.join(c, 'ae.dem.pkl')), symname.td_key_index(self.v, c and os.path.join(c, 'v17.dem.pkl')))
        return self._kidx

    def vts_of_td(self, side, td):
        r = side.rtti(); out = []
        for c, (nm, off, cd, t) in r['cols'].items():
            if t == td:
                for vt in r['vt_by_col'].get(c, []): out.append((off, vt))
        return sorted(out)

    def hier_of_td(self, side, td):
        for off, vt in self.vts_of_td(side, td)[:1]:
            h = self.hier(side, vt)
            if h is not None: return [norm_anon(x) for x in h]
        return None

    def resolve_by_symbol(self, e):
        """(kind rtti|vtable) -> result or None.  Confidence NAME: the fork symbol must name exactly one TypeDescriptor per image."""
        ia, iv = self.key_indexes(); ex = e.get('extra') or {}
        sym = ex.get('sym')
        if not sym: return None
        k = symname.sym_key(sym); a = ia.get(k, []); b = iv.get(k, [])
        if len(a) == 0 and len(b) == 0:
            return dict(ae_rva=None, v17=None, conf='UNRESOLVED', method='absent:both', evidence='no 1.6.1170 library row, and the class named by %s is not a TypeDescriptor in either binary (older-runtime id; nothing to map)' % sym, cross='na')
        if len(a) != 1 or len(b) != 1:
            return dict(ae_rva=None, v17=None, conf='UNRESOLVED', method='name:symbol', evidence='no 1.6.1170 library row; symbol %s key hits AE %d / 1.7 %d TypeDescriptors (need exactly 1 each)' % (sym, len(a), len(b)), cross='na')
        tda, tdv = a[0], b[0]
        na = self.ae.rtti()['td_name'][tda].decode(errors='replace')
        hint = 'no 1.6.1170 library row (id absent from versionlib-1-6-1170-0); symbol %s names exactly one TypeDescriptor in each image (AE %s, 1.7 %s: %s); NAME confidence relies on the fork symbol labelling this id correctly (2 of 6630 library-known vtable ids have swapped symbols in the fork)' % (sym, hx(tda), hx(tdv), na)
        ha = self.hier_of_td(self.ae, tda); hv = self.hier_of_td(self.v, tdv)
        if e['kind'] == 'rtti':
            if ha is None or hv is None: cx = 'na'; note = 'no vtable/COL to compare class hierarchies'
            elif ha == hv: cx = 'pass'; note = 'class hierarchy identical (%d bases)' % len(ha)
            elif set(ha) <= set(hv): cx = 'pass'; note = 'LAYOUT CHANGE: 1.7.104 adds base(s) to the class hierarchy'
            else: cx = 'fail'; note = 'CLASS HIERARCHY DIFFERS'
            if cx == 'fail': return dict(ae_rva=tda, v17=None, conf='UNRESOLVED', method='name:symbol', evidence='REJECTED %s: %s' % (hx(tdv), note), cross='fail')
            return dict(ae_rva=tda, v17=tdv, conf='NAME', method='name:symbol', evidence=hint + '; ' + note, cross=cx)
        idx = ex.get('idx', 0)
        va = self.vts_of_td(self.ae, tda); vv = self.vts_of_td(self.v, tdv)
        if len(va) != len(vv) or idx >= len(va):
            return dict(ae_rva=None, v17=None, conf='UNRESOLVED', method='name:symbol', evidence=hint + '; vtable subobject count AE %d vs 1.7 %d (idx %d)' % (len(va), len(vv), idx), cross='na')
        if [x[1] for x in va] != sorted(x[1] for x in va) or [x[1] for x in vv] != sorted(x[1] for x in vv):
            return dict(ae_rva=None, v17=None, conf='UNRESOLVED', method='name:symbol', evidence=hint + '; subobject order by this-offset differs from address order, idx not provable', cross='na')
        rva_a = va[idx][1]
        r = self.resolve_vtable(rva_a)
        if r['conf'] != 'EXACT' or r['v17'] != vv[idx][1] or r['cross'] == 'fail':
            return dict(ae_rva=rva_a, v17=None, conf='UNRESOLVED', method='name:symbol', evidence=hint + '; REJECTED: RTTI vtable resolver says %s (%s)' % (hx(r['v17']), r['evidence'][:200]), cross='fail')
        r = dict(r); r['ae_rva'] = rva_a; r['conf'] = 'NAME'; r['method'] = 'name:symbol+rtti:vtable'
        r['evidence'] = hint + '; fork array index %d = subobject #%d by this-offset (offset order == address order in both images; same count %d); ' % (idx, idx, len(va)) + r['evidence']
        return r

    # ---- vtable slot anchors (derived)
    def slot_anchor(self, sa, sv):
        """equal-length RTTI-exact vtable pair: propagate slot-wise when anchored slots agree, return list of new pairs"""
        if len(sa) != len(sv): return []
        pts = 0; bad = 0
        for x, y in zip(sa, sv):
            if x in self.A:
                pts += 1; bad += self.A[x] != y
        if bad or pts < 3: return []
        return [(x, y, pts) for x, y in zip(sa, sv) if x not in self.A]


    # ---- leaf getters: `lea|mov reg,[rip+X]; ret` bodies (no .pdata, so absent from the function index and from refs_in).
    # NiRTTI objects and singletons are typically reached ONLY through such getters (NiObject::GetRTTI virtuals).
    def leaf_getters(self, side):
        if getattr(side, '_leaf', None) is None:
            a = np.frombuffer(side.tb, dtype=np.uint8)
            t0 = side.pe.text_rva
            byt = collections.defaultdict(list); fwd = {}
            for pre, op in ((b'\x48\x8d\x05', 'lea64'), (b'\x48\x8b\x05', 'mov64'), (b'\x8b\x05', 'mov32'), (b'\x0f\xb6\x05', 'movzxb'), (b'\x0f\xb7\x05', 'movzxw')):
                n = len(pre)
                m = np.ones(len(a) - 8, dtype=bool)
                for i, b in enumerate(pre): m &= (a[i:len(a) - 8 + i] == b)
                m &= (a[n + 4:len(a) - 8 + n + 4] == 0xC3)
                for idx in np.flatnonzero(m):
                    idx = int(idx)
                    if idx and a[idx - 1] not in (0xCC, 0xC3, 0x90) and (t0 + idx) % 16: continue
                    rel = struct.unpack_from('<i', side.tb, idx + n)[0]
                    tgt = t0 + idx + n + 4 + rel
                    fwd[t0 + idx] = (op, tgt); byt[tgt].append(t0 + idx)
            side._leaf = (fwd, byt)
        return side._leaf

    def getter_partner(self, la):
        """1.7 leaf getter matching AE leaf getter la, via vtable-slot partner (RTTI-exact equal-count pair, >=3 anchored slots) -> (lv, how) or None"""
        sp = self.slot_partners(3).get(la)
        if sp and len(sp) == 1: return next(iter(sp)), 'vtable-slot partner'
        return None

    def resolve_getter_global(self, g):
        ae, v = self.ae, self.v
        fa, ba = self.leaf_getters(ae); fv, bv = self.leaf_getters(v)
        leaves = ba.get(g, [])
        if not leaves: return None
        votes = collections.defaultdict(list)
        for la in leaves:
            gp = self.getter_partner(la)
            if gp is None: continue
            lv, how = gp
            if lv not in fv or fv[lv][0] != fa[la][0]: continue
            votes[fv[lv][1]].append((la, lv, how))
        if len(votes) != 1: return None
        tgt, sites = next(iter(votes.items()))
        if ae.pe.sec_of(g) != v.pe.sec_of(tgt): return None
        sg = ', '.join('%s->%s' % (hx(a), hx(b)) for a, b, h in sites[:3])
        return dict(v17=tgt, conf='XREF', method='xref:getter-slot',
                    evidence='%d leaf getter(s) `%s [rip+X]; ret` read this address in the 1.6.1170 image (%s); each sits in a vtable slot whose RTTI-exact, equal-slot-count partner (>=3 anchored slots agree, 0 contradictions) is a getter of the same opcode in 1.7.104 reading %s; all agree; section %s kept' % (len(sites), fa[leaves[0]][0], sg, hx(tgt), ae.pe.sec_of(g)),
                    cross='pass')

    # ---- data globals
    def resolve_data(self, ae_rva):
        r = self._resolve_data_voters(ae_rva)
        if r['conf'] == 'UNRESOLVED' and r['cross'] != 'fail':
            g = self.resolve_getter_global(ae_rva)
            if g: return g
        return r

    def _resolve_data_voters(self, ae_rva):
        ae, v = self.ae, self.v
        refs = ae.refs_in.get(ae_rva, [])
        votes = collections.Counter(); voters = []; skipped = 0
        refs = sorted(refs, key=lambda r: r[0] not in self.A)[:300]      # anchored (L1-identical) referencing functions first
        for fs, off in refs:
            if len(voters) >= 12: break
            fa = ae.by_start.get(fs)
            if fa is None: continue
            r = self.resolve_start(fa)
            if r['v17'] is None or r['conf'] == 'UNRESOLVED' or r['cross'] == 'fail': skipped += 1; continue
            fv = v.by_start.get(r['v17'])
            if fv is None: skipped += 1; continue
            tgt = None; how = None
            if fa['l1'] == fv['l1']:
                for o2, t2 in fv['refs']:
                    if o2 == off: tgt = t2; how = 'l1'; break
            elif fa['l2'] == fv['l2'] and len(fa['refs']) == len(fv['refs']):
                idx = [i for i, (o, t) in enumerate(fa['refs']) if o == off]
                if idx: tgt = fv['refs'][idx[0]][1]; how = 'ord'
            else:
                wo = self.window_align(fa, off, fv)
                if wo is not None:
                    for o2, t2 in fv['refs']:
                        if o2 == wo: tgt = t2; how = 'win'; break
            if tgt is None: skipped += 1; continue
            votes[tgt] += 1; voters.append((fs, fa['nins'], r['conf'], how, r['cross']))
        if not votes:
            cand = self.data_block_candidate(ae_rva)
            if cand is not None and len(refs) >= 1 and ae.pe.sec_of(ae_rva) == v.pe.sec_of(cand):
                rv_ = v.refs_in.get(cand, [])
                ha_ = collections.Counter(ae.by_start[f]['l1'] for f, o in refs if f in ae.by_start)
                hv_ = collections.Counter(v.by_start[f]['l1'] for f, o in rv_ if f in v.by_start)
                if len(rv_) == len(refs) and ha_ and ha_ == hv_:
                    return dict(v17=cand, conf='XREF', method='xref:data-block',
                                evidence='the globals that map from L1-anchored functions nearest to it on each side moved by one delta (%+d B, adjacent flanks <= 0x400 B apart, >=3 of the 4 nearest agree), and the predicted 1.7 address has exactly as many referencing sites (%d) in functions whose masked bodies are identical to the AE referencing functions; section %s kept' % (cand - ae_rva, len(refs), ae.pe.sec_of(ae_rva)),
                                    cross='pass')
            return dict(v17=None, conf='UNRESOLVED', method='', evidence='global %s: %d AE referencing sites, none in a mapped function with an alignable body' % (hx(ae_rva), len(refs)), cross='na')
        if len(votes) > 1:
            return dict(v17=None, conf='UNRESOLVED', method='xref:global', evidence='global %s: voters DISAGREE %s' % (hx(ae_rva), {hx(k): c for k, c in votes.items()}), cross='fail')
        tgt, n = next(iter(votes.items()))
        secA = ae.pe.sec_of(ae_rva); secV = v.pe.sec_of(tgt)
        if secA != secV:
            return dict(v17=None, conf='UNRESOLVED', method='xref:global', evidence='global %s -> %s changes section %s->%s' % (hx(ae_rva), hx(tgt), secA, secV), cross='fail')
        big = max(x[1] for x in voters)
        ords = sum(1 for x in voters if x[3] != 'l1')
        gd = self.gdelta(ae_rva) if n == 1 else None
        if n == 1 and gd is not None and tgt - ae_rva == gd and big >= 20:
            return dict(v17=tgt, conf='XREF', method='xref:global+block',
                        evidence='one mapped referencing function (%d insn, aligned by %s) reads the corresponding operand at %s; second independent fact: the two nearest globals on each side that map from L1-anchored functions all moved by %+d B (a data block relocated intact) and this target sits at exactly that delta; section %s kept' % (big, voters[0][3], hx(tgt), gd, secA), cross='pass')
        v0 = voters[0]
        if n >= 2 or (v0[4] == 'pass' and ((v0[3] == 'l1' and big >= 20) or (v0[3] == 'ord' and big >= 30))):
            return dict(v17=tgt, conf='XREF', method='xref:global',
                        evidence='%d mapped referencing function(s) (%d by identical bytes, %d by ordinal in identical instruction shape or by a unique local code window; largest %d insn) read the corresponding rip-relative operand and all agree on %s; section %s kept; AE has %d referencing sites' % (n, n - ords, ords, big, hx(tgt), secA, len(refs)),
                        cross='pass' if n >= 2 else 'na')
        return dict(v17=None, conf='UNRESOLVED', method='xref:global', evidence='global %s: single weak voter (%d insn)' % (hx(ae_rva), big), cross='na')

    # ---- self-test against RTTI-derived vtable slot pairs
    def selftest(self, limit=4000):
        ae, v = self.ae, self.v
        saveA = self.Aall; saveres = self.res
        self.Aall = dict(self.A); self.res = {}; self.use_slot_check = False
        rt_a = ae.rtti()['by_key']; rt_v = v.rtti()['by_key']
        pairs = []
        for key, avs in rt_a.items():
            vvs = rt_v.get(key, [])
            if len(avs) != 1 or len(vvs) != 1: continue
            sa = ae.vt_slots(avs[0]); sv = v.vt_slots(vvs[0])
            if len(sa) != len(sv): continue
            pts = bad = 0
            for x, y in zip(sa, sv):
                if x in self.A: pts += 1; bad += self.A[x] != y
            if bad or pts < 3: continue
            for x, y in zip(sa, sv):
                if x not in self.A and x in ae.by_start: pairs.append((x, y, key[0].decode(errors='replace')))
        seen = set(); uniq = []
        for p in pairs:
            if p[0] in seen: continue
            seen.add(p[0]); uniq.append(p)
        import random
        random.Random(1).shuffle(uniq)
        uniq = uniq[:limit]
        st = collections.defaultdict(collections.Counter); wrong = []
        for x, y, cn in uniq:
            r = self.resolve_start(ae.by_start[x])
            got = r['v17']
            k = 'right' if got == y else ('unresolved' if got is None else 'WRONG')
            st[(r['conf'] + ' ' + r['method']) if got is not None else 'UNRESOLVED'][k] += 1
            if k == 'WRONG': wrong.append((x, y, got, cn, r['method'], r['evidence'][:200]))
        self.Aall = saveA; self.res = saveres; self.use_slot_check = True
        return len(uniq), st, wrong


    # ---- self-test 2: hold known-good anchors out and re-derive them WITHOUT their own bytes (xref methods only)
    def selftest_holdout(self, n=3000, seed=7):
        import random
        ae, v = self.ae, self.v
        save = (self.A, self.Ainv, self.Aall, self.res, getattr(self, '_spc', {}), getattr(self, '_gmap', None), getattr(self, '_gmap1', None))
        truth = dict(self.A)
        pool = sorted(x for x in truth if ae.by_start[x]['nins'] >= 8)
        random.Random(seed).shuffle(pool); held = pool[:n]; hs = set(held)
        self.A = {k: x for k, x in truth.items() if k not in hs}
        self.Ainv = {x: k for k, x in self.A.items()}
        self.Aall = dict(self.A); self.res = {}; self._spc = {}; self._gmap = None
        self.use_slot_check = False; self.no_sig = True
        out = {}
        for rnd in range(3):
            for x in held:
                if x in out and out[x]['conf'] != 'UNRESOLVED': continue
                fa = ae.by_start[x]
                if x in self.res and self.res[x].get('conf') == 'UNRESOLVED': del self.res[x]
                r = self.resolve_start(fa); out[x] = r
                if r['conf'] == 'UNRESOLVED': self.xref_expand(fa)
        st = collections.defaultdict(collections.Counter); wrong = []
        for x in held:
            r = out[x]; got = r['v17']
            k = 'right' if got == truth[x] else ('unresolved' if got is None else 'WRONG')
            st[(r['conf'] + ' ' + r['method']) if got is not None else 'UNRESOLVED'][k] += 1
            if k == 'WRONG': wrong.append((x, truth[x], got, r['method'], r['evidence'][:240]))
        self.A, self.Ainv, self.Aall, self.res, self._spc, self._gmap, self._gmap1 = save
        self.use_slot_check = True; self.no_sig = False
        return len(held), st, wrong


    # ---- self-test 5: getter route vs the byte-alignment voting route on globals both can resolve
    def selftest_getter(self):
        ae, v = self.ae, self.v
        fa, ba = self.leaf_getters(ae)
        cnt = collections.Counter(); wrong = []
        for g in sorted(ba):
            if ae.in_text(g): continue
            r1 = self._resolve_data_voters(g)
            if r1['v17'] is None: continue
            r2 = self.resolve_getter_global(g)
            if r2 is None: cnt['getter route silent'] += 1; continue
            if r2['v17'] == r1['v17']: cnt['agree'] += 1
            else: cnt['DISAGREE'] += 1; wrong.append((g, r1['v17'], r2['v17']))
        return cnt, wrong


    # ---- self-test 6: block lock-step as a PREDICTOR (hide an anchor, predict it from its flanks, compare with its L1 truth)
    def selftest_lockstep(self, n=20000, seed=3):
        import random
        keys = sorted(self.A); sample = keys[:]; random.Random(seed).shuffle(sample); sample = sample[:n]
        cnt = collections.Counter(); wrong = []
        for x in sample:
            d = self.lockstep_delta(x)
            if d is None: cnt['no prediction'] += 1; continue
            if x + d == self.A[x]: cnt['right'] += 1
            else: cnt['WRONG'] += 1; wrong.append((x, self.A[x], x + d))
        return len(sample), cnt, wrong


    def selftest_nirtti(self, entries, lib):
        cnt = collections.Counter(); wrong = []
        for e in entries:
            if e['kind'] != 'nirtti': continue
            g = lib.id2rva(e['ae_id'])
            if g is None: continue
            t = self.resolve_nirtti_thunk(g)
            r1 = self._resolve_data_voters(g); r2 = self.resolve_getter_global(g)
            for nm, r in (('voting', r1), ('getter-slot', r2)):
                if r is None or r.get('v17') is None: continue
                if t is None or t['v17'] is None: cnt['%s resolved, name-thunk silent' % nm] += 1
                elif t['v17'] == r['v17']: cnt['name-thunk agrees with %s' % nm] += 1
                else: cnt['name-thunk DISAGREES with %s' % nm] += 1; wrong.append((g, nm, r['v17'], t['v17']))
        return cnt, wrong


    # ---- self-test 7: data-block predictor + corroboration, on globals the voting route resolves (hidden from the flanks)
    def selftest_datablock(self, n=6000, seed=5):
        import random
        ae, v = self.ae, self.v
        self.build_gmap(); g = self._gmap
        keys = sorted(g); random.Random(seed).shuffle(keys); cnt = collections.Counter(); wrong = []
        for t in keys[:n]:
            cand = self.data_block_candidate(t, exclude=(t,))
            if cand is None: cnt['no prediction'] += 1; continue
            refs = ae.refs_in.get(t, []); rv_ = v.refs_in.get(cand, [])
            ha_ = collections.Counter(ae.by_start[f]['l1'] for f, o in refs if f in ae.by_start)
            hv_ = collections.Counter(v.by_start[f]['l1'] for f, o in rv_ if f in v.by_start)
            if not (len(rv_) == len(refs) and ha_ and ha_ == hv_): cnt['prediction rejected by corroboration'] += 1; continue
            if cand == g[t]: cnt['right'] += 1
            else: cnt['WRONG'] += 1; wrong.append((t, g[t], cand))
        return min(n, len(keys)), cnt, wrong

    # ---- self-test 3: .rdata string globals, truth = identical string content (independent of the byte-alignment voting)
    def selftest_strings(self, n=2500, seed=11):
        import random
        ae, v = self.ae, self.v
        gs = {}
        for fs in self.A:
            for off, t in ae.by_start[fs]['refs']:
                c = ae.cstr_at(t)
                if c: gs[t] = c
        vt = collections.defaultdict(set)
        for f in v.funcs:
            for off, t in f['refs']:
                c = v.cstr_at(t)
                if c: vt[c].add(t)
        ac = collections.Counter(gs.values())
        cand = sorted(t for t, c in gs.items() if ac[c] == 1 and len(vt.get(c, ())) == 1)
        random.Random(seed).shuffle(cand); cand = cand[:n]
        cnt = collections.Counter(); wrong = []
        for t in cand:
            r = self.resolve_data(t); tru = next(iter(vt[gs[t]]))
            k = 'right' if r['v17'] == tru else ('unresolved' if r['v17'] is None else 'WRONG')
            cnt[k] += 1
            if k == 'WRONG': wrong.append((t, tru, r['v17'], gs[t][:40]))
        return len(cand), cnt, wrong

# ------------------------------------------------------------------ ground truth
def parse_truth(md_path):
    pairs = []   # (ae, v17, note)
    hexre = re.compile(r'`0x([0-9A-Fa-f]{4,8})`')
    cols = None
    lines = open(md_path, errors='ignore').read().split('\n')
    for n, ln in enumerate(lines):
        if not ln.startswith('|'):
            cols = None; continue
        cells = [c.strip() for c in ln.strip().strip('|').split('|')]
        nxt = lines[n + 1] if n + 1 < len(lines) else ''
        if nxt.startswith('|') and set(nxt.strip()) <= set('|-: '):      # this row is a table header
            low = [c.lower() for c in cells]
            cols = None
            if any('1.7.104' in c for c in low) and any(('1.6.1170' in c) or c.startswith('ae') for c in low):
                ai = next(i for i, c in enumerate(low) if '1.6.1170' in c or c.startswith('ae'))
                vi = next(i for i, c in enumerate(low) if '1.7.104' in c)
                cols = (ai, vi)
            continue
        if cols is None or set(ln.strip()) <= set('|-: '): continue
        ai, vi = cols
        if len(cells) <= max(ai, vi): continue
        a = set(hexre.findall(cells[ai].split('**')[0])); b = set(hexre.findall(cells[vi].split('**')[0]))
        if len(a) == 1 and len(b) == 1:
            av = int(next(iter(a)), 16); bv = int(next(iter(b)), 16)
            if av >= 0x1000 and bv >= 0x1000: pairs.append((av, bv, cells[0][:60]))
    return pairs

TRUTH_EXTRA = [   # explicit values stated in prose / multi-value cells of ADDRESS-TABLE-2026-09-15.md
    (0xCD4F70, 0xCEF3B0, 'PollInputDevices +0x53 target (row 61)'),
    (0xCD42E0, 0xCEE700, 'PollInputDevices +0x5B target (row 61)'),
    (0xCD9E00, 0xCFA650, 'PollInputDevices +0x7B target (row 61)'),
    (0xCDACA0, 0xCFBA10, 'PollInputDevices +0x87 target (row 61)'),
    (0xCD4680, 0xCEEAA0, 'ControlMap ctor (row 99)'),
    (0x81E020, 0x833510, 'CombatMagicCaster::GetMagicTarget (row 101)'),
    (0x819D30, 0x82EC10, 'combat-style score-mult helper id 45085 (row 101)'),
]

# ------------------------------------------------------------------ driver
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ids', required=True)
    ap.add_argument('--ae-lib', default=AE_LIB_DEFAULT)
    ap.add_argument('--out-dir', required=True)
    ap.add_argument('--truth', default='/mnt/gaming/modlists/Projects/ai-package-management-framework/Docs/ADDRESS-TABLE-2026-09-15.md')
    ap.add_argument('--cache', default=None)
    ap.add_argument('--no-selftest', action='store_true', help='skip self-tests 1-6 (development runs)')
    ap.add_argument('--tag', default='1.7.104', help='output file tag (idmap-<tag>.csv ...)')
    a = ap.parse_args()
    out = a.out_dir; cache = a.cache or os.path.join(out, 'cache'); os.makedirs(out, exist_ok=True)
    log = lambda *x: print(*x, flush=True)
    t0 = time.time()
    lib = addrlib.DB(a.ae_lib)
    assert lib.version[:3] == (1, 6, 1170), lib.version
    assert lib.id2rva(38048) == 0x6A35F0, 'wrong AE library build (need versionlib-1-6-1170-0.bin, not -0-1)'
    log('AE lib ok: %d ids, version %s' % (len(lib.map), lib.version))
    ae = Side('ae', AE_EXE, os.path.join(cache, 'ae.funcs.pkl'))
    v17 = Side('v17', V17_EXE, os.path.join(cache, 'v17.funcs.pkl'))
    log('indexed: ae %d funcs, v17 %d funcs (%.0fs)' % (len(ae.funcs), len(v17.funcs), time.time() - t0))
    M = Mapper(ae, v17, lib, log); M.cache_dir = cache
    entries = json.load(open(a.ids))
    results = {}   # (kind, key) -> result

    # ---- phase 1: vtables / RTTI
    log('phase 1: RTTI')
    slotpairs = []
    for e in entries:
        if e['kind'] not in ('vtable', 'rtti', 'nirtti'): continue
        rva = lib.id2rva(e['ae_id'])
        k = (e['kind'], e['ae_id'])
        if rva is None:
            r = M.resolve_by_symbol(e) if e['kind'] in ('vtable', 'rtti') else None
            results[k] = r or dict(ae_rva=None, v17=None, conf='UNRESOLVED', method='', evidence='id not in 1.6.1170 library', cross='na'); continue
        r = M.resolve_vtable(rva) if e['kind'] == 'vtable' else (M.resolve_nirtti(rva) if e['kind'] == 'nirtti' else M.resolve_td(rva))
        r['ae_rva'] = rva; results[k] = r
        if 'slots' in r: slotpairs.append((r['slots'], k))
    # ---- phase 2: functions by signature
    log('phase 2: signatures')
    for e in entries:
        if e['kind'] not in ('id', 'rva'): continue
        rva = lib.id2rva(e['ae_id']) if e['kind'] == 'id' else e['ae_rva']
        k = (e['kind'], e['ae_id'] if e['kind'] == 'id' else e['ae_rva'])
        if rva is None: results[k] = dict(ae_rva=None, v17=None, conf='UNRESOLVED', method='', evidence='id not in 1.6.1170 library', cross='na'); continue
        if ae.in_text(rva): continue   # functions after vtable slot anchors
        if ae.pe.sec_of(rva) == '.rdata' and M.ae_vtable_key(rva):
            r = M.resolve_vtable(rva)
            if 'slots' in r: slotpairs.append((r['slots'], k))
        else:
            r = M.resolve_data(rva)
        r['ae_rva'] = rva; results[k] = r
    # slot anchors (derived)
    nslot = 0
    for (sa, sv, pts), k in slotpairs:
        for x, y, p in M.slot_anchor(sa, sv):
            if x not in M.Aall: M.Aall[x] = y; nslot += 1
    log('derived slot anchors: %d' % nslot)
    todo = [(e, lib.id2rva(e['ae_id']) if e['kind'] == 'id' else e['ae_rva']) for e in entries if e['kind'] in ('id', 'rva')]
    todo = [(e, r) for e, r in todo if r is not None and ae.in_text(r)]
    for rnd in range(3):
        un = 0
        for e, rva in todo:
            k = (e['kind'], e['ae_id'] if e['kind'] == 'id' else e['ae_rva'])
            if k in results and results[k]['conf'] != 'UNRESOLVED': continue
            # reset memo for unresolved (new anchors may help)
            fa, off = M.ae_func(rva)
            if fa is not None and fa['start'] in M.res and M.res[fa['start']].get('conf') == 'UNRESOLVED': del M.res[fa['start']]
            r = M.resolve_function(rva); r = dict(r); r['ae_rva'] = rva; results[k] = r
            if r['conf'] == 'UNRESOLVED':
                un += 1
                if fa is not None: M.xref_expand(fa)
        log('  round %d: unresolved functions %d' % (rnd, un))
    # vtable-slot derived resolution for remaining functions
    for e, rva in todo:
        k = (e['kind'], e['ae_id'] if e['kind'] == 'id' else e['ae_rva'])
        if results[k]['conf'] != 'UNRESOLVED': continue
        fa, off = M.ae_func(rva)
        if fa is None or off != 0: continue
        if fa['start'] in M.Aall and fa['start'] not in M.A:
            fv = v17.by_start.get(M.Aall[fa['start']])
            if fv:
                cx, pts, det = M.crosscheck(fa, fv)
                if cx != 'fail':
                    results[k] = dict(ae_rva=rva, v17=fv['start'], conf='XREF', method='xref:vtable-slot-or-graph',
                                      evidence='derived anchor (equal-length RTTI-exact vtable pair with >=3 L1-anchored slots agreeing and 0 contradictions, or neighbour graph); ' + det, cross=cx)
    # call-site verification for ids that carry a call offset (APMF EquipSink kSitesAE: E8 at +off inside the function)
    for e in entries:
        if e['kind'] != 'id' or not (e.get('extra') or {}).get('call_off'): continue
        k = ('id', e['ae_id']); r = results.get(k)
        if not r or r['v17'] is None: continue
        off = e['extra']['call_off']; rva = r['ae_rva']
        fa = ae.by_start.get(rva); fv = v17.by_start.get(r['v17'])
        if not fa or not fv: continue
        ta = [t for o, t, m in fa['calls'] if o == off]; tv = [t for o, t, m in fv['calls'] if o == off]
        if ta and tv:
            ra = M.resolve_function(ta[0])
            ok = ra['v17'] == tv[0]
            r['evidence'] += '; call-site +0x%X: AE E8 -> %s, 1.7 E8 -> %s (callee mapping %s)' % (off, hx(ta[0]), hx(tv[0]), 'AGREES' if ok else 'DISAGREES' if ra['v17'] else 'callee unmapped')
            if ra['v17'] and not ok: r['cross'] = 'fail'
        else:
            r['evidence'] += '; call-site +0x%X: no E8 at that offset in %s' % (off, 'AE' if not ta else '1.7')
            r['cross'] = 'fail'
    # delta hints
    for k, r in results.items():
        if r.get('v17') is not None and r['ae_rva'] is not None and ae.in_text(r['ae_rva']):
            h = M.delta_hint(r['ae_rva'], r['v17'])
            if h is not None: r['evidence'] += '; neighbour-delta hint %+d B (hint only)' % h

    # ---- ground truth
    log('ground truth')
    truth = parse_truth(a.truth) + TRUTH_EXTRA
    seen = set(); gt = []
    for av, bv, note in truth:
        if (av, bv) in seen: continue
        seen.add((av, bv))
        if ae.in_text(av):
            r = M.resolve_function(av)
        else:
            r = M.resolve_vtable(av) if (ae.pe.sec_of(av) == '.rdata' and M.ae_vtable_key(av)) else M.resolve_data(av)
        got = r.get('v17')
        st = 'HIT' if got == bv else ('UNRESOLVED' if got is None else 'MISMATCH')
        gt.append(dict(ae=av, expect=bv, got=got, status=st, conf=r['conf'], method=r.get('method', ''), cross=r['cross'], note=note, evidence=r['evidence']))
    n = len(gt); hit = sum(1 for g in gt if g['status'] == 'HIT'); mis = [g for g in gt if g['status'] == 'MISMATCH']; unr = [g for g in gt if g['status'] == 'UNRESOLVED']
    log('ground truth pairs %d: HIT %d MISMATCH %d UNRESOLVED %d' % (n, hit, len(mis), len(unr)))
    json.dump(gt, open(os.path.join(out, 'groundtruth-%s.json' % a.tag), 'w'), indent=1)
    with open(os.path.join(out, 'groundtruth-%s.csv' % a.tag), 'w', newline='') as fh:
        w = csv.writer(fh); w.writerow(['rva_1_6_1170', 'expected_1_7_104', 'got_1_7_104', 'status', 'confidence', 'method', 'crosscheck', 'note', 'evidence'])
        for g in gt: w.writerow([hx(g['ae']), hx(g['expect']), hx(g['got']), g['status'], g['conf'], g['method'], g['cross'], g['note'], g['evidence']])

    if a.no_selftest:
        stl = ['(self-tests skipped: --no-selftest)']
    else:
        log('selftest vs RTTI vtable-slot pairs')
        nt, st, wrong = M.selftest()
        stl = ['## self-test: signature/xref resolver vs RTTI-exact vtable slot pairs (%d AE slot functions, not used as anchors)' % nt, '']
        for m, c in sorted(st.items()): stl.append('- %s: right %d, WRONG %d, unresolved %d' % (m, c['right'], c['WRONG'], c['unresolved']))
        for w in wrong[:40]: stl.append('  - WRONG AE %s expect %s got %s (%s) %s :: %s' % (hx(w[0]), hx(w[1]), hx(w[2]), w[3][:40], w[4], w[5]))
        # self-test 2: anchor hold-out
        t1 = time.time()
        nh, sth, wrh = M.selftest_holdout()
        stl += ['', '## self-test 2: %d L1-anchored functions held out, re-derived by xref methods only (own bytes unusable; held-out set removed from the anchors)' % nh, '']
        for m, c in sorted(sth.items()): stl.append('- %s: right %d, WRONG %d, unresolved %d' % (m, c['right'], c['WRONG'], c['unresolved']))
        for w in wrh[:40]: stl.append('  - WRONG AE %s expect %s got %s (%s) :: %s' % (hx(w[0]), hx(w[1]), hx(w[2]), w[3], w[4]))
        # self-test 5: getter route cross-validated against the voting route
        cg, wg = M.selftest_getter()
        stl += ['', '## self-test 5: leaf-getter (vtable-slot) route vs byte-alignment voting route, on globals both can resolve', '']
        for k2, c2 in sorted(cg.items()): stl.append('- %s: %d' % (k2, c2))
        for w in wg[:20]: stl.append('  - DISAGREE AE %s: voting %s getter %s' % (hx(w[0]), hx(w[1]), hx(w[2])))
        nl, cl_, wl = M.selftest_lockstep()
        stl += ['', '## self-test 6: block lock-step predictor on %d L1 anchors (flanks computed without the anchor itself)' % nl, '']
        for k2, c2 in sorted(cl_.items()): stl.append('- %s: %d' % (k2, c2))
        for w in wl[:10]: stl.append('  - WRONG AE %s truth %s predicted %s' % (hx(w[0]), hx(w[1]), hx(w[2])))
        cnn, wnn = M.selftest_nirtti(entries, lib)
        stl += ['', '## self-test 5b: NiRTTI name-thunk route vs the independent byte-voting / getter-slot routes', '']
        for k2, c2 in sorted(cnn.items()): stl.append('- %s: %d' % (k2, c2))
        for w in wnn[:20]: stl.append('  - DISAGREE AE %s %s says %s thunk says %s' % (hx(w[0]), w[1], hx(w[2]), hx(w[3])))
        nd, cdb, wdb = M.selftest_datablock()
        stl += ['', '## self-test 7: data-block predictor + corroboration (same referencing-site count and identical referencing bodies) on %d globals, each hidden from its own flanks' % nd, '']
        for k2, c2 in sorted(cdb.items()): stl.append('- %s: %d' % (k2, c2))
        for w in wdb[:10]: stl.append('  - WRONG AE %s truth %s predicted %s' % (hx(w[0]), hx(w[1]), hx(w[2])))
        # self-test 3: string globals
        ns, cs, wrs = M.selftest_strings()
        stl += ['', '## self-test 3: %d .rdata string globals, truth = identical string content (unique in both images), resolved by xref:global' % ns, '',
                '- right %d, WRONG %d, unresolved %d' % (cs['right'], cs['WRONG'], cs['unresolved'])]
        for w in wrs[:20]: stl.append('  - WRONG AE %s expect %s got %s %r' % (hx(w[0]), hx(w[1]), hx(w[2]), w[3]))
        # self-test 4: name-binding hold-out (known library ids re-derived from the fork symbol alone)
        cn = collections.Counter(); wrn = []
        for e in entries:
            if e['kind'] not in ('vtable', 'rtti'): continue
            rv = lib.id2rva(e['ae_id'])
            if rv is None: continue
            r = M.resolve_by_symbol(e)
            if r is None or r.get('v17') is None and r['conf'] == 'UNRESOLVED' and r['method'] in ('name:symbol', 'absent:both') and r.get('ae_rva') is None: cn[e['kind'] + ' nomatch/ambiguous'] += 1; continue
            main_r = results.get((e['kind'], e['ae_id']))
            ok = r.get('ae_rva') == rv
            cn[e['kind'] + (' right' if ok else ' WRONG')] += 1
            if not ok: wrn.append((e['ae_id'], (e.get('extra') or {}).get('sym'), hx(rv), hx(r.get('ae_rva'))))
        stl += ['', '## self-test 4: library-known RTTI/VTABLE ids re-derived from the fork symbol name alone (name:symbol binding vs library row)', '']
        for k2, c2 in sorted(cn.items()): stl.append('- %s: %d' % (k2, c2))
        for w in wrn: stl.append('  - id %d %s: library says %s, name says %s' % w)
        log('selftests 2-4 %.0fs' % (time.time() - t1))
        log('\n'.join(stl[:14]))
    if not (a.no_selftest and os.path.exists(os.path.join(out, 'selftest-%s.md' % a.tag))):
        open(os.path.join(out, 'selftest-%s.md' % a.tag), 'w').write('\n'.join(stl) + '\n')

    # ---- emit
    log('emit')
    import final_states
    rows = []
    for e in entries:
        k = (e['kind'], e['ae_id'] if e['kind'] in ('id', 'vtable', 'rtti', 'nirtti') else e['ae_rva'])
        r = results.get(k)
        if r is None: continue
        ident = ('%d' % e['ae_id']) if e['ae_id'] is not None else 'rva:%s' % hx(e['ae_rva'])
        rows.append(dict(ident=ident, kind=e['kind'], scope='+'.join(e['scopes']), uses=' ; '.join(e['uses'])[:400], ae_rva=r['ae_rva'], v17=r.get('v17'),
                         conf=r['conf'], method=r.get('method', ''), evidence=r['evidence'], cross=r['cross'], sym=(e.get('extra') or {}).get('sym'), ae_id=e['ae_id']))
    # INLINED probe for BSShaderTextureSet::Ctor (REL::ID 99886): which functions store the class vtable?
    try:
        vt = [r for r in rows if r['sym'] == 'VTABLE_BSShaderTextureSet' and r['v17'] and r['ae_rva']]
        if vt:
            va_, vv_ = vt[0]['ae_rva'], vt[0]['v17']
            ra = sorted((ae.by_start[f]['size'], f) for f, o in ae.refs_in.get(va_, []) if f in ae.by_start)
            rv = sorted((v17.by_start[f]['size'], f) for f, o in v17.refs_in.get(vv_, []) if f in v17.by_start)
            if ra and rv and min(ra)[0] >= 200 and len(ra) == len(rv):
                for r in rows:
                    if r['ident'] == '99886':
                        r['inlined'] = ('the only functions that store VTABLE_BSShaderTextureSet (AE %s, 1.7 %s) are %d in 1.6.1170 (%s) and %d in 1.7.104 (%s); none is a standalone constructor (smallest is %d B in AE, %d B in 1.7.104): the 209-byte pair are the allocate-and-construct bodies of BSShaderTextureSet::Create, so the constructor body was absorbed into its callers; the REL::ID(99886) standalone constructor exists only in older AE libraries (see lib presence)'
                                        % (hx(va_), hx(vv_), len(ra), ' '.join('%s(%dB)' % (hx(f), sz) for sz, f in ra), len(rv), ' '.join('%s(%dB)' % (hx(f), sz) for sz, f in rv), min(ra)[0], min(rv)[0]))
    except Exception as ex_:
        log('inline probe failed: %r' % (ex_,))
    cls = final_states.classify(rows, M, lib, ae, v17, symname.demangle_tds(ae.rtti()['td_name'].values(), os.path.join(cache, 'ae.dem.pkl')), symname.demangle_tds(v17.rtti()['td_name'].values(), os.path.join(cache, 'v17.dem.pkl')), log)
    log('final states: %s' % dict(cls))
    def category(r):
        if r['kind'] == 'rtti': return 'RTTI TypeDescriptors'
        if r['kind'] == 'vtable': return 'vtables'
        if r['kind'] == 'nirtti': return 'NiRTTI objects (.data globals)'
        if r['ae_rva'] is None: return 'functions/globals with no 1.6.1170 row'
        if ae.in_text(r['ae_rva']): return 'functions'
        if ae.pe.sec_of(r['ae_rva']) == '.rdata' and M.ae_vtable_key(r['ae_rva']): return 'vtables'
        return 'globals/data'
    for r in rows: r['cat'] = category(r)
    with open(os.path.join(out, 'idmap-%s.csv' % a.tag), 'w', newline='') as fh:
        w = csv.writer(fh); w.writerow(['id', 'kind', 'scope', 'name_use_site', 'rva_1_6_1170', 'rva_1_7_104', 'confidence', 'method', 'evidence', 'crosscheck', 'final_state', 'final_evidence', 'category'])
        for r in rows: w.writerow([r['ident'], r['kind'], r['scope'], r['uses'], hx(r['ae_rva']), hx(r['v17']), r['conf'], r['method'], r['evidence'], r['cross'], r['final'], r['final_ev'], r['cat']])
    # summary
    summ = collections.defaultdict(collections.Counter)
    for r in rows: summ[r['scope']][r['conf']] += 1
    lines = ['# idmap 1.7.104 summary', '', 'ground truth: %d pairs, HIT %d, MISMATCH %d, UNRESOLVED %d' % (n, hit, len(mis), len(unr)), '']
    lines.append('| scope | EXACT | NAME | UNIQUE-SIG | XREF | UNRESOLVED | total |'); lines.append('|---|---|---|---|---|---|---|')
    for sc, c in sorted(summ.items()):
        lines.append('| %s | %d | %d | %d | %d | %d | %d |' % (sc, c['EXACT'], c['NAME'], c['UNIQUE-SIG'], c['XREF'], c['UNRESOLVED'], sum(c.values())))
    tot = collections.Counter(); [tot.update(c) for c in summ.values()]
    lines.append('| ALL | %d | %d | %d | %d | %d | %d |' % (tot['EXACT'], tot['NAME'], tot['UNIQUE-SIG'], tot['XREF'], tot['UNRESOLVED'], sum(tot.values())))
    lines += ['', '## final states (MAPPED / REMOVED / INLINED / HARD)', '', '| category | total | MAPPED | REMOVED | INLINED | HARD |', '|---|---|---|---|---|---|']
    bycat = collections.defaultdict(collections.Counter)
    for r in rows: bycat[r['cat']][r['final']] += 1
    for cname, c in sorted(bycat.items()):
        lines.append('| %s | %d | %d | %d | %d | %d |' % (cname, sum(c.values()), c['MAPPED'], c['REMOVED'], c['INLINED'], c['HARD']))
    ft = collections.Counter(r['final'] for r in rows)
    lines.append('| ALL | %d | %d | %d | %d | %d |' % (len(rows), ft['MAPPED'], ft['REMOVED'], ft['INLINED'], ft['HARD']))
    lines += ['', '## mapped rows by category and method', '', '| category | method | rows |', '|---|---|---|']
    cm = collections.Counter((r['cat'], r['conf'] + ' ' + r['method']) for r in rows if r['final'] == 'MAPPED')
    for (cname, m), c in sorted(cm.items()): lines.append('| %s | %s | %d |' % (cname, m, c))
    lines += ['', '## crosscheck tally (mapped rows only)']
    cc = collections.Counter(r['cross'] for r in rows if r['v17'] is not None); lines.append(str(dict(cc)))
    lines += ['', '## layout flags (class hierarchy / slot layout changes seen on exact-RTTI matches)']
    for r in rows:
        e_ = r['evidence']
        if 'LAYOUT CHANGE' in e_ or 'HIERARCHY DIFFERS' in e_ or 'SLOT CONTRADICTION' in e_:
            idx = min([x for x in (e_.find('LAYOUT CHANGE'), e_.find('CLASS HIERARCHY'), e_.find('SLOT CONTRADICTION')) if x >= 0])
            lines.append('- %s %s %s :: %s' % (r['ident'], r['uses'][:60], hx(r['v17']), e_[idx:][:260]))
    lines += ['', '## ground-truth mismatches (tool bugs to investigate)']
    for g in mis: lines.append('- AE %s expect %s got %s [%s] %s :: %s' % (hx(g['ae']), hx(g['expect']), hx(g['got']), g['conf'], g['note'], g['evidence'][:300]))
    lines += ['', '## ground-truth unresolved']
    for g in unr: lines.append('- AE %s expect %s [%s] :: %s' % (hx(g['ae']), hx(g['expect']), g['note'], g['evidence'][:200]))
    lines += ['', '## HARD ids (handed back)']
    for r in rows:
        if r['final'] == 'HARD': lines.append('- %s %s %s :: %s :: %s' % (r['ident'], r['kind'], r['scope'], r['uses'][:90], r['final_ev'][:500]))
    lines += ['', '## INLINED ids']
    for r in rows:
        if r['final'] == 'INLINED': lines.append('- %s %s :: %s :: %s' % (r['ident'], r['kind'], r['uses'][:90], r['final_ev'][:700]))
    lines += ['', '## REMOVED ids by reason']
    rc = collections.Counter(r['final_ev'][:60] for r in rows if r['final'] == 'REMOVED')
    for kx, c in rc.most_common(8): lines.append('- %d x %s...' % (c, kx))
    open(os.path.join(out, 'summary-%s.md' % a.tag), 'w').write('\n'.join(lines) + '\n')
    log('\n'.join(lines[:40]))
    log('done %.0fs' % (time.time() - t0))

if __name__ == '__main__':
    main()
