"""V11-V14: scheduled tasks, periodic tasks, upgrade, speed."""
import os
import random
import statistics
import sys
import time
from collections import Counter

import pytest

from harness import LEGACY_PY

SEED = int(os.environ.get('VERIFIER_SEED', '1337'))


def test_v11_delayed_tasks_survive_scheduler_kills(env):
    app = env.app()
    rnd = random.Random(SEED)
    rs = [app.work.schedule((i,), delay=rnd.uniform(1, 6)) for i in range(300)]
    env.chaos(lambda: [env.start_consumer(workers=4)], duration=15,
              every=(1.0, 2.0), seed=SEED)
    starts = env.starts_by_task()
    for r in rs:
        val = r.get(blocking=True, timeout=120, preserve=True)
        assert starts[r.id][-1]['nonce'] == val['nonce']
    starts = env.starts_by_task()
    assert sum(len(starts[r.id]) - 1 for r in rs) <= env.kills * 4


def test_v12_periodic_runs_once_per_minute_despite_kills(env):
    env.app()
    proc = env.start_consumer(workers=2, periodic=True)
    for _ in range(3):
        time.sleep(60 - time.time() % 60 + 0.3)  # just after the minute
        env.kill(proc)
        proc = env.start_consumer(workers=2, periodic=True)
    time.sleep(env.vt + 10)
    minutes = Counter(e[3] for e in env.events('start') if e[0] == 'periodic')
    full = sorted(minutes)[1:-1] if len(minutes) > 2 else sorted(minutes)
    assert len(full) >= 2
    assert all(minutes[m] == 1 for m in full), minutes


def test_v13_state_written_by_354_is_drained(make_env):
    env = make_env(VAPP_LEGACY='1')
    import subprocess
    # add tasks with old huey 3.5.4
    script = ('import vapp\n'
              'q = [vapp.work(i).id for i in range(50)]\n'
              's = [vapp.work.schedule((i,), delay=3).id for i in range(20)]\n'
              'print(",".join(q + s))\n')
    ids = subprocess.check_output([LEGACY_PY, '-c', script],
                                  env=dict(os.environ, **env.vars)).decode().strip().split(',')
    # then run the new consumer on the same data
    env.vars.pop('VAPP_LEGACY')
    env.start_consumer(workers=4)
    assert env.wait_for(lambda: all(env.starts_by_task()[i] for i in ids), 60)
    time.sleep(env.vt + 5)
    starts = env.starts_by_task()
    assert all(len(starts[i]) == 1 for i in ids)


def _throughput(env, python, n=5000):
    import subprocess
    import redis
    r = redis.Redis.from_url(env.vars['VAPP_REDIS_URL'])
    r.flushdb()
    subprocess.check_call([python or sys.executable, '-c',
                           'import vapp; [vapp.noop() for _ in range(%d)]' % n],
                          env=dict(os.environ, **env.vars))
    t0 = time.time()
    p = env.start_consumer(workers=4, kind='thread', python=python)
    while int(r.get('verifier:done') or 0) < n:
        time.sleep(0.02)
    dt = time.time() - t0
    env.kill(p)
    return n / dt


@pytest.mark.storages('redis')
def test_v14_redis_throughput_at_least_half_of_354(make_env):
    old, new = [], []
    for _ in range(3):
        old.append(_throughput(make_env(VAPP_LEGACY='1'), LEGACY_PY))
        new.append(_throughput(make_env(), None))
    assert statistics.median(new) >= 0.5 * statistics.median(old)
