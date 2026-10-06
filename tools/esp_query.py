#!/usr/bin/env python3
"""Targeted Bethesda plugin (ESM/ESP/ESL) record query tool.

Commands:
  masters <plugin>                      -- list TES4 masters
  find <plugin> <type> <localid-hex>    -- dump a record (searches nested GRUPs too)
  byedid <plugin> <type> <edid>         -- dump a record by editor ID
  grouplist <plugin>                    -- list top-level GRUP labels

FormID subrecords are resolved against the plugin's master list.
Record header: type[4] dataSize[u32] flags[u32] formID[u32] ... (24 bytes total)
GRUP header:   'GRUP' groupSize[u32,incl hdr] label[4] type[i32] ... (24 bytes)
Compressed flag 0x00040000: data = u32 decompSize + zlib stream.
Large subrecords: XXXX subrecord carries real size of the NEXT subrecord.
"""
import struct, zlib, sys, os

def read_plugin(path):
    with open(path, 'rb') as f:
        return f.read()

def masters_of(data):
    # TES4 record is first
    typ = data[0:4]
    assert typ == b'TES4', typ
    dsz = struct.unpack('<I', data[4:8])[0]
    body = data[24:24+dsz]
    ms = []
    for st, sd in subrecords(body):
        if st == b'MAST':
            ms.append(sd.rstrip(b'\0').decode('cp1252'))
    return ms

def subrecords(buf):
    i = 0; n = len(buf)
    while i + 6 <= n:
        typ = buf[i:i+4]; sz = struct.unpack('<H', buf[i+4:i+6])[0]
        if typ == b'XXXX':
            realsz = struct.unpack('<I', buf[i+6:i+10])[0]
            i += 6 + sz
            typ = buf[i:i+4]; i += 6
            yield typ, buf[i:i+realsz]; i += realsz
        else:
            i += 6
            yield typ, buf[i:i+sz]; i += sz

def rec_body(data, off, dsz, flags):
    body = data[off+24:off+24+dsz]
    if flags & 0x00040000:
        body = zlib.decompress(body[4:])
    return body

def walk_records(data, start, end, depth=0):
    """Yield (rectype, dsz, flags, formid, offset) for every record, recursing into GRUPs."""
    i = start
    while i + 24 <= end:
        typ = data[i:i+4]
        if typ == b'GRUP':
            gsz = struct.unpack('<I', data[i+4:i+8])[0]
            yield from walk_records(data, i+24, i+gsz, depth+1)
            i += gsz
        else:
            dsz, flags, fid = struct.unpack('<III', data[i+4:i+16])
            yield typ, dsz, flags, fid, i
            i += 24 + dsz

def top_groups(data):
    """Yield (label, start, end) of top-level GRUPs."""
    dsz = struct.unpack('<I', data[4:8])[0]
    i = 24 + dsz
    n = len(data)
    while i + 24 <= n:
        assert data[i:i+4] == b'GRUP', (i, data[i:i+4])
        gsz = struct.unpack('<I', data[i+4:i+8])[0]
        yield data[i+8:i+12], i+24, i+gsz
        i += gsz

FORMID_SUBS = {  # subrecord types whose payload is one-or-more u32 formids (for our record types)
    b'NAME', b'RNAM', b'DOFT', b'SOFT', b'INAM', b'MODL', b'VTCK', b'DPLT',
    b'ECOR', b'CNAM', b'ZNAM', b'WNAM', b'ATKR', b'SPLO', b'PNAM', b'HCLF',
    b'FTST', b'DEFT', b'TPLT',
}

def fid_str(fid, masters, selfname):
    idx = fid >> 24
    src = masters[idx] if idx < len(masters) else selfname
    return f"{fid:08X} ({src}:{fid & 0xFFFFFF:06X})"

def dump_record(rectype, body, masters, selfname, resolve=True):
    for st, sd in subrecords(body):
        label = st.decode('ascii', 'replace')
        if st == b'EDID' or st == b'FULL' and sd and sd[-1:] == b'\0':
            try:
                print(f"  {label}: {sd.rstrip(b'\0').decode('cp1252')}")
                continue
            except Exception:
                pass
        if resolve and st in FORMID_SUBS and len(sd) % 4 == 0 and len(sd) >= 4:
            fids = struct.unpack(f'<{len(sd)//4}I', sd)
            # heuristic: only treat as formids for known record types
            print(f"  {label}: " + ", ".join(fid_str(f, masters, selfname) for f in fids))
            continue
        hexs = sd[:64].hex()
        asc = ''.join(chr(c) if 32 <= c < 127 else '.' for c in sd[:64])
        print(f"  {label} [{len(sd)}]: {hexs}{'...' if len(sd)>64 else ''}  |{asc}|")

def find_record(data, want_type, want_fid=None, want_edid=None):
    """Search top-level groups first (fast path for type-grouped records), then full walk."""
    wt = want_type.encode()
    # fast path: matching top-level group
    for label, s, e in top_groups(data):
        if label != wt and wt not in (b'ACHR', b'REFR'):
            continue
        for typ, dsz, flags, fid, off in walk_records(data, s, e):
            if typ != wt: continue
            if want_fid is not None and fid != want_fid: continue
            if want_edid is not None:
                body = rec_body(data, off, dsz, flags)
                edid = None
                for st, sd in subrecords(body):
                    if st == b'EDID': edid = sd.rstrip(b'\0').decode('cp1252','replace'); break
                if edid != want_edid: continue
            return typ, dsz, flags, fid, off
    return None

def main():
    cmd = sys.argv[1]
    path = sys.argv[2]
    data = read_plugin(path)
    selfname = os.path.basename(path)
    ms = masters_of(data)
    if cmd == 'masters':
        for i, m in enumerate(ms): print(f"  [{i:02X}] {m}")
        print(f"  [{len(ms):02X}] {selfname} (self)")
        return
    if cmd == 'grouplist':
        for label, s, e in top_groups(data):
            print(f"  {label.decode('ascii','replace')}  {e-s} bytes")
        return
    want_type = sys.argv[3]
    if cmd == 'find':
        want_fid = int(sys.argv[4], 16)
        r = find_record(data, want_type, want_fid=want_fid)
    elif cmd == 'byedid':
        r = find_record(data, want_type, want_edid=sys.argv[4])
    else:
        sys.exit("unknown cmd")
    if not r:
        print(f"NOT FOUND: {want_type} {sys.argv[4]} in {selfname}")
        sys.exit(1)
    typ, dsz, flags, fid, off = r
    print(f"{typ.decode()} {fid:08X} flags={flags:08X}{' COMPRESSED' if flags & 0x40000 else ''} size={dsz} @0x{off:X} in {selfname}")
    dump_record(typ, rec_body(data, off, dsz, flags), ms, selfname)

if __name__ == '__main__':
    main()
