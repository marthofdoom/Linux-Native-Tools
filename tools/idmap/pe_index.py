#!/usr/bin/env python3
"""pe_index.py -- per-binary function index for idmap.

Reads a Skyrim exe (unpacked or plaintext), walks .pdata for function regions, disassembles each with capstone and
records, per function: masked-byte hash (L1), shape hash (L2), prefix hash, ordered direct call/jmp targets, and
rip-relative data references.  Cached as a pickle next to the output dir.
"""
import struct, hashlib, pickle, os, sys, time, bisect, collections
import capstone
from capstone import Cs, CS_ARCH_X86, CS_MODE_64
from capstone.x86 import X86_OP_REG, X86_OP_IMM, X86_OP_MEM, X86_REG_RIP

BASE = 0x140000000

class PE:
    def __init__(self, path):
        self.path = path
        d = self.d = open(path, 'rb').read()
        pe = struct.unpack_from('<I', d, 0x3c)[0]
        nsec = struct.unpack_from('<H', d, pe + 6)[0]
        opt = struct.unpack_from('<H', d, pe + 20)[0]
        sec = pe + 24 + opt
        self.secs = []
        for i in range(nsec):
            n = d[sec + i * 40:sec + i * 40 + 8].rstrip(b'\0').decode(errors='replace')
            vs, va, rs, ro = struct.unpack_from('<IIII', d, sec + i * 40 + 8)
            self.secs.append((n, va, vs, ro, rs))
        ddoff = pe + 24 + 0x70   # PE32+ data directories
        self.exc_rva, self.exc_size = struct.unpack_from('<II', d, ddoff + 3 * 8)
        self.text = self.secs[0]
        self.text_rva, self.text_vs, self.text_ro = self.text[1], self.text[2], self.text[3]
        self.tbytes = d[self.text_ro:self.text_ro + self.text_vs]
        self.tend = self.text_rva + self.text_vs
    def sec_of(self, rva):
        for n, va, vs, ro, rs in self.secs:
            if va <= rva < va + max(vs, rs): return n
        return None
    def off(self, rva):
        for n, va, vs, ro, rs in self.secs:
            if va <= rva < va + max(vs, rs) and rva - va < rs: return ro + rva - va
        raise ValueError(hex(rva))
    def read(self, rva, n):
        o = self.off(rva); return self.d[o:o + n]
    def u32(self, rva): return struct.unpack_from('<I', self.d, self.off(rva))[0]
    def u64(self, rva): return struct.unpack_from('<Q', self.d, self.off(rva))[0]
    def cstr(self, rva, maxn=200):
        b = self.read(rva, maxn); i = b.find(b'\0'); return b if i < 0 else b[:i]
    def pdata(self):
        """list of (begin,end,unwind_rva,parent_begin_or_None). parent = head function begin for chained (cold/split) regions"""
        out = []
        base = self.off(self.exc_rva)
        for i in range(self.exc_size // 12):
            b, e, u = struct.unpack_from('<III', self.d, base + i * 12)
            if b == 0: continue
            parent = None
            try:
                uo = self.off(u)
                flags = self.d[uo] >> 3
                if flags & 4:
                    ncodes = self.d[uo + 2]
                    po = uo + 4 + ((ncodes + 1) & ~1) * 2
                    pb, pe_, pu = struct.unpack_from('<III', self.d, po)
                    parent = pb
            except Exception: pass
            out.append((b, e, u, parent))
        return out

_md = None
def md():
    global _md
    if _md is None:
        _md = Cs(CS_ARCH_X86, CS_MODE_64); _md.detail = True; _md.skipdata = False
    return _md

def h8(b): return hashlib.blake2b(b, digest_size=8).digest()

def analyze(pe, start, end, maxlen=0x4000):
    """Disassemble [start,end) -> dict(masked bytes, shape list, calls, refs, insn_offs)."""
    end = min(end, start + maxlen)
    code = bytearray(pe.tbytes[start - pe.text_rva:end - pe.text_rva])
    raw = bytes(code)
    masked = bytearray(raw)
    mask = bytearray(b'\x01' * len(raw))   # 1 = fixed byte, 0 = wildcard
    shape = []; calls = []; refs = []; offs = []
    off = 0
    n = len(raw)
    cs = md()
    while off < n:
        got = False
        for ins in cs.disasm(raw[off:], start + off, 1):
            got = True
            sz = ins.size; offs.append(off)
            mn = ins.mnemonic
            sig = [mn]
            b0 = ins.bytes
            # branches with rel32
            tgt = None
            if mn in ('call', 'jmp') or (mn.startswith('j') and mn != 'jmp'):
                if ins.operands and ins.operands[0].type == X86_OP_IMM:
                    tgt = ins.operands[0].imm
                    if (b0[0] in (0xE8, 0xE9)) and sz == 5:
                        masked[off + 1:off + 5] = b'\0\0\0\0'; mask[off + 1:off + 5] = b'\0\0\0\0'
                        calls.append((off, tgt, mn))
                    elif b0[0] == 0x0F and sz == 6:
                        masked[off + 2:off + 6] = b'\0\0\0\0'; mask[off + 2:off + 6] = b'\0\0\0\0'
                        # jcc rel32: intra-function, not a call edge
                    else:
                        pass
                    sig.append('b')
                    shape.append(' '.join(sig)); off += sz; break
            for op in ins.operands:
                if op.type == X86_OP_REG: sig.append('r')
                elif op.type == X86_OP_IMM: sig.append('i')
                elif op.type == X86_OP_MEM:
                    if op.mem.base == X86_REG_RIP:
                        sig.append('R')
                        dofs = ins.disp_offset
                        if dofs and ins.disp_size == 4:
                            masked[off + dofs:off + dofs + 4] = b'\0\0\0\0'; mask[off + dofs:off + dofs + 4] = b'\0\0\0\0'
                            refs.append((off, start + off + sz + op.mem.disp))
                    else:
                        sig.append('m')
            shape.append(' '.join(sig))
            off += sz
            break
        if not got:
            off += 1
    return dict(raw=raw, masked=bytes(masked), mask=bytes(mask), shape=shape, calls=calls, refs=refs, offs=offs)

def build(pe, log=print):
    pd = pe.pdata()
    par = {b: p for b, e, u, p in pd}
    def head_of(b):
        n = 0
        while par.get(b) is not None and n < 16: b = par[b]; n += 1
        return b
    ends = {b: e for b, e, u, p in pd}
    parts = collections.defaultdict(list)
    for b, e, u, p in pd:
        if not (pe.text_rva <= b < pe.tend): continue
        h = head_of(b)
        parts[h].append((b, min(e, pe.tend)))
    funcs = []
    t0 = time.time()
    heads = sorted(h for h in parts if par.get(h) is None and pe.text_rva <= h < pe.tend and any(b == h for b, e in parts[h]))
    for i, h in enumerate(heads):
        rs = [(b, e) for b, e in parts[h] if b == h][:1] + sorted((b, e) for b, e in parts[h] if b != h)
        masked = b''; shape = []; calls = []; refs = []; offs = []; size = 0; nparts = len(rs)
        for b, e in rs:
            a = analyze(pe, b, e)
            rel = b - h
            masked += a['masked']
            shape += a['shape']
            calls += [(rel + o, t, m) for o, t, m in a['calls']]
            refs += [(rel + o, t) for o, t in a['refs']]
            offs += [rel + o for o in a['offs']]
            size += e - b
        hend = [e for b, e in rs if b == h][0]
        npre = min(len(offs), 10)
        hl = hend - h
        pre = h8(masked[:offs[10]] if len(offs) > 10 and offs[10] < hl else masked[:hl])
        funcs.append(dict(start=h, end=hend, hend=hend, size=size, parts=nparts, l1=h8(masked), l2=h8('\n'.join(shape).encode()),
                          pre=pre, npre=npre, nins=len(offs), calls=calls, refs=refs))
        if i % 10000 == 0: log('  %d/%d funcs %.0fs' % (i, len(heads), time.time() - t0))
    return funcs

def load_or_build(path, cache, log=print):
    if os.path.exists(cache):
        return pickle.load(open(cache, 'rb'))
    pe = PE(path)
    funcs = build(pe, log)
    pickle.dump(funcs, open(cache, 'wb'), protocol=4)
    return funcs

if __name__ == '__main__':
    pe = PE(sys.argv[1]); t = time.time()
    pd = pe.pdata(); st = sorted(set(b for b, e, u, c in pd if not c))
    print(len(pd), len(st), 'chained', sum(1 for x in pd if x[3]))
    ends = {b: e for b, e, u, c in pd if not c}
    for b in st[:2000]: analyze(pe, b, ends[b])
    print('2000 funcs', time.time() - t)
