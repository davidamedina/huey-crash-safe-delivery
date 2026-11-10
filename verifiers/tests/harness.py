"""Start and kill huey consumers, read the event log."""
import importlib
import os
import random
import signal
import sqlite3
import subprocess
import sys
import time
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
SOLUTION_PY = os.environ.get('SOLUTION_PYTHON', sys.executable)
LEGACY_PY = os.environ.get('LEGACY_PYTHON', '/opt/huey354/bin/python')


class Env:
    def __init__(self, storage, workdir, redis_url, vt=2.0, **extra):
        self.storage, self.workdir, self.vt = storage, workdir, vt
        self.vars = {
            'VAPP_STORAGE': storage,
            'VAPP_WORKDIR': workdir,
            'VAPP_REDIS_URL': redis_url,
            'VAPP_VT': str(vt),
            'PYTHONPATH': HERE,
        }
        self.vars.update({k: str(v) for k, v in extra.items()})
        with sqlite3.connect(self.events_path) as conn:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('CREATE TABLE IF NOT EXISTS ev (task_id, kind, nonce, '
                         'data, pid, ts REAL)')
        self.consumers = []
        self.kills = 0

    @property
    def events_path(self):
        return os.path.join(self.workdir, 'events.db')

    def app(self):
        os.environ.update(self.vars)
        sys.path.insert(0, HERE)
        sys.modules.pop('vapp', None)
        return importlib.import_module('vapp')

    def start_consumer(self, workers=4, kind='process', python=None,
                       prefix=(), periodic=False):
        env = dict(os.environ, **self.vars)
        if periodic:
            env['VAPP_PERIODIC'] = '1'
        cmd = list(prefix) + [python or SOLUTION_PY, '-m', 'huey.bin.huey_consumer',
                              'vapp.huey', '--workers', str(workers),
                              '--worker-type', kind, '--delay', '0.05',
                              '--max-delay', '0.2',
                              '--health-check-interval', '1']
        if not periodic:
            cmd.append('--no-periodic')
        proc = subprocess.Popen(cmd, env=env, cwd=self.workdir,
                                start_new_session=True,
                                stdout=subprocess.DEVNULL,
                                stderr=open(os.path.join(self.workdir, 'consumer.log'), 'ab'))
        self.consumers.append(proc)
        return proc

    def kill(self, proc, sig=signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            pass
        if sig == signal.SIGKILL:
            self.kills += 1
        proc.wait(timeout=60)

    def stop_all(self):
        for p in self.consumers:
            if p.poll() is None:
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                p.wait(timeout=60)

    def chaos(self, procs_factory, duration, every=(1.0, 3.0), seed=0):
        """Kill a random consumer every few seconds and start a new one."""
        rnd = random.Random(seed)
        procs = procs_factory()
        end = time.time() + duration
        while time.time() < end:
            time.sleep(rnd.uniform(*every))
            victim = rnd.randrange(len(procs))
            self.kill(procs[victim])
            procs[victim] = self.start_consumer()
        return procs

    def events(self, kind=None):
        with sqlite3.connect(self.events_path, timeout=30) as conn:
            q = 'SELECT task_id, kind, nonce, data, pid, ts FROM ev'
            rows = conn.execute(q + (' WHERE kind = ?' if kind else '') +
                                ' ORDER BY ts', (kind,) if kind else ()).fetchall()
        return rows

    def starts_by_task(self):
        out = defaultdict(list)
        for task_id, _, nonce, data, pid, ts in self.events('start'):
            out[task_id].append({'nonce': nonce, 'data': data, 'pid': pid, 'ts': ts})
        return out

    def wait_for(self, predicate, timeout, interval=0.1):
        end = time.time() + timeout
        while time.time() < end:
            if predicate():
                return True
            time.sleep(interval)
        return predicate()
