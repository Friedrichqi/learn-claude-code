# Heavy-test fixture (research/06_tool_cost Part C, HEAVY-TEST)

`code.py` is `s10_task_system/code.py` with two injected bugs; `test_task_system.py` is
`tests/test_task_system.py` pointed at the local `code.py`. Two of its tests fail until both bugs
are fixed. The workload copies this directory into a sandbox (`profile_run.py --write-root
profiling_sandbox/heavy --sandbox-from research/common/fixtures/tool_heavy/task_system`).
`research/conftest.py` does not collect it (`common/fixtures/*`).
