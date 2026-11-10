## Crash-safe task delivery for huey

Long-term task for the backend: make sure that the [huey](https://github.com/coleifer/huey) task queue does not drop tasks in case of consumer crash; right now it does. This repository contains the task prompt, motivation for the task, and a set of verifiers written in Python/pytest.

| Field | Where |
|---|---|
| Repository | [below](#1-repository) |
| Task prompt | [below](#2-task-prompt) |
| Why frontier models could struggle | [below](#3-why-frontier-models-could-struggle) |
| Edge cases and verifiers | [below](#4-edge-cases-and-verifiers) and [`verifiers/`](verifiers/) |
| Video | uploaded separately to the assessment |

---

## 1. Repository

- **Repo:** https://github.com/coleifer/huey (MIT licence)
- **Pinned to:** tag `3.5.4`, commit `5aba8d35a2504a35be46d39734d02eebb1471c62`
- **Status upstream:** not implemented. Users asked for it in #418 ("When are tasks acknowledged?") and #239 ("Re-trying tasks lost in execution"), and #880 shows a related loss on SIGINT. All were closed without a fix. It touches every storage backend and the consumer, so nobody has done it.
- **Main files touched:**
  - `huey/storage.py`: `RedisStorage`, `RedisPriorityQueue`, `SqliteStorage`/`BaseSqlStorage`, `FileStorage`. Their `dequeue`, `read_schedule` and `put_if_empty` need new semantics, plus schema/layout upgrades.
  - `huey/api.py`: `Huey.__init__` (new options), `_execute` (result, `.then()`, chord and error bookkeeping), `TaskLock`, `_check_chord`.
  - `huey/consumer.py`: `Worker.loop`, `Scheduler.loop`, `Consumer.check_worker_health` and shutdown (lease renewal, recovery of orphaned tasks).
  - `huey/consumer_options.py`, `huey/bin/huey_consumer.py`: option plumbing.
  - `docs/`, `huey/tests/`.

## 2. Task prompt

> Huey loses work. A worker takes a task off the queue before it runs it, so if the consumer is killed with SIGKILL, OOM-killed or its host dies, every task it was running is gone. The same is true of a SIGTERM shutdown, and of scheduled tasks the scheduler was moving into the queue at that moment. Please make delivery crash-safe for `RedisHuey`, `PriorityRedisHuey`, `SqliteHuey` and `FileHuey`. Other storages and immediate mode can keep their current behaviour.
>
> What I need:
>
> - These classes accept a new `visibility_timeout` option (seconds, default 30). A task whose consumer died must start again on a live consumer within `visibility_timeout` + 5 seconds of the death. If every consumer was down, the timer starts when the first one comes back.
> - A task that runs longer than `visibility_timeout` on a healthy consumer must never be started a second time. For Redis, consumers on different machines may have clocks up to 10 seconds apart.
> - Once a task's result or final error is stored, the task never runs again, and its `.then()` callback and any chord callback start exactly once with that stored value.
> - A re-run after a crash does not use up the task's `retries`. Once a task has been interrupted by a crash `max_redeliveries` times (new option, default 3), it is not run again: its result raises `TaskException` and its `.error()` handler runs, as for any other failure.
> - A lock taken with `huey.lock_task()` by a task whose consumer died must not stop that task's re-run from taking the same lock. While the owner is alive, the lock still blocks other tasks.
> - Scheduled, delayed and periodic tasks are never lost or run twice if the consumer dies while moving them into the queue.
> - A Redis database, SQLite file or `FileHuey` directory written by huey 3.5.4, with tasks still queued or scheduled, must work with the new code without a manual migration, and none of those tasks may be lost.
> - Throughput for short tasks on Redis must stay at least half of what 3.5.4 does.
>
> Keep the existing public API and the existing test suite working, and add no new runtime dependencies.

(361 words)

## 3. Why frontier models could struggle

**What I know that most engineers don't.** I have run task queues in production and lost work in each of the ways above. "At-least-once" sounds like one change ("ack after running instead of before"), but in a real queue it splits into a dozen pieces of bookkeeping. They have to agree across processes, across a crash, and across four storage engines with different atomicity tools: Redis Lua/`LMOVE`, SQLite transactions, and a directory of files where `rename` is the only atomic step.

**The hard part, and what the model has to discover on its own:**

1. **Leases, not acks.** Moving the task to a "processing" list (the textbook `RPOPLPUSH` pattern) passes a demo but breaks two sentences. A long task gets stolen unless the lease is renewed while it runs (sentence 2). Renewal has to come from something other than the worker, because the worker is blocked in user code, and with process workers it is a separate OS process. With skewed clocks, any expiry check that uses the consumer's own `time.time()` steals live leases. Expiry has to be judged on the storage's clock (Redis `TIME`), or as relative deadlines that the storage itself stores.
2. **The window after the task returns.** huey stores the result, *then* enqueues the `.then()` child, *then* (in the model's new code) releases the lease. A crash between any two of these steps either re-runs a task that already stored a result, or drops or duplicates the child. Fixing it needs one atomic commit (result + child + ack) or idempotent child IDs plus a "has a result" check before running. Most attempts get this wrong without noticing, because it only shows up under random kills.
3. **Chords use `storage.incr`.** `_check_chord` counts finished members with a non-idempotent counter. A member that re-runs after a crash, after the `incr` but before its ack, counts twice. The callback then fires early with a missing result, or never fires. Nothing in the prompt says "chord counter". The model has to find it from the sentence about chord callbacks starting exactly once.
4. **Locks have no owner.** `TaskLock` is a `put_if_empty` key. When the re-run takes the same lock, it finds the dead worker's key and raises `TaskLockedException`. Clearing locks at startup (`flush_locks`) is wrong too, because another live consumer may own them. The lock needs an owner tied to the lease.
5. **Two kinds of failure counter.** User `retries` and crash redeliveries must be counted separately, and the redelivery count must survive the crash it counts. So it has to be written *before* the task runs, not after.
6. **Upgrade in place.** SQLite needs new columns or a table without breaking a 3.5.4 file. `FileHuey` needs a layout change that still reads old queue files. Redis must still drain the old list key.
7. **Process workers and the consumer's health check.** `check_worker_health` already restarts dead workers, but the task the dead worker held is gone. The model has to connect this with the lease code.

**Where models usually go wrong:** they add a "processing" list and an ack, write unit tests with mocked storage, and stop. Those attempts fail the long-task, clock-skew, chord, lock, poison-task and upgrade checks. Others make every startup re-queue everything in "processing", which duplicates tasks that live consumers on other hosts are still running.

## 4. Edge cases and verifiers

Full code: [`verifiers/`](verifiers/). Each check runs real `huey_consumer` processes in their own process group and kills the whole group, which simulates a host dying. Every check except Redis-only V6 and V13 runs once per storage (`sqlite`, `file`, `redis`, `priority_redis`), with `visibility_timeout=2` to keep runs short. Tasks write `start`/`end` events with a random per-run *nonce* to a separate SQLite log that huey doesn't know about. So checks see only behaviour, never huey's internal keys or tables.

| # | Check | Prompt sentence it traces to | How it avoids failing a correct solution / rewarding gaming |
|---|---|---|---|
| V1 | 200 normal tasks, pipelines, chords and `.error()` handlers all finish with the right values; huey's own test suite (`runtests.py`) still passes | "Keep the existing public API and the existing test suite working" | Basics. Shows whether the model built anything at all. |
| V2 | Kill the consumer while a 10 s task runs, start a new consumer at once → the task starts again within `vt + 5` s | sentence 1 | 5 s of slack. Any design (Lua, streams, lease rows, rename-claim) passes. Doing nothing fails. |
| V3 | Kill every consumer, wait 20 s, start one → orphaned task starts within `vt + 5` s of that start | sentence 1 ("timer starts when the first one comes back") | Catches designs that only recover from a peer's in-memory state. |
| V4 | SIGTERM with a running task (old huey drops it) → the task runs again | opening paragraph (SIGTERM) | Also accepts a graceful finish before exit. Only *loss* fails. |
| V5 | A 3 × `vt` task on a healthy consumer, with an idle second consumer ready → exactly one `start` event | sentence 2 | Fails "processing list without renewal". Runs a different event per task id, so no timing coincidence. |
| V6 | Same as V5, but the idle consumer runs under `faketime +10s` (Redis only) | sentence 2 (clock skew) | Fails expiry checks against the local clock. libfaketime is baked into the image (no network at grading). |
| V7 | **Random-kill soak.** 150 pipelines `a.then(b).then(c)` + 20 chords of 5, two consumers × 4 process workers, one consumer process group SIGKILLed every 1–3 s (seeded) and restarted. Invariants: (a) everything finishes; (b) for every task id the **stored result's nonce equals the nonce of the last execution that started**, so nothing ran after its result was stored; (c) every child's input nonce equals its parent's stored nonce; (d) every chord callback starts exactly once and gets all 5 stored member nonces; (e) total extra starts ≤ kills × workers | sentence 3 | Allows the re-runs that at-least-once permits (a crash before the result was stored). Rejects "re-run everything on startup" and the chord double-`incr`. Random timing with a fixed seed means it can't be passed by luck or tuned to one schedule. |
| V8 | A `retries=0` task that kills its own worker on the first attempt only → it succeeds on the re-run | sentence 4 (doesn't use up `retries`) | Paired with V9. Treating a crash as a normal failure fails here. |
| V9 | A task that kills its worker on every attempt → exactly `1 + 3` starts, `result.get()` raises `TaskException`, `.error()` handler runs once, and other tasks keep flowing | sentence 4 (`max_redeliveries`) | Paired with V8: "never redeliver" fails V8, "always redeliver" fails V9. |
| V10 | A task holds `lock_task('L')` and is killed. Its re-run takes `L` and finishes. A different task trying `L` while a healthy owner holds it gets `TaskLockedException` | sentence 5 | Fails both "locks never expire" and "flush all locks at startup" (the second half fails). |
| V11 | 300 tasks with `delay` 1–6 s, consumer group killed every 1–2 s while the scheduler moves them → each runs, and the stored-nonce rule from V7 holds | sentence 6 | Same invariant as V7, so allowed crash re-runs pass. |
| V12 | Periodic task every minute: kill the consumer 0.3 s after each minute boundary for 3 minutes → one run per minute, none missing | sentence 6 (periodic) | Checks per-minute counts, not timestamps, so slow machines pass. |
| V13 | Fill storage with 50 queued + 20 scheduled tasks using a stock **huey 3.5.4** venv, then start the new consumer on the same Redis db / SQLite file / directory → all 70 run, each once | sentence 7 | 3.5.4 is baked into the image as a pinned wheel. No manual migration step runs. |
| V14 | 5,000 no-op tasks on Redis, 4 thread workers: new code ≥ 0.5 × throughput of 3.5.4 measured in the same container | sentence 8 | Wide margin (½), median of 3 runs. It only catches designs that do something like an `fsync` or full scan per task. |

**Resisting an attempt that tries to pass without doing the work** (lessons from Parts 1–2):

- Hidden tests live in `/verifier`, read-only. Only an allowlist (`huey/**`, `docs/**`) is copied from the model's tree into a clean checkout. Any `conftest.py`, `pytest.ini`, `pyproject` change, `sitecustomize.py` or `.pth` the model added is dropped.
- Pytest runs with `python -I`, `-c /verifier/pytest.ini --rootdir /verifier --confcutdir /verifier -p no:cacheprovider` and a JUnit XML report. `test.sh` fails unless every expected test ID is present and passed, so skipping or deselecting can't help.
- Facts come from the verifier's own event log and from `Result.get()`, never from storage keys. The model can't special-case the checks.
- Kill timing is random (seeded per run). Each check is paired with a "do nothing" or "do too much" counterpart (V2/V5, V8/V9, V10a/V10b), so a one-sided shortcut always fails something.

**Environment** ([`verifiers/Dockerfile`](verifiers/Dockerfile), [`verifiers/test.sh`](verifiers/test.sh)): `python:3.12-slim-bookworm` pinned by digest, redis-server, libfaketime, an exact-pinned verifier venv (pytest, redis, plus setuptools and wheel so the model's huey installs with `--no-build-isolation` and doesn't hit PyPI), and a separate stock huey 3.5.4 venv for the V13 and V14 baselines. Everything is installed at build time, so grading runs with `--network none`. Before shipping, the transitive deps should be hash-locked with `pip-compile --generate-hashes`.

**Status of the verifier code:** it is written against huey 3.5.4's public API and compiles, but it has **not been run yet** (it needs Linux for `SIGKILL`/process groups and Docker for the image). Next step: build the image, check that V1 passes and V2–V13 fail on stock 3.5.4, and calibrate the timings.

## 5. Video

The video is uploaded directly to the assessment (not linked here).
