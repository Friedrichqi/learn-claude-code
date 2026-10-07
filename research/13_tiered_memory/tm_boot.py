"""Interpreter shim behind bin/tmpython: installs research/13_tiered_memory's client layer (tm_client) and then runs
the given script as __main__, exactly as `python script args` would. mu_sweep's `--python` points at bin/tmpython,
so 11's mu_session loads after the layer and wraps it."""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tm_client  # noqa: E402

tm_client.install()
script = os.path.abspath(sys.argv[1])
sys.argv = sys.argv[1:]
sys.path[0] = os.path.dirname(script)
runpy.run_path(script, run_name="__main__")
