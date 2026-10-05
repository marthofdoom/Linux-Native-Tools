#!/usr/bin/env python3
"""collect_ids.py -- enumerate the Address Library ids (AE column = 1.6.1170 ids) that MFO / APMF use.

Sources:
  ours   : id constructs in MFO/APMF native/ (RelocationID / REL::ID / VariantID / VTABLE_ / RTTI_ / Offset:: names)
           plus hand-listed APMF table ids/RVA literals (EquipSink site/path tables, AiCastSeats RVAs)
  cl     : CommonLib-internal ids reached by name-match + transitive closure over the fork's src/ and inline
           include/RE bodies (same method as _commonlib/fork-plan-2026-09-23/clids.py, pointed at the FORK)
  infra  : always-reached CommonLib ids (MemoryManager, BSFixedString, RTDynamicCast, logger)
Output: ids.json  (list of {key, kind, ae_id|ae_rva, scope, uses[]})
Symbol tables come from the fork checkout (mit-3.7 tip), never from alandtse.
"""
import re, os, sys, json, collections, argparse

P = '/mnt/gaming/modlists/Projects'
FORK = P + '/_commonlib/mit-3.7-fork'
MFO = P + '/marth-follower-overhaul/native'
APMF = P + '/ai-package-management-framework/native'
tok = re.compile(r'\b[A-Za-z_]\w*\b')

# ---------- fork symbol tables ----------
def parse_offsets(path):
    """Offset::Ns::Name -> (se, ae)"""
    out = {}; stack = []
    ns = re.compile(r'^\s*namespace\s+([\w:]+)')
    cx = re.compile(r'constexpr\s+auto\s+(\w+)\s*=\s*RELOCATION_ID\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)')
    depth = 0; nsdepth = []
    for l in open(path, errors='ignore'):
        m = ns.match(l)
        if m: stack.append(m.group(1)); nsdepth.append(depth)
        m = cx.search(l)
        if m: out['::'.join(stack[1:] + [m.group(1)])] = (int(m.group(2)), int(m.group(3)))
        depth += l.count('{') - l.count('}')
        while nsdepth and depth <= nsdepth[-1] and '}' in l:
            nsdepth.pop(); stack.pop()
    return out

def parse_variant_tables(path, prefix):
    """VTABLE_X / RTTI_X -> list of (se, ae)"""
    out = {}
    pat = re.compile(r'(%s_\w+)\s*[\({]\s*(.*)$' % prefix)
    vid = re.compile(r'VariantID\s*\(\s*(\d+)\s*,\s*(\d+)\s*,')
    for l in open(path, errors='ignore'):
        m = pat.search(l)
        if m and 'VariantID' in l:
            v = [(int(a), int(b)) for a, b in vid.findall(l)]
            if not v:   # RTTI_X(se, ae, vr) form
                mm = re.search(prefix + r'_\w+\s*\(\s*(\d+)\s*,\s*(\d+)\s*,', l)
                if mm: v = [(int(mm.group(1)), int(mm.group(2)))]
            out[m.group(1)] = v
    return out

OFFS = parse_offsets(FORK + '/include/RE/Offsets.h')
VT = parse_variant_tables(FORK + '/include/RE/Offsets_VTABLE.h', 'VTABLE')
RT = parse_variant_tables(FORK + '/include/RE/Offsets_RTTI.h', 'RTTI')

ids = collections.OrderedDict()   # key -> dict
def add(kind, scope, use, ae_id=None, ae_rva=None, extra=None):
    key = (kind, ae_id if ae_id is not None else ae_rva)
    e = ids.setdefault(key, {'kind': kind, 'ae_id': ae_id, 'ae_rva': ae_rva, 'scopes': set(), 'uses': []})
    e['scopes'].add(scope)
    if use not in e['uses']: e['uses'].append(use)
    if extra: e.setdefault('extra', {}).update(extra)

