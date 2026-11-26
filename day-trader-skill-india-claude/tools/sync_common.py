#!/usr/bin/env python3
"""Copy common/ into each skill as _common/ so every skill folder works on its own.

    python3 tools/sync_common.py            # write the copies
    python3 tools/sync_common.py --check    # exit 1 if any copy has drifted

common/ is the one place to edit. The copies are checked by tests/test_repo.py.
Standard library only.
"""

import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "common")

# Skills that import _common. Add a skill here when it starts to use it.
USERS = ["trader-scanner", "trader-entry", "trader-exit", "trader-budget", "trader-risk", "trader-timemachine", "trader-alerts"]


def files():
    out = []
    for dirpath, _dirs, names in os.walk(SRC):
        if "__pycache__" in dirpath:
            continue
        for n in names:
            if n.endswith((".py", ".json")):
                out.append(os.path.relpath(os.path.join(dirpath, n), SRC))
    return sorted(out)


def read(path):
    with open(path, "rb") as fh:
        return fh.read()


def check():
    bad = []
    for skill in USERS:
        dst = os.path.join(ROOT, skill, "_common")
        for rel in files():
            a, b = os.path.join(SRC, rel), os.path.join(dst, rel)
            if not os.path.exists(b) or read(a) != read(b):
                bad.append("%s/_common/%s" % (skill, rel))
        if os.path.isdir(dst):
            for dirpath, _d, names in os.walk(dst):
                if "__pycache__" in dirpath:
                    continue
                for n in names:
                    rel = os.path.relpath(os.path.join(dirpath, n), dst)
                    if rel not in files() and n != "__init__.py":
                        bad.append("%s/_common/%s (not in common/)" % (skill, rel))
    return bad


def write():
    for skill in USERS:
        dst = os.path.join(ROOT, skill, "_common")
        if os.path.isdir(dst):
            shutil.rmtree(dst)
        for rel in files():
            os.makedirs(os.path.dirname(os.path.join(dst, rel)), exist_ok=True)
            shutil.copyfile(os.path.join(SRC, rel), os.path.join(dst, rel))
        with open(os.path.join(dst, "__init__.py"), "w") as fh:
            fh.write('"""Copied from common/ by tools/sync_common.py. Do not edit here."""\n')
    return len(USERS)


def main(argv):
    if "--check" in argv:
        bad = check()
        for b in bad:
            print("drifted: " + b)
        if bad:
            print("run: python3 tools/sync_common.py")
        return 1 if bad else 0
    n = write()
    print("copied common/ into %d skills" % n)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
