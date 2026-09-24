Reference solutions for the coding-bench problems in `research/common/fixtures/latency_bench/coding/` (kept out of the
bench directory so that agents cannot find them with grep during a run). Each passes its test file:

    for p in intervals ttl_cache expr; do
      cp reference_solutions/$p/solution.py ../../../common/fixtures/latency_bench/coding/$p/ && \
      python3 ../../../common/fixtures/latency_bench/coding/$p/test_$p.py; rm ../../../common/fixtures/latency_bench/coding/$p/solution.py
    done
