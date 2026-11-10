# Grading image. All deps are installed here, grading runs offline.
FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df

RUN apt-get update \
 && apt-get install -y --no-install-recommends redis-server faketime git \
 && rm -rf /var/lib/apt/lists/*

# huey 3.5.4 source
RUN git clone https://github.com/coleifer/huey /app \
 && git -C /app checkout 5aba8d35a2504a35be46d39734d02eebb1471c62

# venv for the checks
COPY requirements-verifier.txt /tmp/
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements-verifier.txt

# old huey 3.5.4, used by V13 and V14
RUN python -m venv /opt/huey354  && /opt/huey354/bin/pip install --no-cache-dir redis==8.1.0 huey==3.5.4

COPY tests /verifier/tests
COPY pytest.ini test.sh /verifier/
RUN chmod -R a-w /verifier && chmod +x /verifier/test.sh

WORKDIR /app
