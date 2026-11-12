"""Papyrus compiled script (.pex) reader for Skyrim.
Format reference: UESP wiki 'Skyrim Mod:Compiled Script File Format' (public documentation). Big-endian.

Header: magic 0xFA57C0DE, major u8, minor u8, gameId u16, compileTime u64, 3 wstrings (source, user, machine).
String table: u16 count + wstrings (wstring = u16 length + bytes). Debug info: u8 present; if so u64 time,
u16 count, per function {u16 object, u16 state, u16 name, u8 type, u16 nLines, u16 lines[]}.
User flags: u16 count x {u16 name, u8 bit}. Objects: u16 count x {u16 name, u32 size, u16 parent, u16 doc,
u32 userFlags, u16 autoState, u16 nVars x {u16 name, u16 type, u32 flags, Value}, u16 nProps x {u16 name,
u16 type, u16 doc, u32 flags, u8 pflags, [u16 autoVar if pflags&4, else function if &1, function if &2]},
u16 nStates x {u16 name, u16 nFuncs x {u16 name, Function}}}.
Function: u16 ret, u16 doc, u32 userFlags, u8 flags(1 global, 2 native), u16 nParams x {name,type},
u16 nLocals x {name,type}, u16 nInstr x Instruction.
Instruction: u8 opcode + fixed Values (+ for callmethod/callparent/callstatic: a Value giving the vararg
count, then that many Values). Value: u8 type (0 none, 1 identifier(string idx), 2 string(idx), 3 int32,
4 float32, 5 bool u8) + payload.
"""
import struct

OPS = {  # opcode: (name, fixed arg count, has varargs)
    0: ('nop', 0, 0), 1: ('iadd', 3, 0), 2: ('fadd', 3, 0), 3: ('isub', 3, 0), 4: ('fsub', 3, 0),
    5: ('imul', 3, 0), 6: ('fmul', 3, 0), 7: ('idiv', 3, 0), 8: ('fdiv', 3, 0), 9: ('imod', 3, 0),
    10: ('not', 2, 0), 11: ('ineg', 2, 0), 12: ('fneg', 2, 0), 13: ('assign', 2, 0), 14: ('cast', 2, 0),
    15: ('cmp.eq', 3, 0), 16: ('cmp.lt', 3, 0), 17: ('cmp.le', 3, 0), 18: ('cmp.gt', 3, 0), 19: ('cmp.ge', 3, 0),
    20: ('jmp', 1, 0), 21: ('jmpt', 2, 0), 22: ('jmpf', 2, 0),
    23: ('callmethod', 3, 1), 24: ('callparent', 2, 1), 25: ('callstatic', 3, 1),
    26: ('return', 1, 0), 27: ('strcat', 3, 0), 28: ('propget', 3, 0), 29: ('propset', 3, 0),
    30: ('array.create', 2, 0), 31: ('array.length', 2, 0), 32: ('array.get', 3, 0), 33: ('array.set', 3, 0),
    34: ('array.find', 4, 0), 35: ('array.rfind', 4, 0),
}

class PexError(Exception): pass

class R:
    def __init__(self, b): self.b, self.p = b, 0
    def u8(self): v = self.b[self.p]; self.p += 1; return v
    def u16(self): v = struct.unpack_from('>H', self.b, self.p)[0]; self.p += 2; return v
    def u32(self): v = struct.unpack_from('>I', self.b, self.p)[0]; self.p += 4; return v
    def i32(self): v = struct.unpack_from('>i', self.b, self.p)[0]; self.p += 4; return v
    def f32(self): v = struct.unpack_from('>f', self.b, self.p)[0]; self.p += 4; return v
    def u64(self): v = struct.unpack_from('>Q', self.b, self.p)[0]; self.p += 8; return v
    def wstr(self):
        n = self.u16(); s = self.b[self.p:self.p+n]; self.p += n
        return s.decode('latin1')