def add_symbol(sym, scope, use):
    """sym like VTABLE_X / RTTI_X / Offset::A::B / RELOCATION_ID(a,b) / REL::ID(n)"""
    sym = sym.replace('RE::', '')
    if sym.startswith('VTABLE_'):
        for i, (se, ae) in enumerate(VT.get(sym, [])):
            if not ae: continue   # no AE id (VR-only slot of the table)
            add('vtable', scope, '%s[%d] %s' % (sym, i, use), ae_id=ae, extra={'sym': sym, 'idx': i})
        if sym not in VT: add('unknown', scope, sym + ' (VTABLE not in fork)')
    elif sym.startswith('RTTI_'):
        for se, ae in RT.get(sym, []):
            if not ae: continue
            add('rtti', scope, '%s %s' % (sym, use), ae_id=ae, extra={'sym': sym})
        if sym not in RT: add('unknown', scope, sym + ' (RTTI not in fork)')
    elif sym.startswith('Offset::'):
        k = sym[len('Offset::'):]
        if k in OFFS: add('id', scope, '%s %s' % (sym, use), ae_id=OFFS[k][1])
        else: add('unknown', scope, sym + ' (Offset not in fork)')
    else:
        m = re.match(r'(?:REL::)?(?:RELOCATION_ID|RelocationID)\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)', sym)
        if m: add('id', scope, 'RelocationID(%s,%s) %s' % (m.group(1), m.group(2), use), ae_id=int(m.group(2)))
        m = re.match(r'(?:REL::)?VariantID\s*\(\s*(\d+)\s*,\s*(\d+)\s*,', sym)
        if m: add('id', scope, 'VariantID(%s,%s) %s' % (m.group(1), m.group(2), use), ae_id=int(m.group(2)))

# ---------- our constructs ----------
ownpat = re.compile(r'(RELOCATION_ID\s*\([^)]*\)|REL::RelocationID\s*\([^)]*\)|\bRelocationID\s*\([^)]*\)|REL::VariantID\s*\([^)]*\)|\bVariantID\s*\([^)]*\)|RE::VTABLE_\w+|RE::RTTI_\w+|RE::Offset::\w+(?:::\w+)*)')
def scan_own(root, tag):
    for dp, _, fs in os.walk(root):
        for f in fs:
            if not f.endswith(('.cpp', '.h')) or f in ('APMF_API.h', 'VerifiedAddresses.h'): continue
            p = os.path.join(dp, f)
            for i, l in enumerate(open(p, errors='ignore'), 1):
                s = l.split('//')[0]
                for m in ownpat.finditer(s):
                    add_symbol(re.sub(r'\s+', '', m.group(1)) if '(' in m.group(1) else m.group(1), 'ours', '%s:%s:%d' % (tag, os.path.relpath(p, root), i))

# ---------- skyrim_cast RTTI (target types) ----------
def scan_casts(root, tag):
    pat = re.compile(r'(?:skyrim_cast|netimmerse_cast)\s*<\s*(?:const\s+)?(?:RE::)?(\w+)')
    for dp, _, fs in os.walk(root):
        for f in fs:
            if not f.endswith(('.cpp', '.h')): continue
            for i, l in enumerate(open(os.path.join(dp, f), errors='ignore'), 1):
                for m in pat.finditer(l.split('//')[0]):
                    if 'RTTI_' + m.group(1) in RT:
                        add_symbol('RTTI_' + m.group(1), 'ours', 'skyrim_cast target %s:%s:%d' % (tag, f, i))

