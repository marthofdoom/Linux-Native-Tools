#!/usr/bin/env python3
"""Self-test with synthetic fixtures built in code (no modlist files needed). Run: python3 selftest.py"""
import os, struct, sys, tempfile, zlib, shutil, io, contextlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lodebug_core.instance import Instance, win_to_posix
from lodebug_core.vfs import VFS
from lodebug_core.bsa import Bsa
from lodebug_core import pex as X, plugins as P, safety, cli

def sub(t, d): return t + struct.pack('<H', len(d)) + d
def rec(t, fid, subs, flags=0, compress=False):
    body = b''.join(subs)
    if compress:
        flags |= 0x40000; body = struct.pack('<I', len(body)) + zlib.compress(body)
    return t + struct.pack('<IIIHHHH', len(body), flags, fid, 0, 0, 0, 0) + body
def grup(label, recs):
    body = b''.join(recs)
    return b'GRUP' + struct.pack('<I', 24 + len(body)) + label + struct.pack('<IHHHH', 0, 0, 0, 0, 0) + body
def plugin(masters, gmsts, flags=0, compress=False):
    hdr = [sub(b'HEDR', struct.pack('<fII', 1.7, 0, 0))] + [sub(b'MAST', m.encode() + b'\0') + sub(b'DATA', b'\0' * 8) for m in masters]
    out = rec(b'TES4', 0, hdr, flags)
    rs = [rec(b'GMST', fid, [sub(b'EDID', ed.encode() + b'\0'), sub(b'DATA', val)], compress=compress) for fid, ed, val in gmsts]
    return out + grup(b'GMST', rs)

def bsa_build(version, files, compress):
    """files: {path: bytes}. Folder/file hashes are zero (readers here do not use them)."""
    import lz4.frame
    folders = {}
    for p, d in files.items():
        f, _, n = p.rpartition('/'); folders.setdefault(f, []).append((n, d))
    fc = len(folders); nf = len(files)
    tfl = sum(len(f) + 1 for f in folders); tnl = sum(len(n) + 1 for fl in folders.values() for n, _ in fl)
    recsz = 24 if version == 105 else 16
    off_blocks = 36 + recsz * fc
    blk = b''; names = b''; datas = []
    blocklen = [len(f) + 2 + 16 * len(fl) for f, fl in folders.items()]
    data_start = off_blocks + sum(blocklen) + tnl
    cur = data_start; blob = b''; frecs = []
    for f, fl in folders.items():
        for n, d in fl:
            if compress:
                c = struct.pack('<I', len(d)) + (lz4.frame.compress(d) if version == 105 else zlib.compress(d))
            else: c = d
            frecs.append((f, n, len(c), cur)); blob += c; cur += len(c)
    hdr = b'BSA\0' + struct.pack('<IIIIIIIHH', version, 36, 3 | (4 if compress else 0), fc, nf, tfl, tnl, 0, 0)
    fr = b''; pos = off_blocks + tnl
    i = 0; blk = b''
    for (f, fl), bl in zip(folders.items(), blocklen):
        fr += struct.pack('<QII', 0, len(fl), pos) if version == 104 else struct.pack('<QIIQ', 0, len(fl), 0, pos)
        blk += bytes([len(f) + 1]) + f.replace('/', '\\').encode() + b'\0'
        for n, d in fl:
            _f, _n, sz, o = frecs[i]; i += 1
            blk += struct.pack('<QII', 0, sz, o); names += n.encode() + b'\0'
        pos += bl
    return hdr + fr + blk + names + blob

