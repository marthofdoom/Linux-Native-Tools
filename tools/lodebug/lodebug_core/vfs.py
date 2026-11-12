"""Virtual file system over an MO2 instance.

Precedence implemented (documented in README, see 'least sure' list):
  1. loose files: overwrite, then enabled mods by modlist priority (top of modlist.txt wins), then game Data;
  2. BSAs of ACTIVE plugins (a plugin loads '<name>.bsa' and '<name> - *.bsa'); a later plugin's BSA beats an
     earlier one's. Loose files always beat any BSA.
"""
import os, re
from .instance import ci_child, ci_walk
from .bsa import Bsa, BsaError

class Provider:
    def __init__(self, kind, owner, path, bsa=None, entry=None, plugin=None):
        self.kind, self.owner, self.path, self.bsa, self.entry, self.plugin = kind, owner, path, bsa, entry, plugin
    def read(self):
        if self.kind == 'loose':
            with open(self.path, 'rb') as f: return f.read()
        return self.bsa.read(self.entry)
    def describe(self):
        if self.kind == 'loose': return f"loose  [{self.owner}]  {self.path}"
        return f"bsa    [{self.owner}]  {os.path.basename(self.bsa.path)} (plugin {self.plugin})  {self.path}"

class VFS:
    def __init__(self, inst):
        self.inst = inst
        self.layers = inst.layers()
        self._archives = None
        self._bsa_cache = {}

    # ---- archives -------------------------------------------------------
    def archives(self):
        """[(plugin, owner label, path)] in load order (later = higher priority)."""
        if self._archives is not None: return self._archives
        tops = {k: v for k, v in self.inst.top_index().items() if k.endswith('.bsa')}
        out = []
        for pl in self.inst.plugins():
            if not pl.active: continue
            stem = pl.name.rsplit('.', 1)[0].lower()
            for n in sorted(tops):
                if n == stem + '.bsa' or n.startswith(stem + ' - '):
                    out.append((pl.name, tops[n][0], tops[n][1]))
        self._archives = out
        return out

    def open_bsa(self, path):
        b = self._bsa_cache.get(path)
        if b is None:
            try: b = Bsa(path)
            except BsaError as e:
                b = None
            self._bsa_cache[path] = b
        return b

    # ---- lookups --------------------------------------------------------
    def which(self, rel):
        """All providers of a Data-relative path, winner first."""
        rel = rel.replace('\\', '/').strip('/').lstrip('/')
        if rel.lower().startswith('data/'): rel = rel[5:]
        res = []
        for label, base in self.layers:
            p = ci_walk(base, rel)
            if p and os.path.isfile(p): res.append(Provider('loose', label, p))
        low = rel.lower()
        for plugin, label, path in reversed(self.archives()):
            b = self.open_bsa(path)
            if not b: continue
            e = b.find(low)
            if e: res.append(Provider('bsa', label, rel, b, e, plugin))
        return res

    def list_dir_files(self, reldir, ext):
        """name_lower -> [Provider winner-first] for files directly in reldir with extension ext
        (loose + BSA)."""
        found = {}
        reldir = reldir.strip('/').lower()
        for label, base in self.layers:
            d = ci_walk(base, reldir)
            if not d or not os.path.isdir(d): continue
            try: names = os.listdir(d)
            except OSError: continue
            for n in names:
                if n.lower().endswith(ext):
                    full = os.path.join(d, n)
                    if os.path.isfile(full):
                        found.setdefault(n.lower(), []).append(Provider('loose', label, full))
        for plugin, label, path in reversed(self.archives()):
            b = self.open_bsa(path)
            if not b: continue
            for e in b.entries:
                if e[0] == reldir and e[1].endswith(ext):
                    found.setdefault(e[1], []).append(Provider('bsa', label, reldir + '/' + e[1], b, e, plugin))
        return found
