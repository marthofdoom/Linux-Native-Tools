#!/usr/bin/env python3
"""symname.py -- bind CommonLib RTTI_* / VTABLE_* symbol names to TypeDescriptors of one binary, by name only.

The fork's symbol names are demangled class names with every non-alphanumeric run collapsed to '_'.  We demangle every
TypeDescriptor of the image with llvm-undname (names come from our own binary, nothing external), reduce both sides to a
KEY (alnum only; `anonymous namespace' removed; class/struct/union/enum words removed; 'unsigned int/char/short' -> uint/
uchar/ushort as CommonLib spells them) and accept a binding only when the key hits exactly ONE TypeDescriptor.
Used for ids that have no 1.6.1170 Address Library row (so no AE RVA to start from), and as an independent self-test
(known ids are re-derived by name and compared with the library row).
"""
import re, subprocess, collections, pickle, os

def key(s):
    s = s.replace("`anonymous namespace'", '').replace('`', '')
    s = re.sub(r'\b(class|struct|union|enum)\s+', '', s)
    s = re.sub(r'\bunsigned\s+int\b', 'uint', s); s = re.sub(r'\bunsigned\s+char\b', 'uchar', s)
    s = re.sub(r'\bunsigned\s+short\b', 'ushort', s)
    return re.sub(r'[^A-Za-z0-9]', '', s)

def demangle_tds(td_names, cache=None):
    """{mangled '.?AV...' -> demangled text}"""
    if cache and os.path.exists(cache): return pickle.load(open(cache, 'rb'))
    names = sorted(set(td_names))
    inp = ''.join('??_R0%s@8\n' % n.decode()[1:] for n in names)
    out = subprocess.run(['llvm-undname'], input=inp, capture_output=True, text=True).stdout
    blocks = out.strip().split('\n\n'); dem = {}
    if len(blocks) == len(names):
        for n, b in zip(names, blocks):
            last = b.strip().split('\n')[-1].strip()
            if last.endswith("`RTTI Type Descriptor'"): dem[n] = last[:-len("`RTTI Type Descriptor'")].strip()
    if cache: pickle.dump(dem, open(cache, 'wb'))
    return dem

def td_key_index(side, cache=None):
    """key -> [TypeDescriptor rva] for one Side (uses side.rtti())"""
    r = side.rtti(); dem = demangle_tds(r['td_name'].values(), cache)
    idx = collections.defaultdict(list)
    for rva, nm in r['td_name'].items():
        d = dem.get(nm)
        if d: idx[key(d)].append(rva)
    return idx

def sym_key(sym):
    for p in ('RTTI_', 'VTABLE_', 'NiRTTI_'):
        if sym.startswith(p): return key(sym[len(p):])
    return key(sym)
