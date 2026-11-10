"""V2-V5: crashed work comes back; healthy long work is not stolen."""
import signal
import time

import pytest

SLACK = 5.0


def _first_start_after(env, task_id, t0):
    starts = [s for s in env.starts_by_task()[task_id] if s['ts'] > t0]
    return starts[0]['ts'] if starts else None


def test_v2_killed_task_restarts_on_live_consumer(env):
    app = env.app()
    c1 = env.start_consumer(workers=2)
    r = app.work(7, sleep=10)
    assert env.wait_for(lambda: env.starts_by_task()[r.id], 30)
    env.kill(c1)
    t_kill = time.time()
    env.start_consumer(workers=2)
    assert env.wait_for(lambda: _first_start_after(env, r.id, t_kill), env.vt + SLACK + 1)
    assert _first_start_after(env, r.id, t_kill) - t_kill <= env.vt + SLACK
    assert r.get(blocking=True, timeout=30, preserve=True)['value'] == 7


def test_v3_orphan_recovered_after_full_outage(env):
    app = env.app()
    c1 = env.start_consumer(workers=1)
    r = app.work(3, sleep=10)
    assert env.wait_for(lambda: env.starts_by_task()[r.id], 30)
    env.kill(c1)
    time.sleep(20)                       # nobody is running
    t_up = time.time()
    env.start_consumer(workers=1)
    assert env.wait_for(lambda: _first_start_after(env, r.id, t_up), env.vt + SLACK + 1)
    assert r.get(blocking=True, timeout=30, preserve=True)['value'] == 3


def test_v4_sigterm_does_not_lose_running_task(env):
    app = env.app()
    c1 = env.start_consumer(workers=1)
    r = app.work(5, sleep=6)
    assert env.wait_for(lambda: env.starts_by_task()[r.id], 30)
    env.kill(c1, signal.SIGTERM)
    env.start_consumer(workers=1)
    # Either it finished before exit or it runs again; it must not vanish.
    assert r.get(blocking=True, timeout=env.vt + SLACK + 10, preserve=True)['value'] == 5


def _long_task_runs_once(env, prefix=()):
    app = env.app()
    env.start_consumer(workers=1)
    r = app.work(9, sleep=3 * env.vt)
    assert env.wait_for(lambda: env.starts_by_task()[r.id], 30)
    env.start_consumer(workers=2, prefix=prefix)     # idle peer, ready to steal
    assert r.get(blocking=True, timeout=60, preserve=True)['value'] == 9
    time.sleep(env.vt + SLACK)
    assert len(env.starts_by_task()[r.id]) == 1


def test_v5_long_task_on_healthy_consumer_runs_once(env):
    _long_task_runs_once(env)


@pytest.mark.storages('redis', 'priority_redis')
def test_v6_clock_skew_does_not_steal_live_lease(env):
    _long_task_runs_once(env, prefix=('faketime', '-f', '+10s'))
