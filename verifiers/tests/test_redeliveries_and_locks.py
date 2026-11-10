"""V8-V10: crash re-runs vs retries, poison tasks, and lock ownership."""
import time

import pytest
from huey.exceptions import TaskException, TaskLockedException


def test_v8_crash_rerun_does_not_consume_retries(env):
    app = env.app()
    env.start_consumer(workers=2)           # health check restarts the dead worker
    r = app.die_first_time()                # retries=0
    assert r.get(blocking=True, timeout=env.vt + 30, preserve=True) == 'survived'
    assert len(env.starts_by_task()[r.id]) == 2


def test_v9_poison_task_stops_after_max_redeliveries(env):
    app = env.app()
    env.start_consumer(workers=2)
    r = app.huey.enqueue(app.always_die.s().error(app.record_error))
    with pytest.raises(TaskException):
        r.get(blocking=True, timeout=4 * (env.vt + 10), preserve=True)
    time.sleep(env.vt + 5)
    assert len(env.starts_by_task()[r.id]) == 1 + 3
    assert env.wait_for(lambda: len(env.events('error_handler')) == 1, 20)
    # The consumer is still doing useful work afterwards.
    assert app.work(1).get(blocking=True, timeout=30)['value'] == 1


def test_v10a_rerun_can_take_lock_of_its_dead_self(env):
    app = env.app()
    env.start_consumer(workers=2)
    r = app.hold_lock(0.5, die_inside=True)
    assert r.get(blocking=True, timeout=env.vt + 30, preserve=True) == 'locked-ok'


def test_v10b_lock_still_excludes_while_owner_alive(env):
    app = env.app()
    env.start_consumer(workers=2)
    owner = app.hold_lock(4 * env.vt)       # longer than a lease: must stay owned
    assert env.wait_for(lambda: env.starts_by_task()[owner.id], 30)
    time.sleep(2 * env.vt)
    other = app.hold_lock(0)
    with pytest.raises(TaskException) as exc:
        other.get(blocking=True, timeout=30, preserve=True)
    assert 'TaskLockedException' in str(exc.value.metadata.get('error', '')) or \
        TaskLockedException.__name__ in repr(exc.value.metadata)
    assert owner.get(blocking=True, timeout=60, preserve=True) == 'locked-ok'
