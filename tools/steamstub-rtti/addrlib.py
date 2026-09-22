#!/usr/bin/env python3
# Address Library decoder (formats 1 = SE version-*.bin, 2 = AE versionlib-*.bin).
# Same varint scheme as CommonLibSSE-NG REL::IDDatabase::unpack_file; format only changes the file family.
import struct, sys, bisect

def load(path):
    d = open(path, 'rb').read(); off = 0
    def rd(fmt):
        nonlocal off
        v = struct.unpack_from(fmt, d, off); off += struct.calcsize(fmt); return v
    (fmt,) = rd('<i')
    if fmt not in (1, 2):
        raise SystemExit('unknown address-library format %d' % fmt)
    version = rd('<4i'); (nameLen,) = rd('<i'); name = d[off:off+nameLen].decode(); off += nameLen
    (ptrSize,) = rd('<i'); (count,) = rd('<i')
    id2off = {}; prevID = 0; prevOffset = 0
    for _ in range(count):
        (t,) = rd('<B'); lo = t & 0xF; hi = t >> 4
        if lo == 0: (idv,) = rd('<Q')
        elif lo == 1: idv = prevID + 1
        elif lo == 2: (b,) = rd('<B'); idv = prevID + b
        elif lo == 3: (b,) = rd('<B'); idv = prevID - b
        elif lo == 4: (w,) = rd('<H'); idv = prevID + w
        elif lo == 5: (w,) = rd('<H'); idv = prevID - w
        elif lo == 6: (w,) = rd('<H'); idv = w
        elif lo == 7: (dd,) = rd('<I'); idv = dd
        else: raise ValueError('lo')
        tmp = (prevOffset // ptrSize) if (hi & 8) else prevOffset
        h = hi & 7
        if h == 0: (offv,) = rd('<Q')
        elif h == 1: offv = tmp + 1
        elif h == 2: (b,) = rd('<B'); offv = tmp + b
        elif h == 3: (b,) = rd('<B'); offv = tmp - b
        elif h == 4: (w,) = rd('<H'); offv = tmp + w
        elif h == 5: (w,) = rd('<H'); offv = tmp - w
        elif h == 6: (w,) = rd('<H'); offv = w
        elif h == 7: (dd,) = rd('<I'); offv = dd
        else: raise ValueError('hi')
        if hi & 8: offv *= ptrSize
        id2off[idv] = offv; prevID = idv; prevOffset = offv
    return {'format': fmt, 'version': version, 'name': name, 'ptrSize': ptrSize, 'count': count, 'map': id2off}

class DB:
    def __init__(self, path):
        r = load(path); self.map = r['map']; self.version = r['version']; self.format = r['format']
        self.rev = {}
        for k, v in self.map.items(): self.rev.setdefault(v, []).append(k)
        self.offs = sorted(self.rev)
    def id2rva(self, i): return self.map.get(i)
    def rva2id(self, rva): return self.rev.get(rva)
    def containing(self, rva):
        """greatest library offset <= rva (function containing rva), returns (ids, base_rva)"""
        i = bisect.bisect_right(self.offs, rva) - 1
        if i < 0: return None
        b = self.offs[i]; return self.rev[b], b

AE = '/mnt/gaming/modlists/Projects/custom-modlist/vfs/Default/Data/SKSE/Plugins/versionlib-1-6-1170-0.bin'
SE = '/mnt/gaming/modlists/Projects/custom-modlist/vfs/Default/Data/SKSE/Plugins/version-1-5-97-0.bin'

if __name__ == '__main__':
    for p in (AE, SE):
        db = DB(p); print(p.split('/')[-1], 'format', db.format, 'version', db.version, 'ids', len(db.map))
    ae = DB(AE); se = DB(SE)
    print('vectors: AE 38894 ->', hex(ae.id2rva(38894)), '(expect 0x6c9820) ; SE 37938 ->', hex(se.id2rva(37938)), '(expect 0x637a80)')
    for a in sys.argv[1:]:
        k, v = a.split(':'); db = ae if k == 'ae' else se
        if v.startswith('0x'): print(k, v, '-> ids', db.rva2id(int(v, 16)), 'containing', db.containing(int(v, 16)))
        else: print(k, 'id', v, '->', hex(db.id2rva(int(v))) if db.id2rva(int(v)) is not None else None)
