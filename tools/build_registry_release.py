#!/usr/bin/env python3
"""Release-time descriptor generation followed by immutable index publication."""
import os, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from registry import index

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    # A corpus whose descriptors are hand-authored has no generators. RELEASE_GENERATORS names the
    # ones shipped for the US public-data corpus; another instance will have some, all or none of
    # them present. Running a generator that is not on disk is not a failure to report -- it is a
    # corpus that does not use it.
    for script in index.RELEASE_GENERATORS:
        path = os.path.join(ROOT, "tools", script)
        if not os.path.isfile(path):
            print(f"skip {script}: not present in this corpus")
            continue
        subprocess.run([sys.executable, path], cwd=ROOT, check=True)
    subprocess.run([sys.executable, os.path.join(ROOT, "registry", "index.py"), "build", "--release"],
                   cwd=ROOT, check=True)
    subprocess.run([sys.executable, os.path.join(ROOT, "registry", "index.py"), "verify", "--release"],
                   cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
