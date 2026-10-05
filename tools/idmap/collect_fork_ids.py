#!/usr/bin/env python3
"""collect_fork_ids.py -- enumerate EVERY Address Library id (AE column = 1.6.1170 ids) the MIT CommonLib fork references.

Reads a checkout of the fork (default: `git archive` of the fork tip into a scratch dir; pass --fork DIR).
Forms scanned (comments stripped, whole file, multi-line safe) over include/ and src/:
  REL::ID(n)                          -> n
  RELOCATION_ID(se, ae) / REL::RelocationID(se, ae[, vr])  -> ae
  REL::VariantID(se, ae, vr)          -> ae   (include/RE/Offsets*.h: Offset::, VTABLE_*, RTTI_*, NiRTTI_* tables)
ae == 0 means "no AE id" (SE/VR-only slot) and is not an id.  Non-literal arguments are counted and reported.
Kinds: vtable (VTABLE_*), rtti (RTTI_*, TypeDescriptor), nirtti (NiRTTI_*, a data object), id (everything else:
functions and globals).  Output is ids.json in the same schema collect_ids.py writes (scope 'fork').
"""
import re, os, sys, json, collections, argparse

def strip_comments(s):
    s = re.sub(r'/\*.*?\*/', lambda m: re.sub(r'[^\n]', ' ', m.group(0)), s, flags=re.S)
    return re.sub(r'//[^\n]*', '', s)

NUM = r'(\d+)'
SKIP_FILES = ('include/REL/Relocation.h', 'include/SKSE/Impl/PCH.h')
CALLS = [
    ('REL::ID', re.compile(r'\bREL::ID\s*\(\s*(\w+)\s*\)')),
    ('RELOCATION_ID', re.compile(r'\bRELOCATION_ID\s*\(\s*(\w+)\s*,\s*(\w+)\s*\)')),
    ('RelocationID', re.compile(r'\bREL::RelocationID\s*\(\s*(\w+)\s*,\s*(\w+)\s*(?:,\s*\w+\s*)?\)')),
    ('VariantID', re.compile(r'\bVariantID\s*\(\s*(\w+)\s*,\s*(\w+)\s*,\s*(\w+)\s*\)')),
    ('RelocationID-decl', re.compile(r'\bRelocationID\s+\w+\s*[\(\{]\s*(\w+)\s*,\s*(\w+)\s*(?:,\s*\w+\s*)?[\)\}]')),
    ('ID-decl', re.compile(r'\bREL::ID\s+\w+\s*[\(\{]\s*(\d+)\s*[\)\}]')),
    ('VariantID-decl', re.compile(r'\bVariantID\s+\w+\s*\(\s*(\w+)\s*,\s*(\w+)\s*,\s*(\w+)\s*\)')),   # constexpr REL::VariantID RTTI_X(se, ae, vr)
]
DECL = re.compile(r'(?:constexpr\s+)?(?:std::array<\s*REL::VariantID\s*,\s*\d+\s*>|REL::VariantID|REL::RelocationID|auto)\s+(\w+)\s*(?:=|\{|\()')

def kind_of(sym):
    if sym.startswith('VTABLE_'): return 'vtable'
    if sym.startswith('RTTI_'): return 'rtti'
    if sym.startswith('NiRTTI_'): return 'nirtti'
    return 'id'

def scan(root, subs, scope, ids, nforms, nonlit):
    for sub in subs:
        for dp, _, fs in os.walk(os.path.join(root, sub) if sub else root):
            for f in sorted(fs):
                if not f.endswith(('.h', '.cpp', '.inl', '.hpp')) or f in ('VerifiedAddresses.h',): continue
                p = os.path.join(dp, f); rel = os.path.relpath(p, root)
                if rel in SKIP_FILES: continue
                txt = strip_comments(open(p, errors='ignore').read())
                is_off = os.path.basename(p).startswith('Offsets')
                for form, rx in CALLS:
                    for m in rx.finditer(txt):
                        line = txt.count('\n', 0, m.start()) + 1
                        g = m.groups()
                        aev = g[0] if form in ('REL::ID', 'ID-decl') else g[1]
                        if not aev.isdigit(): nonlit.append('%s:%d %s' % (rel, line, m.group(0))); continue
                        ae = int(aev)
                        nforms[form] += 1
                        if ae == 0: continue
                        ls = txt.rfind('\n', 0, m.start()) + 1
                        d = DECL.search(txt[ls:m.end()])
                        sym = d.group(1) if d else None
                        if sym and not (is_off or sym.startswith(('VTABLE_', 'RTTI_', 'NiRTTI_'))): sym = None
                        kind = kind_of(sym) if sym else 'id'
                        if is_off and sym and kind == 'id': sym = 'Offset::' + sym
                        extra = None
                        if kind == 'vtable':          # position inside the symbol's VariantID array (counts ae==0 slots too)
                            seg = txt[ls:m.start()]; extra = {'sym': sym, 'idx': len(re.findall(r'VariantID\s*\(', seg))}
                        elif kind in ('rtti', 'nirtti'): extra = {'sym': sym}
                        key = (kind, ae)
                        e = ids.setdefault(key, {'kind': kind, 'ae_id': ae, 'ae_rva': None, 'scopes': [], 'uses': []})
                        if scope not in e['scopes']: e['scopes'].append(scope)
                        if extra: e.setdefault('extra', {}).update(extra)
                        u = '%s%s %s:%d' % (sym or form, '[%d]' % extra['idx'] if extra and 'idx' in extra else '', rel, line)
                        if u not in e['uses'] and len(e['uses']) < 6: e['uses'].append(u)

def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--fork', required=True); ap.add_argument('-o', default='ids-fork.json')
    ap.add_argument('--own', nargs='*', default=[], help='extra source roots (MFO/APMF native/): their id constructs are added with scope ours')
    ap.add_argument('--merge', default=None, help='earlier ids.json (collect_ids.py): entries not already present are added with their own scopes')
    a = ap.parse_args()
    ids = collections.OrderedDict(); nonlit = []; nforms = collections.Counter()
    scan(a.fork, ('include', 'src'), 'fork', ids, nforms, nonlit)
    nfork = len(ids)
    for r in a.own: scan(r, ('',), 'ours', ids, nforms, nonlit)
    nown = len(ids) - nfork
    nm = 0
    if a.merge:
        for e in json.load(open(a.merge)):
            key = (e['kind'], e['ae_id'] if e['ae_id'] is not None else e['ae_rva'])
            if key in ids:
                for sc in e['scopes']:
                    if sc not in ids[key]['scopes']: ids[key]['scopes'].append(sc)
                continue
            ids[key] = e; nm += 1
    out = list(ids.values())
    for e in out: e['scopes'] = sorted(e['scopes'])
    json.dump(out, open(a.o, 'w'), indent=1)
    c = collections.Counter(e['kind'] for e in out)
    print('forms scanned (incl. ae=0):', dict(nforms))
    print('non-literal id args (skipped):', len(nonlit))
    for x in nonlit[:20]: print('  ', x)
    print('fork distinct AE ids: %d; added from --own: %d; added from --merge: %d' % (nfork, nown, nm))
    print('total entries:', len(out), dict(c))
    print('fork-only total by kind:', dict(collections.Counter(e['kind'] for e in out if 'fork' in e['scopes'])))
if __name__ == '__main__': main()
