"""V7: kill consumers at random while pipelines and chords run.

Main rule: the saved result must come from the last run that started.
Otherwise the task ran again after its result was saved.
"""
import os

from huey import group

SEED = int(os.environ.get('VERIFIER_SEED', '1337'))
WORKERS = 4


def test_v7_random_kills_keep_results_and_callbacks_exact(env):
    app = env.app()
    start = lambda: [env.start_consumer(workers=WORKERS) for _ in range(2)]

    pipes = [app.huey.enqueue(app.work.s(i, sleep=0.2).then(app.step, sleep=0.2)
                              .then(app.step, sleep=0.2)) for i in range(150)]
    chords = [app.huey.enqueue(group([app.work.s(j, sleep=0.3) for j in range(5)])
                               .then(app.collect)) for _ in range(20)]

    procs = env.chaos(start, duration=45, seed=SEED)
    del procs

    # all tasks finish
    pipe_vals = [p.get(blocking=True, timeout=120, preserve=True) for p in pipes]
    chord_vals = [c.get(blocking=True, timeout=120, preserve=True) for c in chords]
    starts = env.starts_by_task()

    def check_last_started(result_obj, value):
        runs = starts[result_obj.id]
        assert runs, 'no start event for %s' % result_obj.id
        assert runs[-1]['nonce'] == value['nonce'], (
            'task %s started again after its result was stored' % result_obj.id)

    for group_res, values in zip(pipes, pipe_vals):
        results = list(group_res)
        for res, val in zip(results, values):
            check_last_started(res, val)
        for parent_val, child in zip(values, results[1:]):  # child got parent's result
            assert all(s['data'] == parent_val['nonce'] for s in starts[child.id])

    for c, val in zip(chords, chord_vals):
        cb_runs = starts[c.callback.id]
        assert len(cb_runs) == 1, 'chord callback started %d times' % len(cb_runs)
        members = sorted(m['nonce'] for m in c.results.get(preserve=True))
        assert cb_runs[0]['data'] == ','.join(members)
        for m in c.results:
            check_last_started(m, m.get(preserve=True))

    # no more re-runs than the kills can explain
    extra = sum(len(v) - 1 for v in starts.values())
    assert extra <= env.kills * WORKERS