def pex_build(strings, funcs):
    """funcs: [(name, [(pname,ptype)], [(opname, [vals], [varvals])])]; vals are ('id'|'str', text) or ('int', n)."""
    st = list(strings)
    def si(s):
        if s not in st: st.append(s)
        return st.index(s)
    opc = {v[0]: k for k, v in X.OPS.items()}
    def val(v):
        k, x = v
        if k == 'id': return b'\x01' + struct.pack('>H', si(x))
        if k == 'str': return b'\x02' + struct.pack('>H', si(x))
        if k == 'int': return b'\x03' + struct.pack('>i', x)
        if k == 'none': return b'\x00'
    fb = b''
    for name, params, instrs in funcs:
        f = struct.pack('>HHIB', si('None'), si(''), 0, 0)
        f += struct.pack('>H', len(params)) + b''.join(struct.pack('>HH', si(n), si(t)) for n, t in params)
        f += struct.pack('>H', 0)
        f += struct.pack('>H', len(instrs))
        for op, args, extra in instrs:
            f += bytes([opc[op]]) + b''.join(val(a) for a in args)
            if op in ('callmethod', 'callstatic', 'callparent'):
                f += val(('int', len(extra))) + b''.join(val(a) for a in extra)
        fb += struct.pack('>H', si(name)) + f
    objname = si('TestScript'); parent = si('ObjectReference'); auto = si('')
    state = struct.pack('>HH', si(''), len(funcs)) + fb
    obj = struct.pack('>HHIHHH', parent, si(''), 0, auto, 0, 0) + struct.pack('>H', 0) + struct.pack('>H', 1) + state
    # object: name, size, then the above (var count 0 and prop count 0 precede the state count)
    obj = struct.pack('>HHIH', parent, si(''), 0, auto) + struct.pack('>HH', 0, 0) + struct.pack('>H', 1) + state
    objb = struct.pack('>HI', objname, 4 + len(obj)) + obj
    w = lambda s: struct.pack('>H', len(s)) + s.encode()
    head = struct.pack('>IBBHQ', 0xFA57C0DE, 3, 2, 1, 0) + w('TestScript.psc') + w('u') + w('m')
    strtab = struct.pack('>H', len(st)) + b''.join(w(s) for s in st)
    return head + strtab + b'\x00' + struct.pack('>H', 0) + struct.pack('>H', 1) + objb

PASS = FAIL = 0
def check(name, cond):
    global PASS, FAIL
    if cond: PASS += 1; print(f"ok   {name}")
    else: FAIL += 1; print(f"FAIL {name}")

