"""V1: the thing still works as a task queue."""
from huey import group
from huey.exceptions import TaskException


def test_tasks_pipelines_chords_and_errors_complete(env):
    app = env.app()
    env.start_consumer(workers=4)

    singles = [app.work(i) for i in range(200)]
    pipe = app.huey.enqueue(app.work.s(1).then(app.step).then(app.step))
    ch = app.huey.enqueue(group([app.work.s(i) for i in range(5)]).then(app.collect))

    assert [r.get(blocking=True, timeout=60)['value'] for r in singles] == list(range(200))
    assert [r['value'] for r in pipe.get(blocking=True, timeout=60)] == [1, 2, 3]
    assert ch.get(blocking=True, timeout=60)['value'] == sum(range(5))


def test_ordinary_failure_still_surfaces(env):
    app = env.app()
    env.start_consumer(workers=2)
    # A plain exception (not a crash) behaves exactly as in 3.5.4.
    r = app.huey.enqueue(app.step.s({'nonce': 'x'}))   # KeyError: 'value'
    try:
        r.get(blocking=True, timeout=30)
    except TaskException:
        pass
    else:
        raise AssertionError('expected TaskException')
