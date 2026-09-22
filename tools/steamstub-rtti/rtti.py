import sys,struct,re
sys.path.insert(0,'/tmp/claude-1000/-mnt-gaming-modlists-Projects-marth-follower-overhaul/a935cdde-4a9b-4a50-983f-339ce7b41062/scratchpad/unpack')
from img import Img
from addrlib import DB, AE, SE
BASE=0x140000000
def vtables_for(img, pat):
    d=img.d; out=[]
    for m in re.finditer(pat, d):
        # TypeDescriptor: vtable ptr(8) spare(8) name; name at TD+0x10
        tdoff=m.start()-0x10
        # find TD rva
        for n,va,vs,ro,rs in img.secs:
            if ro<=tdoff<ro+rs: tdrva=va+(tdoff-ro); break
        else: continue
        name=d[m.start():d.find(b'\0',m.start())].decode(errors='replace')
        # COLs: sig(4) offset(4) cdOffset(4) pTD(4 rva) pCHD(4) pSelf(4)
        for cm in re.finditer(struct.pack('<I',tdrva), d):
            coloff=cm.start()-12
            if coloff<0: continue
            sig,off,cd=struct.unpack_from('<III',d,coloff)
            if sig!=1: continue
            for n,va,vs,ro,rs in img.secs:
                if ro<=coloff<ro+rs: colrva=va+(coloff-ro); break
            else: continue
            pself=struct.unpack_from('<I',d,coloff+20)[0]
            if pself!=colrva: continue
            # vtable = the 8-byte pointer to COL (absolute VA) + 8
            for pm in re.finditer(struct.pack('<Q',BASE+colrva), d):
                po=pm.start()
                for n,va,vs,ro,rs in img.secs:
                    if ro<=po<ro+rs: vt=va+(po-ro)+8; break
                else: continue
                out.append((name,off,vt))
    return out
which=sys.argv[1]; pat=sys.argv[2].encode()
img=Img('/mnt/gaming/modlists/Projects/marth-follower-overhaul/binaries/%s/SkyrimSE.exe'%('1.6.1170' if which=='ae' else '1.5.97'))
db=DB(AE if which=='ae' else SE)
nslots=int(sys.argv[3]) if len(sys.argv)>3 else 6
for name,off,vt in sorted(set(vtables_for(img,pat))):
    ids=db.rva2id(vt); slots=[]
    for i in range(nslots):
        try: f=img.u64(vt+8*i)-BASE
        except Exception: break
        if not (0x1000<=f<0x1800000): break
        sid=db.rva2id(f); slots.append('%d:%x(%s)'%(i,f,','.join(map(str,sid)) if sid else '-'))
    print('%s off=%d vt=%x id=%s | %s'%(name,off,vt,ids,' '.join(slots)))
