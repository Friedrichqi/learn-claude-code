# `pytest research` should run only real tests. The benchmark seeds under common/fixtures and the
# sandbox copies archived under */data are unsolved exercises whose tests fail by design.
collect_ignore_glob = ["*/data/*", "common/fixtures/*"]
