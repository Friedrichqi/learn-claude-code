"""B2 of research/13_tiered_memory: a peer GPU's HBM as the KV offload tier, reached over NVLink.

An out-of-tree vLLM OffloadingSpec (vLLM 0.29's OffloadingSpecFactory loads it by name and module path):

    CUDA_VISIBLE_DEVICES=<own>,<peer> PYTHONPATH=research/13_tiered_memory vllm serve ... \
      --kv-transfer-config '{"kv_connector": "OffloadingConnector", "kv_role": "kv_both",
                             "kv_connector_extra_config": {"spec_name": "PeerHBMOffloadingSpec",
                               "spec_module_path": "tm_peer_spec", "cpu_bytes_to_use": 25769803776,
                               "peer_device": 1}}'

It is vLLM's CPU spec with one change: the tier's buffer is allocated on cuda:<peer_device> instead of pinned host
memory, after cudaDeviceEnablePeerAccess both ways, so vLLM's own block-copy path (swap_blocks_batch /
the Triton copy kernel on UVA pointers) moves blocks GPU <-> peer GPU over NVLink. `cpu_bytes_to_use` is the size
of the peer region; the lender's own KV pool must leave room for it (`--kv-cache-memory-bytes` on both servers).
vLLM's handler asserts its second tensor is CPU memory, so the handlers are built with placeholder CPU tensors of
the same shape (malloc'd, never touched) and then pointed at the peer tensors; copy pointers are computed per
transfer from those tensor lists (gpu_worker.py `_fill_direct_ops`). Validated on an H200 pair in stage S4
(tm_calib.py peer, and NVLink byte counters during restores).
"""
from __future__ import annotations

import time

import torch

from vllm.logger import init_logger
from vllm.v1.kv_offload.cpu import gpu_worker as gw
from vllm.v1.kv_offload.cpu.spec import CPUOffloadingSpec

logger = init_logger(__name__)


def enable_peer(a: int, b: int) -> str:
    import ctypes
    lib = None
    for name in ("libcudart.so", "libcudart.so.13", "libcudart.so.12"):
        try:
            lib = ctypes.CDLL(name)
            break
        except OSError:
            continue
    if lib is None:
        return "no libcudart"
    out, cur = [], torch.cuda.current_device()
    for src, dst in ((a, b), (b, a)):
        torch.cuda.set_device(src)
        out.append(f"{src}->{dst}:{lib.cudaDeviceEnablePeerAccess(ctypes.c_int(dst), ctypes.c_uint(0))}")
    torch.cuda.set_device(cur)
    return " ".join(out)


class PeerHBMWorker(gw.CPUOffloadingWorker):
    def __init__(self, kv_caches, blocks_per_chunk: int, num_cpu_blocks: int, peer: int):
        self._mmap_region = None
        local = torch.cuda.current_device()
        logger.info("PeerHBM tier: %d blocks on cuda:%d for cuda:%d (peer access %s)", num_cpu_blocks, peer, local,
                    enable_peer(local, peer))
        gpu_tensors, peer_tensors, placeholders = [], [], []
        t0 = time.monotonic()
        for kv in kv_caches.tensors:
            page = kv.page_size_bytes
            gpu_tensors.append(kv.tensor.view(torch.int8).view((-1, page)))
            peer_tensors.append(torch.zeros((num_cpu_blocks, page * blocks_per_chunk), dtype=torch.int8,
                                            device=f"cuda:{peer}"))
            placeholders.append(torch.empty((num_cpu_blocks, page * blocks_per_chunk), dtype=torch.int8, device="cpu"))
        logger.info("PeerHBM tier allocated %.2f GiB in %.2f s",
                    sum(t.numel() for t in peer_tensors) / (1 << 30), time.monotonic() - t0)
        self._store_handler = gw.SingleDirectionOffloadingHandler(
            gpu_tensors=gpu_tensors, cpu_tensors=placeholders, blocks_per_chunk=blocks_per_chunk,
            layer_refs_per_group=kv_caches.group_data_refs, gpu_to_cpu=True)
        self._load_handler = gw.SingleDirectionOffloadingHandler(
            gpu_tensors=gpu_tensors, cpu_tensors=placeholders, blocks_per_chunk=blocks_per_chunk,
            layer_refs_per_group=kv_caches.group_data_refs, gpu_to_cpu=False)
        self._store_handler.dst_tensors = peer_tensors     # GPU -> peer
        self._load_handler.src_tensors = peer_tensors      # peer -> GPU
        del placeholders
        self._peer_tensors = peer_tensors


class PeerHBMOffloadingSpec(CPUOffloadingSpec):
    def __init__(self, config):
        super().__init__(config)
        self.peer = int(self.extra_config.get("peer_device", 1))

    def _uses_shared_region(self) -> bool:
        return False

    def create_worker(self, kv_caches):
        return PeerHBMWorker(kv_caches, self.blocks_per_chunk, self.num_blocks, self.peer)
