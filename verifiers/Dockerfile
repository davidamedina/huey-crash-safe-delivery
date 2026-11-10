# Grading image for the "crash-safe delivery for huey" task.
# Everything is installed at build time; grading runs with --network none.
FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df

RUN apt-get update \
 && apt-get install -y --no-install-recommends redis-server faketime git \
 && rm -rf /var/lib/apt/lists/*

# The repository the model works in, pinned to huey 3.5.4.
RUN git clone https://github.com/coleifer/huey /app \
 && git -C /app checkout 5aba8d35a2504a35be46d39734d02eebb1471c62

# Root-owned verifier venv (pytest + redis client); the model's huey is put on
# it at grade time from a clean copy of /app.
COPY requirements-verifier.txt /tmp/
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements-verifier.txt

# Stock huey 3.5.4 for the upgrade and throughput baselines (V13, V14).
RUN python -m venv /opt/huey354  && /opt/huey354/bin/pip install --no-cache-dir redis==8.1.0 huey==3.5.4

COPY tests /verifier/tests
COPY pytest.ini test.sh /verifier/
RUN chmod -R a-w /verifier && chmod +x /verifier/test.sh

WORKDIR /app
