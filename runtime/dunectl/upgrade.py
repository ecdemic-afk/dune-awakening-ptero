"""`upgrade`: install a new version of this project without a Reinstall.

Reinstall re-validates the whole 5 GB game download just to replace a few
scripts. This downloads only the project from GitHub (same source as the
install), stages it while the server keeps running, and swaps it in during
a restart. The Kubernetes stand-in is a compiled binary: it is carried over, and
if its source changed the operator is told a Reinstall is needed for that part.
"""
from __future__ import annotations

import hashlib
import io
import os
import shutil
import tarfile
import urllib.request

SWAPPED = ("scripts", "runtime", "data")
SRC_DIRS = {"scripts": "core/scripts", "runtime": "runtime", "data": "core/data"}
BINARY = "mock-k8s-go"


def download(repo: str, ref: str, timeout: int = 120) -> bytes:
    req = urllib.request.Request(f"https://codeload.github.com/{repo}/tar.gz/{ref}",
                                 headers={"User-Agent": "dune-awakening-ptero"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def stage(base: str, tarball: bytes) -> str:
    """Unpack into base/.upgrade/new and return its path. Raises ValueError on a bad archive."""
    root = os.path.join(base, ".upgrade")
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root)
    with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as tf:
        try:
            tf.extractall(root, filter="data")  # refuses absolute paths and links out of root
        except TypeError:  # Python < 3.11.4 (Debian bookworm ships 3.11.2): check by hand
            real_root = os.path.realpath(root)
            for m in tf.getmembers():
                target = os.path.realpath(os.path.join(root, m.name))
                if not target.startswith(real_root + os.sep) or m.islnk() or m.issym() or m.isdev():
                    raise ValueError(f"unsafe path in the download: {m.name}")
            tf.extractall(root)
    tops = [d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d))]
    if len(tops) != 1:
        raise ValueError("unexpected archive layout")
    src = os.path.join(root, tops[0])
    for rel in SRC_DIRS.values():
        if not os.path.isdir(os.path.join(src, rel)):
            raise ValueError(f"the download has no {rel}/ — wrong repository or version?")
    new = os.path.join(root, "new")
    os.rename(src, new)
    return new


def _tree_hash(path: str) -> str:
    h = hashlib.sha256()
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames.sort()
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            h.update(os.path.relpath(full, path).replace(os.sep, "/").encode())
            with open(full, "rb") as fh:
                h.update(fh.read())
    return h.hexdigest()


def mock_k8s_changed(base: str, new: str) -> bool:
    old_src = os.path.join(base, "mock-k8s")
    new_src = os.path.join(new, "core", "mock-k8s")
    if not (os.path.isdir(old_src) and os.path.isdir(new_src)):
        return False
    return _tree_hash(old_src) != _tree_hash(new_src)


def swap(base: str, new: str) -> None:
    """Replace scripts/, runtime/, data/ with the staged copy (server stopped).
    The previous copies are kept in .upgrade/previous/ for a manual rollback."""
    prev = os.path.join(base, ".upgrade", "previous")
    shutil.rmtree(prev, ignore_errors=True)
    os.makedirs(prev)
    binary = os.path.join(base, "scripts", BINARY)
    keep_binary = os.path.join(prev, BINARY)
    if os.path.exists(binary):
        shutil.copy2(binary, keep_binary)
    for name in SWAPPED:
        cur = os.path.join(base, name)
        if os.path.exists(cur):
            os.rename(cur, os.path.join(prev, name))
        shutil.copytree(os.path.join(new, SRC_DIRS[name]), cur)
    if os.path.exists(keep_binary):
        shutil.copy2(keep_binary, os.path.join(base, "scripts", BINARY))
    for sh in os.listdir(os.path.join(base, "scripts")):
        if sh.endswith(".sh") or sh == BINARY:
            p = os.path.join(base, "scripts", sh)
            os.chmod(p, os.stat(p).st_mode | 0o111)
    shutil.rmtree(new, ignore_errors=True)