# ---------- CommonLib reach (clids.py method, pointed at the fork) ----------
idpat = re.compile(r'(RELOCATION_ID\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)|REL::ID\s*\(\s*(\d+)\s*\)|RelocationID\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)|Offset::\w+(?:::\w+)*|VTABLE_\w+|RTTI_\w+)')
defpat = re.compile(r'^\s*(?:template\s*<[^>]*>\s*)?(?:[\w:<>\*&,\s]+?[\s\*&])?(\w+)::(~?\w+)\s*\([^;]*$')
hdrfn = re.compile(r'^\s*(?:\[\[nodiscard\]\]\s*)?(?:static\s+|inline\s+|virtual\s+|constexpr\s+)*[\w:<>\*&,\s]+?[\s\*&](~?\w+)\s*\([^;]*\)\s*(?:const)?\s*(?:noexcept)?\s*\{?\s*$')
clspat = re.compile(r'^\s*(?:class|struct)\s+(?:\w+\s+)?(\w+)\b(?!.*;)')
def cl_funcs():
    funcs = {}
    def addf(cls, fn, ids_, toks, loc):
        e = funcs.setdefault((cls, fn), [set(), set(), loc]); e[0] |= ids_; e[1] |= toks
    for dp, _, fs in os.walk(FORK + '/src'):
        for f in fs:
            if not f.endswith('.cpp'): continue
            p = os.path.join(dp, f); lines = open(p, errors='ignore').read().split('\n')
            cur = None; ids_ = set(); toks = set(); loc = None
            for i, l in enumerate(lines, 1):
                m = defpat.match(l)
                if m and not l.strip().startswith(('return', 'if', 'else', '//')) and '=' not in l.split('(')[0]:
                    if cur: addf(*cur, ids_, toks, loc)
                    cur = (m.group(1), m.group(2)); ids_ = set(); toks = set(); loc = '%s:%d' % (os.path.relpath(p, FORK), i)
                    continue
                s = l.split('//')[0]
                for mm in idpat.finditer(s): ids_.add(mm.group(1).replace(' ', ''))
                toks |= set(tok.findall(s))
            if cur: addf(*cur, ids_, toks, loc)
    for dp, _, fs in os.walk(FORK + '/include/RE'):
        for f in fs:
            if not f.endswith('.h') or f.startswith('Offsets'): continue
            p = os.path.join(dp, f); lines = open(p, errors='ignore').read().split('\n')
            cls = None; fn = None; fnline = 0
            for i, l in enumerate(lines, 1):
                m = clspat.match(l)
                if m: cls = m.group(1)
                m = hdrfn.match(l)
                if m and cls: fn = m.group(1); fnline = i
                s = l.split('//')[0]
                ids_ = {mm.group(1).replace(' ', '') for mm in idpat.finditer(s)}
                if ids_ and fn and i - fnline < 15:
                    addf(cls, fn, ids_, set(tok.findall(s)), '%s:%d' % (os.path.relpath(p, FORK), i))
    return funcs

def used_tokens(root):
    t = set()
    for dp, _, fs in os.walk(root):
        for f in fs:
            if f.endswith(('.cpp', '.h')) and f not in ('APMF_API.h',):
                t |= set(tok.findall(open(os.path.join(dp, f), errors='ignore').read()))
    return t

def cl_reach(funcs, root, tag):
    U = used_tokens(root)
    reach = set(k for k in funcs if k[0] in U and k[1] in U)
    changed = True
    while changed:
        changed = False
        T = set().union(*(funcs[k][1] for k in reach)) if reach else set()
        for k in funcs:
            if k not in reach and k[1] in T and (k[0] in T or k[0] in U):
                reach.add(k); changed = True
    n = 0
    for k in sorted(reach):
        if not funcs[k][0]: continue
        n += 1
        for s in funcs[k][0]:
            add_symbol(s, 'cl', 'CL %s::%s [%s] %s' % (k[0], k[1], tag, funcs[k][2]))
    return n

