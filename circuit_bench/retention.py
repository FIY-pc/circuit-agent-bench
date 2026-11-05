"""Retain native conversations and saved projects independently of turn success.

Only the native conversation file is read from private product state. Profiles,
authentication files and arbitrary paths mentioned in messages are never copied.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import threading
import time


def write_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temporary.replace(path)


RECORD_FIELDS = ('id', 'title', 'createdAt', 'updatedAt', 'archived', 'customTitle',
                 'titleGenerated', 'messages', 'transcript', 'compactions', 'checkpoints')
SELECTION_FIELDS = ('model', 'api', 'effort', 'contextWindow', 'maxOutput', 'vision')


class NativeTranscript:
    """Snapshot the product's atomic conversation saves, including inline media."""

    def __init__(self, state, output, interval=0.25):
        self.source = Path(state) / 'conversations.json'
        self.output = Path(output) / 'trajectory'
        self.output.mkdir(parents=True, exist_ok=True)
        self.interval = interval
        self.status = {'status': 'unavailable', 'reason': 'Native conversation not yet saved'}
        if (self.output / 'conversations.json').is_file() and (self.output / 'status.json').is_file():
            self.status = json.loads((self.output / 'status.json').read_text())
        self._signature = None
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()

    def _watch(self):
        while not self._stop.is_set():
            self.snapshot()
            self._stop.wait(self.interval)

    def snapshot(self):
        try:
            if self.source.is_symlink():
                raise ValueError('Native conversation must not be a symlink')
            if not self.source.exists():
                if self.status['status'] == 'retained':
                    self.status['sourceMissing'] = True
                return
            stat = self.source.stat()
            signature = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
            if signature == self._signature:
                return
            source = json.loads(self.source.read_text())
            if source.get('schema') != 'vibe-logisim.conversations/v2':
                raise ValueError('Unsupported native conversation schema')
            exported = {'schema': source['schema'], 'workspaces': {}}
            for key, workspace in source['workspaces'].items():
                records = {}
                for cid, record in workspace['conversations'].items():
                    records[cid] = {name: record[name] for name in RECORD_FIELDS if name in record}
                    selection = record.get('selection') or {}
                    records[cid]['selection'] = {name: selection[name] for name in SELECTION_FIELDS if name in selection}
                exported['workspaces'][key] = {'activeId': workspace['activeId'], 'conversations': records}
            media = {}
            self._media(exported, media)
            write_json(self.output / 'conversations.json', exported)
            write_json(self.output / 'media.json', list(media.values()))
            self.status = {'status': 'retained', 'path': 'trajectory/conversations.json',
                           'media': len(media), 'savedAt': time.time()}
            self._signature = signature
        except (OSError, ValueError, KeyError, TypeError) as error:
            # Preserve the last valid snapshot if a later write cannot be read.
            self.status = {'status': 'error', 'error': f'{type(error).__name__}: {error}'}
        finally:
            write_json(self.output / 'status.json', self.status)

    def _media(self, value, media):
        if isinstance(value, list):
            for item in value:
                self._media(item, media)
        elif isinstance(value, dict):
            # Pi native image content is inline base64. Keep the original in the
            # transcript as well as a viewable, content-addressed media file.
            if value.get('type') == 'image' and isinstance(value.get('data'), str):
                data = base64.b64decode(value['data'], validate=True)
                digest = hashlib.sha256(data).hexdigest()
                mime = value.get('mimeType', 'application/octet-stream')
                suffix = {'image/png': '.png', 'image/jpeg': '.jpg', 'image/webp': '.webp',
                          'image/gif': '.gif'}.get(mime, '.bin')
                relative = 'media/' + digest + suffix
                target = self.output / relative
                target.parent.mkdir(exist_ok=True)
                if not target.exists():
                    target.write_bytes(data)
                media[digest] = {'path': relative, 'mimeType': mime, 'sha256': digest}
            for item in value.values():
                self._media(item, media)

    def close(self):
        self._stop.set()
        if self._thread:
            self._thread.join()
        self.snapshot()
        return self.status


def retain_outputs(state, project, output, transcript=None):
    """Export before removing private state; the live project is never removed."""
    output, project, state = Path(output), Path(project), Path(state)
    errors = []
    try:
        trajectory = (transcript or NativeTranscript(state, output)).close()
    except Exception as error:
        trajectory = {'status': 'error', 'error': f'{type(error).__name__}: {error}'}
    if trajectory['status'] == 'error':
        errors.append('Native transcript export failed; private state retained')
    if project != output / 'result':
        try:
            shutil.copytree(project, output / 'result', symlinks=True, dirs_exist_ok=True)
        except Exception as error:
            errors.append(f'Project snapshot: {type(error).__name__}: {error}')
    if trajectory['status'] != 'error':
        shutil.rmtree(state, ignore_errors=True)
    return {'artifact': {'path': str(project), 'exists': project.is_dir()},
            'trajectory': trajectory, 'retentionErrors': errors}


def process_identity(pid):
    """Linux process start time prevents signalling a recycled PID during recovery."""
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        return None if fields[0] == 'Z' else fields[19]
    except (OSError, IndexError):
        return None


def signal_process(record, sig, *, group=False):
    pid, identity = record.get('pid'), record.get('start')
    if pid and identity and process_identity(pid) == identity:
        try:
            (os.killpg if group else os.kill)(pid, sig)
        except ProcessLookupError:
            pass


def interrupt_episode(output, timeout=35):
    """Harbor calls this before collection, even after cancelling docker exec."""
    output = Path(output)
    run_path, process_path = output / 'run.json', output / 'process.json'
    if not run_path.exists() or not process_path.exists():
        return
    run = json.loads(run_path.read_text())
    if run.get('finalized'):
        return
    process = json.loads(process_path.read_text())
    signal_process(process, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while process.get('start') and process_identity(process['pid']) == process['start'] and time.monotonic() < deadline:
        run = json.loads(run_path.read_text())
        if run.get('finalized'):
            return
        time.sleep(0.05)
    run = json.loads(run_path.read_text())
    if run.get('finalized'):
        return
    signal_process(process, signal.SIGKILL)
    # The host has its own process group; stop it before reading its final save.
    signal_process(process.get('host') or {}, signal.SIGKILL, group=True)
    run = json.loads(run_path.read_text())
    if run.get('finalized'):
        return
    run.update(outcome='interrupted', finalized=True, recovered=True,
               error='Episode terminated before finalization', ledger=None,
               elapsedMs=round((time.time() - process['started']) * 1000))
    run.update(retain_outputs(process['state'], process['project'], output))
    write_json(run_path, run)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--interrupt', required=True, type=Path)
    parser.add_argument('--timeout', type=float, default=35)
    args = parser.parse_args()
    interrupt_episode(args.interrupt, args.timeout)
