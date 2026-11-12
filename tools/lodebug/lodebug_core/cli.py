import argparse, os, re, sys, time, struct
from .instance import Instance
from .safety import check_outputs, ensure_cache
from .vfs import VFS

CRIME_CALLS = ['SendStealAlarm', 'SendTrespassAlarm', 'SendAssaultAlarm', 'SendMurderAlarm', 'ModCrimeGold',
               'ModCrimeGoldViolent', 'SetCrimeGold', 'SetCrimeGoldViolent', 'PlayerPayCrimeGold',
               'SendPlayerToJail']

def get_inst(a):
    check_outputs(os.path.realpath(a.instance), getattr(a, 'out', None))
    ensure_cache()
    return Instance(a.instance, a.profile)

def load_index(inst):
    """Active plugins with the MO2 style load index (00-FD regular, FE:xxx light)."""
    res = []; reg = 0; lt = 0
    for p in inst.resolve_plugins():
        if p.active and p.path:
            if p.is_esl: lab = f"FE:{lt:03X}"; lt += 1
            else: lab = f"{reg:02X}"; reg += 1
        else: lab = '--'
        res.append((lab, p))
    return res

# ---------------------------------------------------------------- order
def cmd_order(a):
    inst = get_inst(a)
    print(f"instance {inst.root}  profile {inst.profile}  portable={inst.portable}")
    print(f"game dir {inst.game_dir}  Data {inst.data_dir}")
    print(f"mods: {len(inst.mods)} listed, {len(inst.enabled_mods())} enabled")
    n = 0
    for lab, p in load_index(inst):
        if a.active_only and lab == '--': continue
        fl = ('M' if p.is_esm else '-') + ('L' if p.is_esl else '-')
        print(f"{lab:>6} {fl} {'on ' if p.active else 'off'} {p.name}   <- {p.owner if p.path else 'NOT FOUND'}")
        n += 1
    print(f"-- {n} plugins listed", file=sys.stderr)

# ---------------------------------------------------------------- record
def cmd_record(a):
    from . import plugins as P
    inst = get_inst(a)
    t0 = time.time()
    want_origin = None
    if a.formid:
        pn, _, ids = a.formid.rpartition(':')
        if not pn: sys.exit('--formid needs <plugin>:<hexid>')
        want_origin = (pn.lower(), int(ids, 16) & 0xFFFFFF)
    if not (a.edid or a.formid): sys.exit('give --edid or --formid')
    hits = []   # (loadidx, plugin, fid, origin, flags, subs, localized)
    for lab, p in load_index(inst):
        if lab == '--': continue
        if want_origin and p.name.lower() != want_origin[0] and want_origin[0] not in [m.lower() for m in p.masters]:
            continue
        try: pl = P.Plug(p.path)
        except Exception as e:
            print(f"warn: cannot read {p.name}: {e}", file=sys.stderr); continue
        with pl:
            try:
                for typ, flags, fid, off, dsz in pl.records(a.type):
                    if a.type and typ.decode() != a.type: continue
                    if want_origin and pl.origin(fid) != want_origin: continue
                    subs = pl.subs(off, dsz, flags)
                    ed = P.edid_of(subs)
                    if a.edid and (ed or '').lower() != a.edid.lower(): continue
                    hits.append((lab, p, typ.decode(), fid, pl.origin(fid), flags, subs, pl.localized))
            except Exception as e:
                print(f"warn: {p.name}: {type(e).__name__}: {e}", file=sys.stderr)
    if not hits:
        print("NOT FOUND in any active plugin"); return
    # group by origin identity
    groups = {}
    for h in hits: groups.setdefault(h[4], []).append(h)
    for origin, hs in groups.items():
        t = hs[0][2]
        ed = P.edid_of(hs[0][6])
        print(f"\n{t} {ed}  origin {origin[0]}:{origin[1]:06X}  ({len(hs)} plugin(s) define/override)")
        prev = None
        for lab, p, typ, fid, org, flags, subs, loc in hs:
            fl = f"{'COMPRESSED ' if flags & 0x40000 else ''}{'DELETED ' if flags & 0x20 else ''}{'DISABLED ' if flags & 0x800 else ''}"
            print(f"  [{lab}] {p.name}  <- {p.owner}   raw FormID {fid:08X} {fl}")
            if typ == 'GMST':
                for st, sd in subs:
                    if st == b'DATA': print(f"      value: {P.decode_gmst(ed, sd, loc)}")
            else:
                cur = {}
                for st, sd in subs:
                    cur.setdefault(st, []).append(sd)
                for st, sd in subs:
                    mark = ''
                    if prev is not None:
                        pv = prev.get(st)
                        mark = '' if pv and sd in pv else '  *CHANGED/NEW*'
                    lbl = st.decode('ascii', 'replace')
                    if st == b'EDID': print(f"      {lbl}: {P.cstr(sd)}")
                    elif st == b'FULL': print(f"      {lbl}: {P.full_of([(st, sd)], loc)}{mark}")
                    else: print(f"      {lbl} [{len(sd)}]: {P.hexdump(sd)}{mark}")
                if prev is not None:
                    for st in prev:
                        if st not in cur: print(f"      {st.decode('ascii','replace')}: *REMOVED vs previous layer*")
                prev = cur
        w = hs[-1]
        extra = ''
        if w[2] == 'GMST':
            for st, sd in w[6]:
                if st == b'DATA': extra = f" => {P.decode_gmst(ed, sd, w[7])}"
        print(f"  WINNER by load order: {w[1].name} (<- {w[1].owner}){extra}")
    if len(groups) > 1 and (a.type or hits[0][2]) == 'GMST':
        w = hits[-1]
        print(f"\nEDID-keyed effective winner (GMSTs are looked up by name): {w[1].name}")
    print(f"-- {time.time()-t0:.1f}s", file=sys.stderr)

