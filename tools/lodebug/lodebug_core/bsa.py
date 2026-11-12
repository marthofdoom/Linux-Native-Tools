"""BSA reader for Skyrim LE (v104, zlib) and SSE (v105, LZ4 frame).
Format reference: UESP wiki 'Skyrim Mod:Archive File Format' (public documentation).

Layout: 36-byte header (magic 'BSA\\0', version, folderOffset, archiveFlags, folderCount, fileCount,
totalFolderNameLength, totalFileNameLength, fileFlags u16, pad u16); folder records (v104: hash u64,
count u32, offset u32 = 16 B; v105: hash u64, count u32, pad u32, offset u64 = 24 B); then per folder a
bstring name + count x file record (hash u64, size u32, offset u32); then the file-name zstring block.
archiveFlags: 0x1 dir names, 0x2 file names, 0x4 compressed, 0x100 embedded names (the data block starts
with a bstring path). Size bit 30 inverts the archive's compression default. Compressed data starts with
u32 uncompressed size.
"""
import struct, zlib

class BsaError(Exception): pass

class Bsa:
    def __init__(self, path):
        self.path = path
        self.entries = []        # (folder_lower, name_lower, size_raw, offset)
        self._parse()

    def _parse(self):
        with open(self.path, 'rb') as f:
            h = f.read(36)
            if len(h) < 36 or h[:4] != b'BSA\0': raise BsaError('not a BSA')
            (self.version, foff, self.flags, fc, nfiles, tfln_dirs, tfln, ffl) = struct.unpack('<IIIIIIIH', h[4:34])
            if self.version not in (104, 105): raise BsaError(f'unsupported BSA version {self.version}')
            recsz = 24 if self.version == 105 else 16
            fr = f.read(recsz * fc)
            counts = []
            for i in range(fc):
                if self.version == 105: _h, c, _p, _o = struct.unpack('<QIIQ', fr[i*recsz:(i+1)*recsz])
                else: _h, c, _o = struct.unpack('<QII', fr[i*recsz:(i+1)*recsz])
                counts.append(c)
            blk = f.read(tfln_dirs + fc + nfiles * 16)
            names = f.read(tfln) if self.flags & 2 else b''
        p = 0; recs = []
        for c in counts:
            n = blk[p]; fname = blk[p+1:p+n].rstrip(b'\0').decode('latin1').replace('\\', '/').lower(); p += 1 + n
            for _ in range(c):
                _h, sz, off = struct.unpack('<QII', blk[p:p+16]); p += 16
                recs.append((fname, sz, off))
        fnames = names.split(b'\0')
        if len(fnames) < len(recs): raise BsaError('file name block shorter than file count')
        self.entries = [(d, fnames[i].decode('latin1').lower(), sz, off) for i, (d, sz, off) in enumerate(recs)]
        self.embed = bool(self.flags & 0x100)

    def paths(self):
        return [(d + '/' + n) if d else n for d, n, _s, _o in self.entries]

    def read(self, entry):
        d, n, sz, off = entry
        compressed = bool(self.flags & 4) ^ bool(sz & (1 << 30))
        sz &= ~(1 << 30)
        with open(self.path, 'rb') as f:
            f.seek(off); b = f.read(sz)
        if self.embed:                       # bstring path prefix
            b = b[1 + b[0]:]
        if compressed:
            b = b[4:]
            if self.version == 105:
                import lz4.frame
                return lz4.frame.decompress(b)
            return zlib.decompress(b)
        return b

    def find(self, path):
        want = path.replace('\\', '/').lower()
        for e in self.entries:
            if ((e[0] + '/' + e[1]) if e[0] else e[1]) == want: return e
        return None

    def read_path(self, path):
        e = self.find(path)
        return self.read(e) if e else None
