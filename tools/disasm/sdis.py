import sys, pefile, capstone, pickle, os
sys.path.insert(0,'/mnt/gaming/modlists/Projects/Linux-Native-Tools/tools/steamstub-rtti')
import addrlib
EXE='/mnt/gaming/modlists/Projects/marth-follower-overhaul/binaries/1.6.1170/SkyrimSE.unpacked.exe'
LIB='/mnt/gaming/modlists/Tuxborn/mods/Address Library for SKSE Plugins/SKSE/Plugins/versionlib-1-6-1170-0.bin'
C=os.path.join(os.path.dirname(__file__),'ids.pkl')
if os.path.exists(C): id2off=pickle.load(open(C,'rb'))
else:
    id2off=addrlib.load(LIB)['map']; pickle.dump(id2off,open(C,'wb'))
off2id={v:k for k,v in id2off.items()}
pe=pefile.PE(EXE,fast_load=True); data=pe.__data__
def rd(rva,n): return pe.get_data(rva,n)
md=capstone.Cs(capstone.CS_ARCH_X86,capstone.CS_MODE_64); md.detail=False
def name(t): return ' <id %d>'%off2id[t] if t in off2id else ''
import struct
def cstr(rva):
    try:
        b=rd(rva,64); b=b[:b.index(0)]
        if len(b)>2 and all(32<=c<127 for c in b): return b.decode()
    except Exception: pass
def gs(t):
    out=''
    for k in (8,):
        try:
            p=struct.unpack('<Q',rd(t+k,8))[0]-0x140000000
            if 0<p<0x4000000:
                s=cstr(p)
                if s: out+=' "%s"'%s
        except Exception: pass
    try:
        f=struct.unpack('<f',rd(t,4))[0]
        if 1e-6<abs(f)<1e7: out+=' (f=%g)'%f
    except Exception: pass
    return out
def dis(rva,n):
    code=rd(rva,n*15)
    for i,ins in enumerate(md.disasm(code,0x140000000+rva)):
        if i>=n: break
        s='%x: %s %s'%(ins.address-0x140000000,ins.mnemonic,ins.op_str)
        if ins.mnemonic in('call','jmp') and ins.op_str.startswith('0x'):
            t=int(ins.op_str,16)-0x140000000; s='%x: %s %x%s'%(ins.address-0x140000000,ins.mnemonic,t,name(t))
        elif 'rip' in ins.op_str:
            import re
            m=re.search(r'\[rip ([+-]) (0x[0-9a-f]+)\]',ins.op_str)
            if m:
                d=int(m.group(2),16)*(1 if m.group(1)=='+' else -1)
                t=ins.address+ins.size+d-0x140000000; s+='   ; =%x%s%s'%(t,name(t),gs(t))
        print(s)
        if ins.mnemonic=='int3': break
if __name__=='__main__':
    a=sys.argv[1]
    rva=id2off[int(a[3:])] if a.startswith('id:') else int(a,16)
    dis(rva,int(sys.argv[2]) if len(sys.argv)>2 else 200)
