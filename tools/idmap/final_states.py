#!/usr/bin/env python3
"""final_states.py -- every id ends as MAPPED / REMOVED / INLINED / HARD, with evidence (marth 2026-10-04: UNRESOLVED is not a final state).

  MAPPED   accepted match (EXACT / NAME / UNIQUE-SIG / XREF, crosscheck not failed); evidence is the row's own.
  REMOVED  the id (or the thing it names) does not exist in the 1.7.104 image: evidence = older Address Library presence, absence from
           the 1.6.1170 library and the TypeDescriptor tables of BOTH executables (exact key and loose key), nearest remaining names.
  INLINED  a function that no longer exists as a standalone body because it was absorbed into callers: evidence = the surviving
           referencers (e.g. the only functions that store the owning class's vtable) and why none of them is the standalone body.
  HARD     could not be classified: the methods tried are listed and the row is handed back.
"""
import re, os, glob, collections, sys
sys.path.insert(0, '/mnt/gaming/modlists/Projects/Linux-Native-Tools/tools/steamstub-rtti')
import addrlib

LIBDIR = '/mnt/gaming/modlists/Tuxborn/mods/Address Library for SKSE Plugins/SKSE/Plugins/'

def load_libs():
    libs = {}
    for n in sorted(glob.glob(LIBDIR + 'versionlib-1-6-*.bin')):
        k = os.path.basename(n)[len('versionlib-'):-4]
        if k == '1-6-1170-0-1': continue
        libs[k] = addrlib.DB(n)
    return libs

def presence(libs, i):
    pr = [k for k, l in libs.items() if l.id2rva(i) is not None]
    return pr

USE = {   # how the fork source uses the stale id (grep of fork tip fde0f3ae)
    69188: 'used: src/RE/B/BSScaleformTranslator.cpp:8 (Offset::BSScaleformTranslator::GetCachedString)',
    11044: 'declared in Offsets.h only; no use anywhere in fork src/include',
    405935: 'used: src/RE/C/Console.cpp:15 (Console::GetSelectedRefHandle reads the ObjectRefHandle global)',
    21890: 'used: src/RE/S/Script.cpp:59 (Script::CompileAndRun_Impl)',
}

def lib_evidence(libs, i):
    pr = presence(libs, i)
    if pr: return 'present in Address Library %s, absent in %s' % (','.join(p.replace('-', '.')[:-2] for p in pr), ','.join(k.replace('-', '.')[:-2] for k in libs if k not in pr))
    return 'absent from every locally available AE library (%s)' % ','.join(k.replace('-', '.')[:-2] for k in libs)

def nearest_names(dem, loose, sym, n=2):
    toks = [w for w in re.split(r'_+', sym.split('_', 1)[1]) if len(w) >= 8 and not w.startswith('lambda')] if '_' in sym else []
    if not toks: return []
    w = max(toks, key=len)
    return [d[:90] for d in dem.values() if w in d][:n]

def classify(rows, M, lib, ae, v, dem_a, dem_v, log=print):
    libs = load_libs()
    cls = collections.Counter()
    for r in rows:
        k = r['kind']; conf = r['conf']
        if conf in ('EXACT', 'NAME', 'UNIQUE-SIG', 'XREF') and r.get('cross') != 'fail' and r['v17'] is not None:
            r['final'] = 'MAPPED'; r['final_ev'] = ''; cls['MAPPED'] += 1; continue
        ev = r['evidence']; meth = r.get('method', '')
        ident = r['ident']; sym = r.get('sym') or ''
        if meth == 'absent:both':
            i = int(ident)
            r['final'] = 'REMOVED'
            near = nearest_names(dem_a, None, sym) + nearest_names(dem_v, None, sym)
            r['final_ev'] = '%s (so %s is not in the 1.6.1130/1170/1179 libraries); the class named by %s is not a TypeDescriptor in the 1.6.1170 or the 1.7.104 executable%s' % (
                lib_evidence(libs, i), 'it' , sym, ('; nearest names sharing its longest token: ' + ' | '.join(near[:2])) if near else '; no TypeDescriptor in either image even shares its longest name token')
        elif k in ('rtti', 'vtable') and re.search(r'AE (\d+) / 1\.7 0 TypeDescriptors|AE vtables 1, 1\.7 vtables 0|key hits AE 1 / 1\.7 0', ev):
            r['final'] = 'REMOVED'
            near = nearest_names(dem_v, None, sym)
            r['final_ev'] = 'the class is a TypeDescriptor in the 1.6.1170 executable but there is none with that name (nor its hash-normalized or lambda-hash-normalized form) in the 1.7.104 executable%s' % (('; 1.7.104 names sharing its longest token: ' + ' | '.join(near)) if near else '')
        elif k == 'id' and ev == 'id not in 1.6.1170 library':
            i = int(ident)
            if i == 99886 and r.get('inlined'):
                r['final'] = 'INLINED'; r['final_ev'] = r['inlined']
            else:
                r['final'] = 'HARD'
                r['final_ev'] = 'tried: (1) %s: a retired id, so there is no 1.6.1170 RVA to start the 1.6.1170->1.7.104 ladder from; (2) the fork SE column id is present in the 1.6.1170 library but maps to unrelated code (149 fork pairs with both ids present never share an RVA); (3) no RTTI, string or vtable tie names the function; (4) older libraries give 1.6.317-659 RVAs of binaries we do not hold. Fork use: %s. Needs a replacement id for this function from the fork owner' % (lib_evidence(libs, i), USE.get(i, 'not checked'))
        elif k == 'vtable' and 'names exactly one TypeDescriptor in each image' in ev and 'subobject count AE 0 vs 1.7 0' in ev:
            r['final'] = 'HARD'
            r['final_ev'] = 'the class TypeDescriptor is unique in both images (std::bad_weak_ptr, CRT class) but NEITHER executable has a CompleteObjectLocator or vtable pointer for it (no RTTI vtable), so the RTTI/NAME routes have nothing to bind; tried: COL scan, TypeDescriptor-by-name, no 1.6.1170 library row to start the ladder from. Needs the vtable located by its constructor/throw-info users'
        else:
            r['final'] = 'HARD'; r['final_ev'] = 'tried: ladder (sig, shape, prefix, identical-caller, graph, fuzzy, block lock-step, table lock-step, data-block, getter-slot); last evidence: ' + ev[:300]
        cls[r['final']] += 1
    return cls
