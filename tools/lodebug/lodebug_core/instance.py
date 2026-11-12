"""MO2 instance model: mods in priority order, profile load order, plugin -> owning mod.

Formats used (all plain text / INI, public MO2 behaviour):
  modlist.txt   '+name' enabled, '-name' disabled, '*name' unmanaged; FIRST line = HIGHEST priority.
  plugins.txt   '*Name.esp' = enabled (no star = disabled); base masters are implicit.
  loadorder.txt full plugin order.
"""
import os, re, struct
from dataclasses import dataclass, field

# Game facts (not list data): masters the engine always loads when present.
BASE_MASTERS = ['skyrim.esm', 'update.esm', 'dawnguard.esm', 'hearthfires.esm', 'dragonborn.esm']
PLUGIN_EXT = ('.esp', '.esm', '.esl')

def win_to_posix(p):
    """Z:\\mnt\\x (any number of backslashes) -> /mnt/x. Other drives are left alone."""
    p = p.strip()
    m = re.match(r'^@ByteArray\((.*)\)$', p, re.S)
    if m: p = m.group(1)
    p = p.strip('"')
    p = re.sub(r'\\+', '/', p)
    if re.match(r'^[A-Za-z]:/', p):
        if p[0].upper() == 'Z': p = p[2:]
        else: p = '/' + p[0].lower() + '_drive' + p[2:]
    return p

def ci_child(parent, name, _cache={}):
    """Case-insensitive child lookup (Wine/Windows semantics). Returns real path or None."""
    direct = os.path.join(parent, name)
    if os.path.lexists(direct): return direct
    ent = _cache.get(parent)
    if ent is None:
        try: ent = {e.lower(): e for e in os.listdir(parent)}
        except OSError: ent = {}
        _cache[parent] = ent
    r = ent.get(name.lower())
    return os.path.join(parent, r) if r else None

def ci_walk(root, rel):
    cur = root
    for part in rel.replace('\\', '/').strip('/').split('/'):
        cur = ci_child(cur, part)
        if cur is None: return None
    return cur

def read_ini_general(path):
    """Tiny INI reader: flat dict key->value for the [General] section (and lower-cased keys)."""
    out = {}; sec = None
    try: f = open(path, 'r', encoding='utf-8', errors='replace')
    except OSError: return out
    with f:
        for ln in f:
            ln = ln.strip()
            if ln.startswith('['): sec = ln.strip('[]'); continue
            if sec == 'General' and '=' in ln:
                k, v = ln.split('=', 1); out[k.strip().lower()] = v.strip()
    return out

@dataclass
class Mod:
    name: str
    path: str
    prio: int          # 0 = highest priority
    enabled: bool

@dataclass
class Plugin:
    name: str
    index: int
    active: bool
    path: str | None = None
    owner: str = '?'           # mod name, '<game Data>' or '<overwrite>'
    flags: int = 0
    masters: list = field(default_factory=list)

    @property
    def is_esm(self): return bool(self.flags & 1) or self.name.lower().endswith('.esm')
    @property
    def is_esl(self): return bool(self.flags & 0x200) or self.name.lower().endswith('.esl')

