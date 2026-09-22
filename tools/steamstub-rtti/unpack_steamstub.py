#!/usr/bin/env python3
# SteamStub v3.1 x64 unpacker (Steamless algorithm re-implemented; header at EP-0xF0).
import struct, sys, math, collections
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
def rd(d,fmt,off): return struct.unpack_from(fmt,d,off)
def sections(d):
    pe=rd(d,'<I',0x3c)[0]; nsec=rd(d,'<H',pe+6)[0]; opt=rd(d,'<H',pe+20)[0]; sec=pe+24+opt
    out=[]
    for i in range(nsec):
        n=d[sec+i*40:sec+i*40+8].rstrip(b'\0').decode(errors='replace'); vs,va,rs,ro=rd(d,'<IIII',sec+i*40+8); out.append((n,va,vs,ro,rs))
    return pe,out
def rva2off(secs,rva):
    for n,va,vs,ro,rs in secs:
        if va<=rva<va+max(vs,rs): return ro+(rva-va)
    raise ValueError(hex(rva))
def steamxor(buf):
    b=bytearray(buf); key=rd(b,'<I',0)[0]
    for x in range(4,len(b),4):
        val=rd(b,'<I',x)[0]; struct.pack_into('<I',b,x,val^key); key=val
    return bytes(b)
src,dst=sys.argv[1],sys.argv[2]
d=bytearray(open(src,'rb').read())
pe,secs=sections(d); ep=rd(d,'<I',pe+24+16)[0]; base=rd(d,'<Q',pe+24+24)[0]
hoff=rva2off(secs,ep-0xF0); hdr=steamxor(d[hoff:hoff+0xF0])
sig=rd(hdr,'<I',4)[0]
# 0xC0DEC0DE = SteamStub v3.0/3.1 ; 0xC0DEC0DF = SteamStub v3.1.2 (same 0xF0 header layout in Steamless)
if sig not in (0xC0DEC0DE,0xC0DEC0DF): raise SystemExit('not a SteamStub v3.1 header: '+hex(sig))
print('steamstub signature',hex(sig))
(imgbase,aoep,bindoff,unk0,oep,unk1,payload,drmoff,drmsize,appid,flags,bindvs,unk2,codeva,coderaw)=rd(hdr,'<QQIIQIIIIIIIIQQ',8)
key=hdr[0x58:0x78]; iv=hdr[0x78:0x88]; stolen=hdr[0x88:0x98]
print('sig ok; appid',appid,'flags',hex(flags),'oep',hex(oep),'codeva',hex(codeva),'coderaw',hex(coderaw))
NOENC=0x04  # DrmFlags.NoEncryption
if flags & NOENC: print('not encrypted'); sys.exit(1)
cs=[s for s in secs if s[1]==codeva][0]; n,va,vs,ro,rs=cs
# rebuild IV: ECB-decrypt IV with key
ecb=Cipher(algorithms.AES(key),modes.ECB()).decryptor(); iv2=ecb.update(iv)+ecb.finalize()
data=bytes(stolen)+bytes(d[ro:ro+rs])
cbc=Cipher(algorithms.AES(key),modes.CBC(iv2)).decryptor(); dec=cbc.update(data)+cbc.finalize()
# write decrypted text back at the section's raw offset (keep file layout; stolen 16 bytes are the section head)
d[ro:ro+rs]=dec[:rs]
# restore original entry point
struct.pack_into('<I',d,pe+24+16,oep)
open(dst,'wb').write(d)
s=dec[:1<<20]; c=collections.Counter(s); H=-sum(x/len(s)*math.log2(x/len(s)) for x in c.values())
print('decrypted .text entropy %.2f'%H,'CC runs',s.count(b'\xcc\xcc\xcc'),'-> wrote',dst)
