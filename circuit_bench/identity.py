"""Content identities shared by task packaging and all harness integrations."""
import hashlib
from pathlib import Path

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest_tree(root):
    """Input/output package identity, including filenames and bytes."""
    root = Path(root)
    h = hashlib.sha256()
    for p in sorted(root.rglob('*')):
        if p.is_symlink():
            raise ValueError('Benchmark input/output packages must contain regular files, not symlinks')
        if p.is_file() and '__pycache__' not in p.parts:
            h.update(p.relative_to(root).as_posix().encode() + b'\0')
            h.update(bytes.fromhex(sha(p)))
    return h.hexdigest()
