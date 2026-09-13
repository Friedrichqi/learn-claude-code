Reference solutions for the coding-bench problems in `scripts/latency_bench/coding/` (kept out of the
bench directory so that agents cannot find them with grep during a run). Each passes its test file:

    for p in intervals ttl_cache expr; do
      cp reference_solutions/$p/solution.py ../../scripts/latency_bench/coding/$p/ && \
      python3 ../../scripts/latency_bench/coding/$p/test_$p.py; rm ../../scripts/latency_bench/coding/$p/solution.py
    done