def main():
    root = tempfile.mkdtemp(prefix='lodebug_selftest_')
    try:
        inst = os.path.join(root, 'inst'); game = os.path.join(root, 'game')
        for d in ('mods/ModA/scripts', 'mods/ModB', 'overwrite', 'profiles/P'): os.makedirs(os.path.join(inst, d))
        os.makedirs(os.path.join(game, 'Data'))
        open(os.path.join(inst, 'portable.txt'), 'w').write('x')
        open(os.path.join(inst, 'ModOrganizer.ini'), 'w').write(
            "[General]\nselected_profile=@ByteArray(P)\ngamePath=@ByteArray(Z:\\\\%s)\n" % game.replace('/', '\\\\').lstrip('\\'))
        open(os.path.join(inst, 'profiles/P/modlist.txt'), 'w').write("# x\n+ModA\n+ModB\n-ModOff\n")
        open(os.path.join(inst, 'profiles/P/loadorder.txt'), 'w').write("Skyrim.esm\nB.esp\nA.esp\nLight.esl\n")
        open(os.path.join(inst, 'profiles/P/plugins.txt'), 'w').write("*B.esp\n*A.esp\n*Light.esl\n")
        gm = lambda v: struct.pack('<i', v)
        open(os.path.join(game, 'Data/Skyrim.esm'), 'wb').write(plugin([], [], flags=1))
        open(os.path.join(inst, 'mods/ModB/B.esp'), 'wb').write(plugin(['Skyrim.esm'], [(0x01001000, 'iTestGold', gm(250))]))
        open(os.path.join(inst, 'mods/ModA/A.esp'), 'wb').write(plugin(['Skyrim.esm', 'B.esp'], [(0x01001000, 'iTestGold', gm(500))], compress=True))
        open(os.path.join(inst, 'mods/ModA/Light.esl'), 'wb').write(plugin(['Skyrim.esm'], [(0x800, 'fTestF', struct.pack('<f', 1.5))], flags=0x200))
        # archives: ModA/A.bsa (v105 lz4) holds scripts/foo.pex + scripts/bar.pex; ModB/B.bsa (v104 zlib) holds scripts/bar.pex
        pa = pex_build(['x'], [('OnActivate', [('akActionRef', 'ObjectReference')], [
            ('callstatic', [('id', 'game'), ('id', 'GetPlayer'), ('id', '::p')], []),
            ('callmethod', [('id', 'SendTrespassAlarm'), ('id', 'self'), ('id', '::n')], [('id', '::p')]),
            ('callmethod', [('id', 'SendStealAlarm'), ('id', 'self'), ('id', '::n')], [('id', 'akActionRef')])])])
        open(os.path.join(inst, 'mods/ModA/A.bsa'), 'wb').write(bsa_build(105, {'scripts/foo.pex': pa, 'scripts/bar.pex': b'A', 'meshes/x/y.nif': b'n' * 500}, True))
        open(os.path.join(inst, 'mods/ModB/B.bsa'), 'wb').write(bsa_build(104, {'scripts/bar.pex': b'B', 'interface/t.txt': b'hello'}, True))
        open(os.path.join(inst, 'mods/ModA/scripts/bar.pex'), 'wb').write(b'LOOSE')

        i = Instance(inst, None)
        check('Z:\\ path mapping', win_to_posix('@ByteArray(Z:\\\\mnt\\\\x y)') == '/mnt/x y')
        check('profile + game dir', i.profile == 'P' and i.data_dir == os.path.join(game, 'Data'))
        ap = i.active_plugins()
        check('load order + owners', [p.name for p in ap] == ['Skyrim.esm', 'B.esp', 'A.esp', 'Light.esl'] and ap[2].owner == 'ModA' and ap[0].owner == '<game Data>')
        check('ESL/ESM flags', ap[0].is_esm and ap[3].is_esl and not ap[1].is_esl)
        v = VFS(i)
        ps = v.which('scripts/bar.pex')
        check('loose beats bsa', ps[0].kind == 'loose' and [p.kind for p in ps[1:]] == ['bsa', 'bsa'])
        check('later plugin bsa beats earlier (A over B)', ps[1].plugin == 'A.esp' and ps[2].plugin == 'B.esp')
        check('bsa v105 lz4 read', v.which('meshes/x/y.nif')[0].read() == b'n' * 500)
        check('bsa v104 zlib read', v.which('interface/t.txt')[0].read() == b'hello')
        check('case-insensitive which', len(v.which('Scripts\\BAR.PEX')) == 3)
        # records
        buf = io.StringIO()
        ns = type('A', (), dict(instance=inst, profile=None, type='GMST', edid='iTestGold', formid=None, out=None))()
        with contextlib.redirect_stdout(buf): cli.cmd_record(ns)
        o = buf.getvalue()
        check('record: both layers + compressed + winner A int 500', 'int 250' in o and 'int 500' in o and 'WINNER by load order: A.esp' in o)
        ns.edid = None; ns.formid = 'B.esp:001000'
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf): cli.cmd_record(ns)
        check('record by formid resolves through masters', 'A.esp' in buf.getvalue() and 'B.esp' in buf.getvalue())
        ns.formid = None; ns.edid = 'fTestF'
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf): cli.cmd_record(ns)
        check('ESL float GMST', 'float 1.5' in buf.getvalue())
        # pex
        sc = X.Script(pa)
        calls = list(X.find_calls(sc, {'sendtrespassalarm', 'sendstealalarm'}))
        by = {c['call']: c for c in calls}
        check('pex: both calls found in OnActivate', set(by) == {'SendTrespassAlarm', 'SendStealAlarm'} and by['SendStealAlarm']['events'] == ['OnActivate'])
        check('pex: arg origin game.GetPlayer()', by['SendTrespassAlarm']['args'] == ['game.GetPlayer()'])
        check('pex: arg origin event param', by['SendStealAlarm']['args'] == ['param akActionRef'])
        check('pex-dump runs', 'callmethod' in X.dump(sc))
        # pex-calls end to end via bsa
        buf = io.StringIO()
        ns = type('A', (), dict(instance=inst, profile=None, call='crime', out=None))()
        with contextlib.redirect_stdout(buf): cli.cmd_pex_calls(ns)
        check('pex-calls finds script inside BSA with owner', 'foo.pex' in buf.getvalue() and 'A.bsa'.lower() in buf.getvalue().lower())
        # safety
        try:
            os.environ['LODEBUG_CACHE'] = os.path.join(inst, 'cache')
            safety.check_outputs(inst); check('safety: cache inside instance refused', False)
        except SystemExit: check('safety: cache inside instance refused', True)
        os.environ['LODEBUG_CACHE'] = os.path.join(root, 'cache_ok')
        try: safety.check_outputs(inst, os.path.join(inst, 'o')); check('safety: --out inside refused', False)
        except SystemExit: check('safety: --out inside refused', True)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print(f"{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)

if __name__ == '__main__': main()
