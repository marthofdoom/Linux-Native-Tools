"""Plugin record access. Reuses the record parser in tools/esp_query.py (subrecords, walk_records,
top_groups, rec_body, masters_of); this module only adds mmap access, FormID resolution and decoding."""
import os, sys, struct, mmap, re

_here = os.path.dirname(os.path.abspath(__file__))
_tools = os.path.dirname(os.path.dirname(_here))          # tools/
if _tools not in sys.path: sys.path.insert(0, _tools)
import esp_query as EQ                                     # noqa: E402

class Plug:
    """An mmap'd plugin. Keep objects short-lived (thousands of plugins)."""
    def __init__(self, path):
        self.path = path
        self.f = open(path, 'rb')
        try: self.data = mmap.mmap(self.f.fileno(), 0, access=mmap.ACCESS_READ)
        except ValueError: self.data = b''
        self.flags = struct.unpack('<I', self.data[8:12])[0]
        self.masters = EQ.masters_of(self.data)
        self.name = os.path.basename(path)
    def close(self):
        try: self.data.close()
        except Exception: pass
        self.f.close()
    def __enter__(self): return self
    def __exit__(self, *a): self.close()
    @property
    def localized(self): return bool(self.flags & 0x80)

    def groups(self, rtype=None):
        wt = rtype.encode() if rtype else None
        for label, s, e in EQ.top_groups(self.data):
            if wt is None or label == wt:
                yield label, s, e

    def records(self, rtype=None):
        """Yield (type, flags, formid, off, dsz) for every record (optionally one type's top group)."""
        for label, s, e in self.groups(rtype):
            for typ, dsz, flags, fid, off in EQ.walk_records(self.data, s, e):
                yield typ, flags, fid, off, dsz

    def body(self, off, dsz, flags):
        return EQ.rec_body(self.data, off, dsz, flags)

    def subs(self, off, dsz, flags):
        return list(EQ.subrecords(self.body(off, dsz, flags)))

    def origin(self, fid):
        """(origin plugin name lower, local id) for a raw FormID in this plugin."""
        idx = fid >> 24
        src = self.masters[idx] if idx < len(self.masters) else self.name
        return src.lower(), fid & 0xFFFFFF

def read_tes4(path):
    with open(path, 'rb') as f:
        hdr = f.read(24)
        if hdr[:4] != b'TES4': raise ValueError('not a plugin')
        dsz = struct.unpack('<I', hdr[4:8])[0]
        flags = struct.unpack('<I', hdr[8:12])[0]
        body = f.read(dsz)
    ms = [sd.rstrip(b'\0').decode('cp1252') for st, sd in EQ.subrecords(body) if st == b'MAST']
    return flags, ms

def cstr(b):
    return b.split(b'\0', 1)[0].decode('cp1252', 'replace')

def edid_of(subs):
    for st, sd in subs:
        if st == b'EDID': return cstr(sd)
    return None

def full_of(subs, localized=False):
    for st, sd in subs:
        if st == b'FULL':
            if localized and len(sd) == 4: return f"<lstring {struct.unpack('<I', sd)[0]:08X}>"
            return cstr(sd)
    return None

def decode_gmst(edid, sd, localized=False):
    c = (edid or '?')[:1].lower()
    if c == 'i' and len(sd) == 4: return f"int {struct.unpack('<i', sd)[0]}"
    if c == 'f' and len(sd) == 4: return f"float {struct.unpack('<f', sd)[0]:.6g}"
    if c == 'b' and len(sd) == 4: return f"bool {bool(struct.unpack('<I', sd)[0])}"
    if c == 's':
        if localized and len(sd) == 4: return f"lstring id {struct.unpack('<I', sd)[0]:08X}"
        return f"string {cstr(sd)!r}"
    return 'raw ' + sd.hex()

def hexdump(sd, n=48):
    asc = ''.join(chr(c) if 32 <= c < 127 else '.' for c in sd[:n])
    return f"{sd[:n].hex()}{'...' if len(sd) > n else ''} |{asc}|"