# ---------------------------------------------------------------- grep-records
def cmd_grep_records(a):
    from . import plugins as P
    inst = get_inst(a)
    rx = re.compile(a.edid_regex, re.I) if a.edid_regex else None
    frx = re.compile(a.full_regex, re.I) if a.full_regex else None
    types = [t.strip().upper() for t in a.type.split(',')]
    n = 0
    for lab, p in load_index(inst):
        if lab == '--': continue
        with P.Plug(p.path) as pl:
            for t in types:
                try:
                    for typ, flags, fid, off, dsz in pl.records(t):
                        if typ.decode() != t: continue
                        subs = pl.subs(off, dsz, flags)
                        ed = P.edid_of(subs) or ''; fu = P.full_of(subs, pl.localized) or ''
                        if rx and not rx.search(ed): continue
                        if frx and not frx.search(fu): continue
                        o = pl.origin(fid)
                        print(f"[{lab}] {p.name}  {t} {ed}  FULL={fu!r}  origin {o[0]}:{o[1]:06X}  <- {p.owner}")
                        n += 1
                except Exception as e:
                    print(f"warn: {p.name} {t}: {e}", file=sys.stderr)
    print(f"-- {n} records", file=sys.stderr)

# ---------------------------------------------------------------- bsa
def cmd_bsa(a):
    from .bsa import Bsa
    b = Bsa(a.archive)
    if a.action == 'ls':
        for p in sorted(b.paths()):
            if not a.filter or a.filter.lower() in p: print(p)
    else:
        d = b.read_path(a.path)
        if d is None: sys.exit(f"{a.path} not in {a.archive}")
        sys.stdout.buffer.write(d)

# ---------------------------------------------------------------- vfs
def cmd_vfs(a):
    inst = get_inst(a)
    v = VFS(inst)
    ps = v.which(a.path)
    if not ps: print("NOT FOUND"); return
    for i, p in enumerate(ps): print(('WINNER ' if i == 0 else 'shadow ') + p.describe())

# ---------------------------------------------------------------- pex
def expand_calls(spec):
    out = []
    for n in spec.split(','):
        n = n.strip()
        if not n: continue
        if n.lower() == 'crime': out += CRIME_CALLS
        else: out.append(n)
    return out

def cmd_pex_calls(a):
    from . import pex as X
    inst = get_inst(a)
    t0 = time.time()
    names = expand_calls(a.call)
    lower = {n.lower() for n in names}
    needles = [n.lower().encode() for n in names]
    v = VFS(inst)
    scripts = v.list_dir_files('scripts', '.pex')
    print(f"# {len(scripts)} winning scripts; looking for {', '.join(names)}", file=sys.stderr)
    nhit = 0; nbad = 0
    for key in sorted(scripts):
        provs = scripts[key]; w = provs[0]
        try: data = w.read()
        except Exception as e:
            print(f"warn: cannot read {w.describe()}: {e}", file=sys.stderr); nbad += 1; continue
        low = data.lower()
        if not any(nd in low for nd in needles): continue
        try: sc = X.Script(data)
        except Exception as e:
            print(f"warn: pex parse failed {key}: {type(e).__name__}: {e}", file=sys.stderr); nbad += 1; continue
        calls = list(X.find_calls(sc, lower))
        if not calls: continue
        shadow = f"  (shadows {len(provs)-1} other copy/copies)" if len(provs) > 1 else ''
        print(f"\nSCRIPT scripts/{key}  {w.describe()}{shadow}")
        funcs = {(f.obj, f.name.lower()): f for f in sc.functions()}
        for c in calls:
            nhit += 1
            print(f"  {c['object']} state {c['state']} {c['kind']} {c['function']}({', '.join(c['params'])})")
            tgt = c['target'] or (c['static_class'] + ' (static)')
            print(f"     -> {c['call']} on {tgt}   args: [{', '.join(c['args'])}]   (instr {c['instr']})")
            evs = []
            for e in c['events']:
                f = funcs.get((c['object'], e.lower()))
                evs.append(f"{e}({', '.join(t+' '+n for n,t in f.params)})" if f else e)
            print(f"     entered via: {', '.join(evs)}")
    print(f"-- {nhit} call sites, {nbad} unreadable, {time.time()-t0:.1f}s", file=sys.stderr)

