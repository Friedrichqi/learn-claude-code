#!/usr/bin/env python3
"""Download model weights into /data/fritz/hf (our own HF cache), one repo after another, pinned revisions."""
import sys, time
from huggingface_hub import snapshot_download
REPOS = [("Qwen/Qwen3.8-27B-FP8", "017b9c7af6"), ("deepseek-ai/DeepSeek-V4-Flash-0731", "7872f01b1d"),
         ("MiniMaxAI/MiniMax-M2.7", "d494266a4a"), ("zai-org/GLM-4.7-Flash", "7dd20894a6")]
for rid, rev in REPOS if len(sys.argv) < 2 else [r for r in REPOS if r[0] in sys.argv[1:]]:
    t = time.time()
    path = snapshot_download(rid, revision=None, cache_dir="/data/fritz/hf/hub", max_workers=16)
    print(f"{time.strftime('%H:%M:%S')} {rid} -> {path} ({time.time() - t:.0f}s)", flush=True)