class Instance:
    def __init__(self, root, profile=None):
        self.root = os.path.realpath(root)
        ini = read_ini_general(os.path.join(self.root, 'ModOrganizer.ini'))
        self.ini = ini
        self.portable = os.path.exists(os.path.join(self.root, 'portable.txt'))
        sel = ini.get('selected_profile', '')
        sel = re.sub(r'^@ByteArray\((.*)\)$', r'\1', sel)
        self.profile = profile or sel or 'Default'
        self.profile_dir = os.path.join(self.root, 'profiles', self.profile)
        if not os.path.isdir(self.profile_dir):
            have = sorted(os.listdir(os.path.join(self.root, 'profiles'))) if os.path.isdir(os.path.join(self.root, 'profiles')) else []
            raise SystemExit(f"lodebug: profile '{self.profile}' not found in {self.root}/profiles (have: {have})")
        gp = ini.get('gamepath')
        self.game_dir = win_to_posix(gp) if gp else None
        if self.game_dir and not os.path.isdir(self.game_dir):
            # relative fallbacks seen in the wild
            self.game_dir = None
        self.data_dir = ci_child(self.game_dir, 'Data') if self.game_dir else None
        self.mods_dir = self._dir('mod_directory', 'mods')
        self.overwrite_dir = self._dir('overwrite_directory', 'overwrite')
        self.mods = self._load_mods()
        self._plugins = None

    def _dir(self, key, default):
        v = self.ini.get(key)
        if v:
            v = v.replace('%BASE_DIR%', self.root)
            p = win_to_posix(v)
            if os.path.isdir(p): return p
        return os.path.join(self.root, default)

    def _load_mods(self):
        mods = []
        fn = os.path.join(self.profile_dir, 'modlist.txt')
        names = []
        with open(fn, encoding='utf-8', errors='replace') as f:
            for ln in f:
                ln = ln.rstrip('\r\n')
                if not ln or ln[0] == '#': continue
                if ln[0] in '+-*': names.append((ln[1:], ln[0] == '+'))
        for i, (n, en) in enumerate(names):
            if n.endswith('_separator'): continue
            p = ci_child(self.mods_dir, n)
            if p is None: continue
            mods.append(Mod(n, p, i, en))
        return mods

    def enabled_mods(self):
        return [m for m in self.mods if m.enabled]   # highest priority first

    def layers(self):
        """(label, path) from highest to lowest priority: overwrite, mods, game Data."""
        out = []
        if os.path.isdir(self.overwrite_dir): out.append(('<overwrite>', self.overwrite_dir))
        out += [(m.name, m.path) for m in self.enabled_mods()]
        if self.data_dir: out.append(('<game Data>', self.data_dir))
        return out

    def _implicit_active(self):
        s = set(BASE_MASTERS)
        if self.game_dir:
            ccc = ci_child(self.game_dir, 'Skyrim.ccc')
            if ccc:
                with open(ccc, encoding='utf-8', errors='replace') as f:
                    s |= {l.strip().lower() for l in f if l.strip()}
        return s

    def plugins(self):
        if self._plugins is not None: return self._plugins
        order = []
        lo = os.path.join(self.profile_dir, 'loadorder.txt')
        pt = os.path.join(self.profile_dir, 'plugins.txt')
        enabled = set(); listed = []
        if os.path.exists(pt):
            with open(pt, encoding='utf-8', errors='replace') as f:
                for ln in f:
                    ln = ln.strip()
                    if not ln or ln[0] == '#': continue
                    if ln[0] == '*': enabled.add(ln[1:].lower()); listed.append(ln[1:])
                    else: listed.append(ln)
        if os.path.exists(lo):
            with open(lo, encoding='utf-8', errors='replace') as f:
                order = [l.strip() for l in f if l.strip() and l[0] != '#']
        else:
            order = listed
        implicit = self._implicit_active()
        res = []
        for n in order:
            res.append(Plugin(n, len(res), n.lower() in enabled or n.lower() in implicit))
        self._plugins = res
        return res

    def top_index(self):
        """lower file name -> (layer label, path) for files directly in each layer root (first = winner)."""
        if getattr(self, '_top', None) is None:
            t = {}
            for label, base in self.layers():
                try: names = os.listdir(base)
                except OSError: continue
                for n in names:
                    t.setdefault(n.lower(), (label, os.path.join(base, n)))
            self._top = t
        return self._top

    def resolve_plugins(self):
        """Fill path/owner/flags/masters. Disabled plugins are resolved too (info only)."""
        import mmap
        from . import plugins as P
        top = self.top_index()
        for pl in self.plugins():
            hit = top.get(pl.name.lower())
            if hit and os.path.isfile(hit[1]):
                pl.owner, pl.path = hit
            if pl.path:
                try: pl.flags, pl.masters = P.read_tes4(pl.path)
                except Exception: pass
        return self._plugins

    def active_plugins(self):
        return [p for p in self.resolve_plugins() if p.active and p.path]