def cmd_pex_dump(a):
    from . import pex as X
    if os.path.isfile(a.path): data = open(a.path, 'rb').read()
    else:
        if not a.instance: sys.exit('path is not a file; give --instance to resolve a Data path')
        ps = VFS(get_inst(a)).which(a.path)
        if not ps: sys.exit('NOT FOUND in vfs')
        print(f"; {ps[0].describe()}")
        data = ps[0].read()
    print(X.dump(X.Script(data)))

# ---------------------------------------------------------------- skse-strings
def cmd_skse_strings(a):
    inst = get_inst(a)
    rx = re.compile(a.pattern, re.I)
    v = VFS(inst)
    dlls = v.list_dir_files('skse/plugins', '.dll')
    asc = re.compile(rb'[\x20-\x7e]{4,}')
    for key in sorted(dlls):
        w = dlls[key][0]
        data = w.read(); hits = []
        for m in asc.finditer(data):
            s = m.group().decode('ascii')
            if rx.search(s): hits.append(s)
        for m in re.finditer(rb'(?:[\x20-\x7e]\x00){4,}', data):
            s = m.group().decode('utf-16le')
            if rx.search(s): hits.append('[w] ' + s)
        if hits:
            print(f"\n{key}  {w.describe()}  ({len(hits)} matches)")
            for s in hits[:a.max]: print('   ' + s[:200])
            if len(hits) > a.max: print(f"   ... +{len(hits)-a.max} more")

# ---------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(prog='lodebug', description='Read-only load-order debugger for MO2 Skyrim SE lists')
    sub = ap.add_subparsers(dest='cmd', required=True)
    def inst_args(p, req=True):
        p.add_argument('--instance', required=req, help='MO2 instance dir (contains ModOrganizer.ini)')
        p.add_argument('--profile', help='profile name (default: selected_profile)')
    p = sub.add_parser('order'); inst_args(p); p.add_argument('--active-only', action='store_true'); p.set_defaults(f=cmd_order)
    p = sub.add_parser('record'); inst_args(p); p.add_argument('--type'); p.add_argument('--edid'); p.add_argument('--formid'); p.set_defaults(f=cmd_record)
    p = sub.add_parser('grep-records'); inst_args(p); p.add_argument('--type', required=True); p.add_argument('--edid-regex'); p.add_argument('--full-regex'); p.set_defaults(f=cmd_grep_records)
    p = sub.add_parser('bsa'); p.add_argument('action', choices=['ls', 'cat']); p.add_argument('archive'); p.add_argument('path', nargs='?'); p.add_argument('--filter'); p.set_defaults(f=cmd_bsa)
    p = sub.add_parser('vfs'); p.add_argument('action', choices=['which']); p.add_argument('path'); inst_args(p); p.set_defaults(f=cmd_vfs)
    p = sub.add_parser('pex-calls'); inst_args(p)
    p.add_argument('--call', default='crime', help="comma list; 'crime' = " + ','.join(CRIME_CALLS) + " (add e.g. StartCombat yourself)")
    p.add_argument('--enabled-only', action='store_true', help='accepted for clarity: only enabled mods/active plugins are ever considered')
    p.set_defaults(f=cmd_pex_calls)
    p = sub.add_parser('pex-dump'); p.add_argument('path'); inst_args(p, False); p.set_defaults(f=cmd_pex_dump)
    p = sub.add_parser('skse-strings'); inst_args(p); p.add_argument('--pattern', required=True); p.add_argument('--max', type=int, default=20); p.set_defaults(f=cmd_skse_strings)
    a = ap.parse_args(argv)
    a.f(a)
