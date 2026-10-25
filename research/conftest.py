# `pytest research` should run only real tests. The benchmark seeds under common/fixtures and the
# sandbox copies archived under */data are unsolved exercises whose tests fail by design.
# 16_epic_harness/harness_eval holds benchmark tasks (datasets/), Harbor trial dirs (jobs/), a vendored harness and
# an upstream copy of topic 16; its own tests run under its own interpreters (see the docstrings in its tests/).
collect_ignore_glob = ["*/data/*", "common/fixtures/*", "16_epic_harness/harness_eval/*"]
