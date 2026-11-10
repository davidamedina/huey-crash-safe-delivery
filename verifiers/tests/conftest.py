import os

import pytest
import redis

from harness import Env

REDIS_URL = os.environ.get('VERIFIER_REDIS_URL', 'redis://127.0.0.1:6379/0')
ALL = ['sqlite', 'file', 'redis', 'priority_redis']


def pytest_generate_tests(metafunc):
    if 'storage' in metafunc.fixturenames:
        marker = metafunc.definition.get_closest_marker('storages')
        metafunc.parametrize('storage', marker.args if marker else ALL)


def pytest_configure(config):
    config.addinivalue_line('markers', 'storages(*names): limit storages')


@pytest.fixture
def make_env(storage, tmp_path):
    redis.Redis.from_url(REDIS_URL).flushdb()
    envs = []

    def factory(**kw):
        e = Env(storage, str(tmp_path), REDIS_URL, **kw)
        envs.append(e)
        return e

    yield factory
    for e in envs:
        e.stop_all()


@pytest.fixture
def env(make_env):
    return make_env()
