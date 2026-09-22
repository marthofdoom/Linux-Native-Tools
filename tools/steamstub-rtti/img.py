import struct
class Img:
    def __init__(s,p):
        d=open(p,'rb').read(); s.d=d; pe=struct.unpack_from('<I',d,0x3c)[0]; nsec=struct.unpack_from('<H',d,pe+6)[0]; opt=struct.unpack_from('<H',d,pe+20)[0]; sec=pe+24+opt
        s.secs=[]
        for i in range(nsec):
            n=d[sec+i*40:sec+i*40+8].rstrip(b'\0').decode(errors='replace'); vs,va,rs,ro=struct.unpack_from('<IIII',d,sec+i*40+8); s.secs.append((n,va,vs,ro,rs))
    def off(s,rva):
        for n,va,vs,ro,rs in s.secs:
            if va<=rva<va+max(vs,rs): return ro+rva-va
        raise ValueError(hex(rva))
    def r(s,rva,n): o=s.off(rva); return s.d[o:o+n]
    def u32(s,rva): return struct.unpack_from('<I',s.d,s.off(rva))[0]
    def u64(s,rva): return struct.unpack_from('<Q',s.d,s.off(rva))[0]
