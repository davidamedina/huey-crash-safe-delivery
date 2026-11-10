## Crash-safe task delivery for huey

Long-term task for the backend: make sure that the [huey] task queue does not drop tasks in case of consumer crash; right now it does. This repository contains the task prompt, motivation for the task, and a set of verifiers written in Python/pytest.