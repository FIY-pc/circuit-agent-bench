"""The vibe-logisim implementation bench measures, and its headless host.

Which implementation (`Tree.resolve`):

  default     the tree bench/ sits in (its parent directory)
  --tree P    another local vibe-logisim tree
  --ref R     a tag, branch or commit: cloned into the cache
              (~/.cache/vibe-bench/trees/<sha>, `VIBE_BENCH_CACHE` moves it),
              `npm ci`, `npm run setup`, `cargo build --release`. The source
              repository is only read; a ref it does not know is cloned from
              the public repository (`VIBE_BENCH_REPO` overrides the URL).

`VIBE_BENCH_TREE` / `VIBE_BENCH_REF` stand in for --tree / --ref (scripts
and tests without those options). A local tree's host is built with
`cargo build --release` in that tree (a no-op when it is current);
`VIBE_BENCH_HOST=<binary>` uses a binary as is.
Every run records `Tree.describe()`: commit, ref name, whether the tree had
uncommitted changes, and which of the three ways chose it.

    tree = Tree.resolve()
    with Host(tree) as host:
        doc = host.call('open', path='/home/user/x.circ')
        host.call('observe', id=doc['id'], revision=doc['revision'], circuit='main')
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

BENCH = Path(__file__).resolve().parents[1]
CACHE = Path(os.environ.get('VIBE_BENCH_CACHE') or Path.home() / '.cache' / 'vibe-bench')
PUBLIC_REPO = 'https://github.com/FIY-pc/vibe-logisim.git'
EXE = '.exe' if os.name == 'nt' else ''
RUNTIME_FILES = {'logisim.jar': 'Logisim-ITA.jar', 'logisim-2.15': 'logisim-ita-cn-20200118.exe'}


class HostError(RuntimeError):
    """A host request the host refused: `error` is its {code, message, ...}."""

    def __init__(self, op, error):
        self.op, self.error = op, error
        if isinstance(error, dict):
            text = ' '.join(str(error.get(k)) for k in ('code', 'message', 'detail') if error.get(k))
        else:
            text = str(error)
        super().__init__(f'{op}: {text}')


def _tool(name):
    """The executable on PATH (npm is npm.cmd on Windows)."""
    found = shutil.which(name)
    if not found:
        raise SystemExit(f'找不到 {name}：bench 需要它在 PATH 上')
    return found


def _run(cmd, cwd=None, env=None):
    print('>', ' '.join(str(c) for c in cmd), flush=True)
    p = subprocess.run([str(c) for c in cmd], cwd=cwd, env=env)
    if p.returncode:
        raise SystemExit(f'失败（{p.returncode}）：{" ".join(str(c) for c in cmd)}')


def _git(root, *args):
    p = subprocess.run([_tool('git'), '-C', str(root), *args], capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else None


def _sha256(path: Path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


@dataclass
class Tree:
    root: Path
    source: str               # 'parent' | 'tree' | 'ref'
    ref: str | None           # the branch, tag or ref name
    commit: str | None
    dirty: bool | None
    _host: Path | None = None

    def describe(self):
        return {'commit': self.commit, 'ref': self.ref, 'dirty': self.dirty, 'source': self.source}

    @property
    def label(self):
        """Short commit, `+dirty` when the tree had uncommitted changes."""
        return (self.commit or 'unknown')[:8] + ('+dirty' if self.dirty else '')

    @classmethod
    def resolve(cls, tree=None, ref=None):
        if tree is None and ref is None:
            tree, ref = os.environ.get('VIBE_BENCH_TREE') or None, os.environ.get('VIBE_BENCH_REF') or None
        if tree and ref:
            raise SystemExit('--tree 和 --ref 只能给一个')
        if ref:
            return cls._from_ref(ref)
        root = Path(tree).expanduser().resolve() if tree else BENCH.parent
        if not (root / 'crates/workbench-host').is_dir() or not (root / 'packages/agent-runtime').is_dir():
            how = '' if tree else '（bench 的上一级）'
            raise SystemExit(f'{root}{how} 不是重写版 vibe-logisim 的源码树（没有 crates/workbench-host）；'
                             '用 --tree 指向一棵，或用 --ref <tag|commit> 让 bench 自己取一份')
        commit = _git(root, 'rev-parse', 'HEAD')
        name = _git(root, 'symbolic-ref', '-q', '--short', 'HEAD') or _git(root, 'describe', '--tags', '--exact-match', 'HEAD')
        status = _git(root, 'status', '--porcelain')
        return cls(root, 'tree' if tree else 'parent', name, commit, None if status is None else bool(status))

    @classmethod
    def _from_ref(cls, ref):
        source = BENCH.parent
        sha = _git(source, 'rev-parse', '--verify', '--quiet', f'{ref}^{{commit}}')
        trees = CACHE / 'trees'
        if sha and (trees / sha / '.git').exists():
            return cls(trees / sha, 'ref', ref, sha, False)._prepare()
        trees.mkdir(parents=True, exist_ok=True)
        scratch = Path(tempfile.mkdtemp(prefix='clone-', dir=trees))
        git = _tool('git')
        try:
            if sha:
                # A local clone hardlinks the objects and only reads the source.
                _run([git, 'clone', '-q', '--no-checkout', source, scratch / 'tree'])
                if _git(scratch / 'tree', 'cat-file', '-e', f'{sha}^{{commit}}') is None:
                    _run([git, '-C', scratch / 'tree', 'fetch', '-q', '--no-tags', source, '+refs/*:refs/bench-source/*'])
            else:
                url = os.environ.get('VIBE_BENCH_REPO') or PUBLIC_REPO
                _run([git, 'clone', '-q', '--no-checkout', url, scratch / 'tree'])
                sha = (_git(scratch / 'tree', 'rev-parse', '--verify', '--quiet', f'{ref}^{{commit}}')
                       or _git(scratch / 'tree', 'rev-parse', '--verify', '--quiet', f'origin/{ref}^{{commit}}'))
                if not sha:
                    raise SystemExit(f'本地仓库和 {url} 里都没有 {ref}')
            _run([git, '-C', scratch / 'tree', 'checkout', '-q', '--detach', sha])
            target = trees / sha
            if not (target / '.git').exists():
                if target.exists():
                    shutil.rmtree(target)
                (scratch / 'tree').rename(target)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        return cls(trees / sha, 'ref', ref, sha, False)._prepare()

    def _prepare(self):
        """Dependencies, runtimes and the release host of a cached tree, once."""
        stamp = CACHE / 'hosts' / self.commit / 'ready.json'
        binary = stamp.parent / f'vibe-workbench-host{EXE}'
        if stamp.is_file() and binary.is_file():
            self._host = binary
            return self
        root = self.root
        npm = _tool('npm')
        _run([npm, 'ci', '--no-audit', '--no-fund', '--prefer-offline'], cwd=root)
        self._seed_runtimes()
        _run([npm, 'run', 'setup'], cwd=root)
        target = CACHE / 'cargo-target'
        env = {**os.environ, 'CARGO_TARGET_DIR': str(target)}
        _run([_tool('cargo'), 'build', '--release', '-p', 'vibe-workbench-host', '--bin', 'vibe-workbench-host'], cwd=root, env=env)
        stamp.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target / 'release' / f'vibe-workbench-host{EXE}', binary)
        stamp.write_text(json.dumps({'commit': self.commit, 'ref': self.ref, 'built': time.strftime('%Y-%m-%dT%H:%M:%S')}), encoding='utf-8')
        self._host = binary
        return self

    def _seed_runtimes(self):
        """Copy the pinned Logisim runtimes from a tree that has them, so
        `npm run setup` finds them in place instead of downloading."""
        lock = json.loads((self.root / 'config/runtime-lock.json').read_text(encoding='utf-8'))
        cache = self.root / '.workbench-cache'
        worktrees = [Path(line[9:]) for line in (_git(BENCH.parent, 'worktree', 'list', '--porcelain') or '').splitlines()
                     if line.startswith('worktree ')]
        donors = [d / '.workbench-cache' for d in [BENCH.parent, *worktrees, *sorted((CACHE / 'trees').glob('*'))]]
        for artifact, name in RUNTIME_FILES.items():
            want = (lock.get('artifacts', {}).get(artifact) or {}).get('sha256')
            if not want or (cache / name).is_file():
                continue
            for donor in donors:
                f = donor / name
                if f.is_file() and _sha256(f) == want:
                    cache.mkdir(exist_ok=True)
                    shutil.copy2(f, cache / name)
                    break

    def host_binary(self) -> Path:
        if self._host:
            return self._host
        explicit = os.environ.get('VIBE_BENCH_HOST')
        if explicit:
            self._host = Path(explicit)
            return self._host
        cargo = _tool('cargo')
        _run([cargo, 'build', '--release', '-p', 'vibe-workbench-host', '--bin', 'vibe-workbench-host'], cwd=self.root)
        meta = subprocess.run([cargo, 'metadata', '--no-deps', '--format-version', '1'], cwd=self.root, capture_output=True, text=True, check=True)
        self._host = Path(json.loads(meta.stdout)['target_directory']) / 'release' / f'vibe-workbench-host{EXE}'
        return self._host

    def check_runtimes(self):
        if not (self.root / 'node_modules').is_dir():
            raise SystemExit(f'{self.root} 还没装依赖：在那里运行 npm ci 和 npm run setup')
        if not any((self.root / d / 'Logisim-ITA.jar').is_file() for d in ('.workbench-cache', 'native')):
            raise SystemExit(f'{self.root} 还没有 Logisim 运行时：在那里运行 npm run setup')


class Host:
    """One headless vibe-workbench-host on a free loopback port.

    `env` adds environment variables (the sidecar inherits them); `state` is
    the host's state directory (a temporary one by default); `log` receives
    the host's and sidecar's output."""

    READY = re.compile(r'Vibe Logisim Rust host: (http://\S+)')

    def __init__(self, tree: Tree, *, state=None, env=None, log=None):
        self.tree, self.extra_env = tree, dict(env or {})
        self.state = Path(state) if state else None
        self.log_path = Path(log) if log else None
        self.proc = None
        self.url = None
        self._own_state = False
        self._log = None
        self._streams = []

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.close()

    def start(self):
        self.tree.check_runtimes()
        binary = self.tree.host_binary()
        if not binary.is_file():
            raise SystemExit(f'找不到宿主程序 {binary}')
        if self.state is None:
            self.state = Path(tempfile.mkdtemp(prefix='vibe-bench-host-'))
            self._own_state = True
        self.state.mkdir(parents=True, exist_ok=True)
        _share_java(self.state, into_state=True)
        env = {**os.environ, 'VIBE_WORKBENCH_STATE': str(self.state), 'VIBE_WORKBENCH_PORT': '0',
               'VIBE_WORKBENCH_DEV_PORT': '0', 'VIBE_WORKBENCH_RESOURCES': str(self.tree.root), **self.extra_env}
        self._log = self.log_path.open('a', encoding='utf-8') if self.log_path else None
        kwargs = {'start_new_session': True} if os.name != 'nt' else {'creationflags': 0x00000200}
        self.proc = subprocess.Popen([str(binary)], env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', **kwargs)
        ready = queue.Queue()
        threading.Thread(target=self._pump, args=(ready,), daemon=True).start()
        try:
            self.url = ready.get(timeout=120)
        except queue.Empty:
            self.close()
            raise RuntimeError('宿主 120 s 内没有就绪')
        if not self.url:
            code = self.proc.wait()
            raise RuntimeError(f'宿主启动失败（退出码 {code}）' + (f'，见 {self.log_path}' if self.log_path else ''))
        return self

    def _pump(self, ready):
        found = False
        for line in self.proc.stdout:
            if self._log:
                self._log.write(line)
                self._log.flush()
            if not found:
                m = self.READY.search(line)
                if m:
                    found = True
                    ready.put(m.group(1))
        if not found:
            ready.put(None)

    def call(self, op, *, timeout=900, **args):
        body = json.dumps({'op': op, **args}, ensure_ascii=False).encode('utf-8')
        request = urllib.request.Request(self.url + '/api/command', data=body, headers={'content-type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as r:
                return json.loads(r.read().decode('utf-8')).get('result')
        except urllib.error.HTTPError as e:
            raw = e.read().decode('utf-8', 'replace')
            try:
                error = json.loads(raw).get('error')
            except ValueError:
                error = raw[:400]
            raise HostError(op, error) from None

    def agent(self, method, timeout=900, **args):
        return self.call('agent', method=method, args=args, timeout=timeout)

    def events(self):
        """Open /api/events; returns an EventStream that collects every event."""
        stream = EventStream(self.url)
        self._streams.append(stream)
        return stream

    def close(self):
        for s in self._streams:
            s.close()
        self._streams = []
        p, self.proc = self.proc, None
        if p and p.poll() is None:
            try:
                if os.name == 'nt':
                    p.terminate()
                else:
                    p.send_signal(signal.SIGTERM)
                p.wait(timeout=20)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(timeout=10)
        if p and os.name != 'nt':
            try:   # Java workers or a sidecar the host left behind
                os.killpg(p.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        if self._log:
            self._log.close()
            self._log = None
        if self.state:
            _share_java(self.state, into_state=False)
        if self._own_state and self.state:
            shutil.rmtree(self.state, ignore_errors=True)
            self.state = None


def _share_java(state: Path, *, into_state: bool):
    """The host compiles its Java bridge into <state>/java/<digest> on first
    use; finished builds are kept under the cache and copied into fresh states."""
    shared, local = CACHE / 'java', state / 'java'
    src, dst = (shared, local) if into_state else (local, shared)
    if not src.is_dir():
        return
    for build in src.iterdir():
        if build.is_dir() and (build / 'complete').is_file() and not (dst / build.name).exists():
            scratch = dst / f'.copy-{os.getpid()}-{build.name}'
            try:
                shutil.copytree(build, scratch)
                scratch.rename(dst / build.name)
            except OSError:
                shutil.rmtree(scratch, ignore_errors=True)


class EventStream:
    """The host's server-sent events on a background thread: `events` grows
    as they arrive; `wait(predicate, timeout)` returns the first match."""

    def __init__(self, url):
        self.events = []
        self.times = []   # arrival time of each event (time.time())
        self._cond = threading.Condition()
        self._closed = False
        self._error = None
        self._response = urllib.request.urlopen(url + '/api/events', timeout=None)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        data = []
        try:
            for raw in self._response:
                line = raw.decode('utf-8', 'replace').rstrip('\r\n')
                if line.startswith('data:'):
                    data.append(line[5:].strip())
                elif not line and data:
                    try:
                        event = json.loads('\n'.join(data))
                    except ValueError:
                        event = None
                    data = []
                    if event is not None:
                        with self._cond:
                            self.events.append(event)
                            self.times.append(time.time())
                            self._cond.notify_all()
        except Exception as e:  # the host closed the stream
            self._error = e
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    def wait(self, predicate, timeout, start=0):
        deadline = time.monotonic() + timeout
        with self._cond:
            seen = start
            while True:
                for event in self.events[seen:]:
                    if predicate(event):
                        return event
                seen = len(self.events)
                left = deadline - time.monotonic()
                if left <= 0 or self._closed:
                    return None
                self._cond.wait(min(left, 5))

    def close(self):
        try:
            self._response.close()
        except Exception:
            pass