# ---------- hand-listed APMF literals ----------
def apmf_extras():
    es = APMF + '/core/EquipSink.cpp'
    txt = open(es, errors='ignore').read()
    # AE tables only (kSitesAE, kPathsAE, kWorkerAE)
    m = re.search(r'kSitesAE\[\]\s*=\s*\{(.*?)\};', txt, re.S)
    for a in re.finditer(r'\{\s*(\d+)\s*,\s*(0x[0-9A-Fa-f]+)\s*,\s*0x[0-9A-Fa-f]+\s*,\s*"(\w+)"', m.group(1)):
        add('id', 'ours', 'APMF EquipSink kSitesAE %s (E8 at +%s)' % (a.group(3), a.group(2)), ae_id=int(a.group(1)), extra={'call_off': int(a.group(2), 16)})
    m = re.search(r'kPathsAE\[\]\s*=\s*\{(.*?)\n\s*\};', txt, re.S)
    for a in re.finditer(r'^\s*\{\s*(\d+)\s*,\s*"(\w+)"', m.group(1), re.M):
        add('id', 'ours', 'APMF EquipSink kPathsAE %s' % a.group(2), ae_id=int(a.group(1)))
    add('id', 'ours', 'APMF EquipSink kWorkerAE', ae_id=int(re.search(r'kWorkerAE\s*=\s*(\d+)', txt).group(1)))
    # raw AE RVAs
    ai = open(APMF + '/core/AiCastSeats.cpp', errors='ignore').read()
    for nm, rv in re.findall(r'\{\s*"(\w+)",\s*RE::VTABLE_\w+\[0\],\s*(0x[0-9a-fA-F]+),', ai):
        add('rva', 'ours', 'APMF AiCastSeats %s calcScore slot 0x0C' % nm, ae_rva=int(rv, 16))
    add('rva', 'ours', 'APMF AiCastSeats kCheckShouldEquipBaseAE', ae_rva=int(re.search(r'kCheckShouldEquipBaseAE\s*=\s*(0x[0-9A-Fa-f]+)', ai).group(1), 16))
    eg = open(APMF + '/core/EquipGate.cpp', errors='ignore').read()
    for rv in ('0x80fcd0',):
        add('rva', 'ours', 'APMF EquipGate CallSiteName pre-loop', ae_rva=int(rv, 16))
    for rv in ('0x813af2', '0x813d38', '0x814270', '0x8144b2'):
        add('rva', 'ours', 'APMF EquipGate CallSiteName selector return address', ae_rva=int(rv, 16))
    add('rva', 'ours', 'APMF NonAliasProbe/CastSeats GetMagicTarget (CombatMagicCaster slot 0x0A)', ae_rva=0x81e020)

def infra():
    for a, b, u in [(11045, 11141, 'MemoryManager ctor/alloc'), (66859, 68115, 'MemoryManager'), (66861, 68117, 'MemoryManager'),
                    (66841, 68088, 'MemoryManager'), (66860, 68116, 'MemoryManager'), (35199, 36091, 'MemoryManager'),
                    (67819, 69161, 'BSFixedString'), (67834, 69176, 'BSFixedString'), (102238, 109689, 'RTDynamicCast'),
                    (508778, 502114, 'logger path global')]:
        add('id', 'infra', 'infra %s RELOCATION_ID(%d,%d)' % (u, a, b), ae_id=b)
    # BSStringPool id, if present
    p = FORK + '/src/RE/B/BSStringPool.cpp'
    if os.path.exists(p):
        for m in re.finditer(r'RELOCATION_ID\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)', open(p).read()):
            add('id', 'infra', 'infra BSStringPool RELOCATION_ID(%s,%s)' % m.groups(), ae_id=int(m.group(2)))

if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('-o', default='ids.json'); a = ap.parse_args()
    scan_own(MFO, 'MFO'); scan_own(APMF, 'APMF'); scan_casts(MFO, 'MFO'); scan_casts(APMF, 'APMF')
    apmf_extras(); infra()
    F = cl_funcs()
    nm = cl_reach(F, MFO, 'MFO'); na = cl_reach(F, APMF, 'APMF')
    out = []
    for k, e in ids.items():
        e['scopes'] = sorted(e['scopes']); out.append(e)
    json.dump(out, open(a.o, 'w'), indent=1)
    c = collections.Counter((e['kind'], tuple(e['scopes'])) for e in out)
    print('CL reachable fns with ids: MFO', nm, 'APMF', na)
    print('distinct entries', len(out))
    for k, v in sorted(c.items()): print(k, v)
