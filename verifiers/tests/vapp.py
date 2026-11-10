"""Test tasks. Set up by env vars. Tasks write to their own event log."""
import os
import signal
import sqlite3
import time
import uuid

from huey import FileHuey, PriorityRedisHuey, RedisHuey, SqliteHuey, crontab

STORAGE = os.environ['VAPP_STORAGE']  # sqlite, file, redis, priority_redis
WORKDIR = os.environ['VAPP_WORKDIR']
EVENTS = os.path.join(WORKDIR, 'events.db')


def _make_huey():
    kw = {'utc': True}
    if os.environ.get('VAPP_LEGACY') != '1':
        kw['visibility_timeout'] = float(os.environ.get('VAPP_VT', '2'))
        if 'VAPP_MAX_REDELIVERIES' in os.environ:
            kw['max_redeliveries'] = int(os.environ['VAPP_MAX_REDELIVERIES'])
    if STORAGE == 'sqlite':
        return SqliteHuey('v', filename=os.path.join(WORKDIR, 'huey.db'), **kw)
    if STORAGE == 'file':
        return FileHuey('v', path=os.path.join(WORKDIR, 'hueyfiles'), **kw)
    cls = RedisHuey if STORAGE == 'redis' else PriorityRedisHuey
    return cls('v', url=os.environ['VAPP_REDIS_URL'], **kw)


huey = _make_huey()


def log(task_id, kind, nonce=None, data=None):
    for _ in range(200):
        try:
            with sqlite3.connect(EVENTS, timeout=30) as conn:
                conn.execute(
                    'INSERT INTO ev (task_id, kind, nonce, data, pid, ts) '
                    'VALUES (?, ?, ?, ?, ?, ?)',
                    (task_id, kind, nonce, data, os.getpid(), time.time()))
            return
        except sqlite3.OperationalError:
            time.sleep(0.05)
    raise RuntimeError('event log unavailable')


def _die():
    # hard kill, like a crash
    os.kill(os.getpid(), signal.SIGKILL)


def _run(task, n, sleep, parent_nonce=None):
    nonce = uuid.uuid4().hex
    log(task.id, 'start', nonce, parent_nonce)
    if sleep:
        time.sleep(sleep)
    log(task.id, 'end', nonce)
    return {'nonce': nonce, 'value': n}


@huey.task(context=True)
def work(n, sleep=0, task=None):
    return _run(task, n, sleep)


@huey.task(context=True)
def step(prev, sleep=0, task=None):
    # pipeline step, logs the parent's nonce
    return _run(task, prev['value'] + 1, sleep, parent_nonce=prev['nonce'])


@huey.task(context=True)
def collect(results, task=None):
    # chord callback, logs all member nonces
    nonce = uuid.uuid4().hex
    log(task.id, 'start', nonce, ','.join(sorted(r['nonce'] for r in results)))
    return {'nonce': nonce, 'value': sum(r['value'] for r in results)}


@huey.task(context=True, retries=0)
def die_first_time(task=None):
    marker = os.path.join(WORKDIR, 'died-%s' % task.id)
    log(task.id, 'start')
    if not os.path.exists(marker):
        open(marker, 'w').close()
        _die()
    return 'survived'


@huey.task(context=True)
def always_die(task=None):
    log(task.id, 'start')
    _die()


@huey.task(context=True)
def record_error(*args, task=None):
    log(task.id, 'error_handler', data=repr(args[-1]) if args else None)


@huey.task(context=True)
def hold_lock(hold, die_inside=False, task=None):
    with huey.lock_task('L'):
        log(task.id, 'start')
        if die_inside and not os.path.exists(os.path.join(WORKDIR, 'lockdied')):
            open(os.path.join(WORKDIR, 'lockdied'), 'w').close()
            _die()
        time.sleep(hold)
    log(task.id, 'end')
    return 'locked-ok'


@huey.task()
def noop():
    # for the speed test (redis only)
    import redis
    redis.Redis.from_url(os.environ['VAPP_REDIS_URL']).incr('verifier:done')


if os.environ.get('VAPP_PERIODIC') == '1':
    @huey.periodic_task(crontab(minute='*'))
    def every_minute():
        log('periodic', 'start', data=time.strftime('%H:%M', time.gmtime()))