class Func:
    def __init__(self):
        self.name = None; self.obj = None; self.state = None; self.flags = 0
        self.params = []; self.locals = []; self.instrs = []; self.kind = 'function'  # function|get|set

class Script:
    def __init__(self, data):
        self.parse(data)

    def s(self, i):
        return self.strings[i] if 0 <= i < len(self.strings) else f'<str#{i}>'

    def value(self, r):
        t = r.u8()
        if t == 0: return ('none', None)
        if t == 1: return ('id', self.s(r.u16()))
        if t == 2: return ('str', self.s(r.u16()))
        if t == 3: return ('int', r.i32())
        if t == 4: return ('float', r.f32())
        if t == 5: return ('bool', bool(r.u8()))
        raise PexError(f'bad value type {t} at {r.p}')

    def func(self, r, name, obj, state, kind='function'):
        f = Func(); f.name, f.obj, f.state, f.kind = name, obj, state, kind
        f.ret = self.s(r.u16()); r.u16(); r.u32(); f.flags = r.u8()
        f.params = [(self.s(r.u16()), self.s(r.u16())) for _ in range(r.u16())]
        f.locals = [(self.s(r.u16()), self.s(r.u16())) for _ in range(r.u16())]
        for _ in range(r.u16()):
            op = r.u8()
            if op not in OPS: raise PexError(f'unknown opcode {op} at {r.p-1} in {name}')
            nm, nfix, va = OPS[op]
            args = [self.value(r) for _ in range(nfix)]
            extra = []
            if va:
                cnt = self.value(r)
                if cnt[0] != 'int': raise PexError('vararg count not int')
                extra = [self.value(r) for _ in range(cnt[1])]
            f.instrs.append((nm, args, extra))
        return f

    def parse(self, data):
        r = R(data)
        if r.u32() != 0xFA57C0DE: raise PexError('bad magic')
        self.major, self.minor, self.game = r.u8(), r.u8(), r.u16()
        self.time = r.u64(); self.source = r.wstr(); self.user = r.wstr(); self.machine = r.wstr()
        self.strings = [r.wstr() for _ in range(r.u16())]
        if r.u8():
            r.u64()
            for _ in range(r.u16()):
                r.p += 7
                n = r.u16(); r.p += 2 * n
        for _ in range(r.u16()): r.u16(); r.u8()
        self.objects = []
        for _ in range(r.u16()):
            oname = self.s(r.u16()); size = r.u32(); end = r.p - 4 + size
            o = {'name': oname, 'funcs': [], 'props': [], 'vars': []}
            o['parent'] = self.s(r.u16()); r.u16(); r.u32(); o['auto'] = self.s(r.u16())
            for _ in range(r.u16()):
                n = self.s(r.u16()); t = self.s(r.u16()); r.u32(); self.value(r)
                o['vars'].append((n, t))
            for _ in range(r.u16()):
                n = self.s(r.u16()); t = self.s(r.u16()); r.u16(); r.u32(); pf = r.u8()
                o['props'].append((n, t, pf))
                if pf & 4: r.u16()
                else:
                    if pf & 1: o['funcs'].append(self.func(r, n, oname, '', 'get'))
                    if pf & 2: o['funcs'].append(self.func(r, n, oname, '', 'set'))
            for _ in range(r.u16()):
                sname = self.s(r.u16())
                for _ in range(r.u16()):
                    fname = self.s(r.u16())
                    o['funcs'].append(self.func(r, fname, oname, sname))
            self.objects.append(o)

    def functions(self):
        for o in self.objects:
            yield from o['funcs']

def fmt_val(v):
    k, x = v
    if k == 'none': return 'None'
    if k == 'str': return repr(x)
    if k == 'bool': return str(x)
    return str(x)

