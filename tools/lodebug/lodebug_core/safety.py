"""Read-only guard: refuse to write caches/outputs inside a modlist instance."""
import os, sys

def cache_dir():
    d = os.path.expanduser(os.environ.get('LODEBUG_CACHE', '~/.cache/lodebug'))
    return os.path.realpath(d)

def inside(path, root):
    p = os.path.realpath(path); r = os.path.realpath(root)
    return p == r or p.startswith(r.rstrip(os.sep) + os.sep)

def check_outputs(instance, out=None):
    """Exit if the cache dir or --out resolves inside the instance."""
    for label, p in (('cache dir', cache_dir()), ('--out', out)):
        if p and inside(p, instance):
            sys.exit(f"lodebug: refusing to start: {label} {p} is inside the instance {instance} (read-only tool)")

def ensure_cache():
    d = cache_dir()
    os.makedirs(d, exist_ok=True)
    return d