# --- light data flow: where did local X get its value (straight-line, last assignment before the call) ---
def origin_of(func, idx, val):
    """Describe a Value used at instruction idx. Straight-line scan only: branches are not followed."""
    k, x = val
    if k != 'id': return fmt_val(val)
    if x.lower() == 'self': return 'self'
    pnames = {n.lower() for n, _t in func.params}
    for j in range(idx - 1, -1, -1):
        nm, args, extra = func.instrs[j]
        if nm in ('callmethod', 'callstatic', 'callparent'):
            dest = args[2] if nm != 'callparent' else args[1]
            if dest == ('id', x):
                if nm == 'callstatic': return f'{args[0][1]}.{args[1][1]}()'
                if nm == 'callmethod': return f'{origin_of(func, j, args[1])}.{args[0][1]}()'
                return f'parent.{args[0][1]}()'
        elif nm == 'propget' and args[2] == ('id', x):
            return f'{origin_of(func, j, args[1])}.{args[0][1]} (property)'
        elif nm in ('assign', 'cast') and args[0] == ('id', x):
            return origin_of(func, j, args[1])
    if x.lower() in pnames: return f'param {x}'
    if x.startswith('::') and x.endswith('_var'): return f'property {x[2:-4]} (auto property, value comes from the plugin)'
    return f'variable {x}'

def entry_points(script, func):
    """Functions in the same script object that (transitively, by name, through calls on self / parent)
    reach `func` and are themselves never called from inside the script (events, fragments). Includes func
    itself when nothing calls it."""
    callers = {}
    for g in script.functions():
        if g.obj != func.obj: continue
        for nm, args, extra in g.instrs:
            if nm == 'callparent': callers.setdefault(str(args[0][1]).lower(), set()).add(g.name)
            elif nm == 'callmethod' and args[1] == ('id', 'self'):
                callers.setdefault(str(args[0][1]).lower(), set()).add(g.name)
    seen = {func.name.lower()}; todo = [func.name]; roots = []
    while todo:
        n = todo.pop(); cs = callers.get(n.lower(), set())
        if not cs: roots.append(n); continue
        for c in cs:
            if c.lower() not in seen: seen.add(c.lower()); todo.append(c)
    return sorted(set(roots), key=str.lower)

def find_calls(script, names_lower):
    """Yield dicts for every call instruction whose called function name is in names_lower."""
    for f in script.functions():
        for i, (nm, args, extra) in enumerate(f.instrs):
            if nm == 'callmethod': called, selfv, cls = args[0][1], args[1], None
            elif nm == 'callstatic': called, selfv, cls = args[1][1], None, args[0][1]
            elif nm == 'callparent': called, selfv, cls = args[0][1], None, None
            else: continue
            if str(called).lower() not in names_lower: continue
            yield {
                'object': f.obj, 'state': f.state or '(auto)', 'function': f.name, 'kind': f.kind,
                'params': [f'{t} {n}' for n, t in f.params], 'call': called, 'static_class': cls,
                'target': origin_of(f, i, selfv) if selfv else None,
                'args': [origin_of(f, i, a) for a in extra], 'instr': i, 'events': entry_points(script, f),
            }

def dump(script):
    out = [f"; {script.source} (compiled by {script.user}@{script.machine}, v{script.major}.{script.minor})"]
    for o in script.objects:
        out.append(f"scriptname {o['name']} extends {o['parent']}")
        for n, t in o['vars']: out.append(f"  var {t} {n}")
        for n, t, pf in o['props']: out.append(f"  property {t} {n} flags={pf}")
        for f in o['funcs']:
            st = f"  state {f.state}" if f.state else ''
            out.append(f"{st}\n  {f.kind} {f.ret} {f.name}({', '.join(t+' '+n for n,t in f.params)}) flags={f.flags}")
            for n, t in f.locals: out.append(f"    local {t} {n}")
            for i, (nm, args, extra) in enumerate(f.instrs):
                a = ' '.join(fmt_val(v) for v in args)
                if extra: a += '  ( ' + ', '.join(fmt_val(v) for v in extra) + ' )'
                out.append(f"    {i:4d}: {nm} {a}")
    return '\n'.join(out)
