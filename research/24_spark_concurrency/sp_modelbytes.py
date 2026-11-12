#!/usr/bin/env python3
"""Topic 24: the per-step DRAM byte model of a checkpoint, from its safetensors headers (PLAN2.md).

    ~/.venv/bin/python research/24_spark_concurrency/sp_modelbytes.py [bf16 fp8 nvfp4]

Writes data/model_bytes_<variant>.json with the keys the analysis uses (the same as exp02's raw/model_bytes.json):

  w_step            bytes of weights a decode step reads once: every tensor of the text model as it is stored (packed
                    FP4 or FP8 values with their scales, or BF16), without the embedding table (a B-row gather), the
                    vision tower (--language-model-only) and the MTP head (never loaded)
  state_bytes_seq   DeltaNet state of one sequence (FP32 recurrent state + BF16 conv state, 48 layers)
  kv_bytes_token    attention KV of one token (16 layers, 4 KV heads x 256, BF16: the KV cache is pinned to BF16)

exp02's model_bytes.py does this for the BF16 folder only (its path and file pattern are fixed); this version takes any
snapshot and also reports bytes by stored dtype. Check: for bf16 the result must equal exp02's number exactly.
Only headers are read: no weights are loaded.
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp_common as C  # noqa: E402

CATS = ("embed", "lm_head", "mlp", "gdn_proj", "attn_proj", "norms", "vision", "mtp")


def headers(snap: Path):
    """(file name, tensor name, dtype, shape, bytes) for every tensor of every safetensors file."""
    for f in sorted(snap.glob("*.safetensors")):
        with open(f, "rb") as fh:
            n = struct.unpack("<Q", fh.read(8))[0]
            h = json.loads(fh.read(n))
        for k, v in h.items():
            if k != "__metadata__":
                yield f.name, k, v["dtype"], v["shape"], v["data_offsets"][1] - v["data_offsets"][0]


def category(fname: str, k: str) -> str:
    if "visual." in k:
        return "vision"
    if k.startswith("mtp.") or ".mtp." in k or "mtp" in fname:
        return "mtp"
    if "lm_head" in k:
        return "lm_head"
    if "embed_tokens" in k:
        return "embed"
    if ".mlp." in k:
        return "mlp"
    if ".linear_attn." in k:
        return "gdn_proj"
    if ".self_attn." in k:
        return "attn_proj"
    return "norms"


def budget(variant: str) -> dict:
    snap = C.snapshot_dir(C.VARIANTS[variant]["repo"])
    if snap is None:
        raise FileNotFoundError(f"no snapshot of {C.VARIANTS[variant]['repo']} under {C.HF_HOME}")
    cfg = json.loads((snap / "config.json").read_text())
    tc = cfg.get("text_config", cfg)
    cat = {c: 0 for c in CATS}
    by_dtype: dict[str, dict[str, int]] = {}
    linear_elems, total = 0, 0
    for fname, k, dtype, shape, b in headers(snap):
        c = category(fname, k)
        cat[c] += b
        total += b
        by_dtype.setdefault(c, {}).setdefault(dtype, 0)
        by_dtype[c][dtype] += b
        # the weight matrices of the matmuls a token passes through (for FLOPs per token); a packed FP4 matrix
        # holds two values per byte
        if c in ("mlp", "gdn_proj", "attn_proj", "lm_head") and len(shape) == 2 and \
                k.rsplit(".", 1)[-1] in ("weight", "weight_packed"):
            n = shape[0] * shape[1]
            linear_elems += n * 2 if k.endswith("weight_packed") else n
    n_lin = sum(1 for x in tc["layer_types"] if x == "linear_attention")
    n_full = len(tc["layer_types"]) - n_lin
    hv, dk, dv = tc["linear_num_value_heads"], tc["linear_key_head_dim"], tc["linear_value_head_dim"]
    conv_dim = 2 * tc["linear_num_key_heads"] * dk + hv * dv
    ssm = hv * dk * dv * 4                                   # FP32 (mamba_ssm_dtype)
    conv = conv_dim * (tc["linear_conv_kernel_dim"] - 1) * 2
    kv_tok = n_full * 2 * tc["num_key_value_heads"] * tc["head_dim"] * 2      # BF16 KV (pinned)
    w_step = cat["mlp"] + cat["gdn_proj"] + cat["attn_proj"] + cat["norms"] + cat["lm_head"]
    return {
        "variant": variant, "repo": C.VARIANTS[variant]["repo"], "snapshot": str(snap), "checkpoint_bytes": total,
        "categories": cat, "by_dtype": by_dtype, "resident_bytes": w_step + cat["embed"],
        "w_step": w_step, "w_step_nolm": w_step - cat["lm_head"],
        "n_linear": n_lin, "n_full": n_full, "ssm_bytes_layer": ssm, "conv_bytes_layer": conv,
        "state_bytes_seq": n_lin * (ssm + conv), "kv_bytes_token": kv_tok,
        "embed_row": tc["hidden_size"] * 2, "logits_row": tc["vocab_size"] * 4,
        "hidden": tc["hidden_size"], "ffn": tc["intermediate_size"], "vocab": tc["vocab_size"],
        # two FLOPs (multiply, add) per weight of every matmul, per token computed (prefill or decode); attention's
        # own QK and AV products over the context are not in it
        "linear_weights": linear_elems, "flops_per_token_linear": 2 * linear_elems,
        "quantization_config": {k: v for k, v in (cfg.get("quantization_config") or {}).items()
                                if k in ("quant_method", "format", "fmt", "activation_scheme", "weight_block_size",
                                         "quantization_status", "version")},
    }


def path(variant: str) -> Path:
    return C.DATA / f"model_bytes_{variant}.json"


def load(variant: str) -> dict:
    return json.loads(path(variant).read_text())


def main() -> int:
    variants = sys.argv[1:] or C.VARIANT_ORDER
    rc = 0
    for v in variants:
        m = budget(v)
        path(v).write_text(json.dumps(m, indent=1))
        gb = 1e9
        print(f"{v}: checkpoint {m['checkpoint_bytes'] / gb:.2f} GB, resident (text model) "
              f"{m['resident_bytes'] / gb:.2f} GB, read per decode step {m['w_step'] / gb:.3f} GB")
        print("  categories (GB):", {k: round(x / gb, 3) for k, x in m["categories"].items()})
        print("  by dtype (GB):", {c: {d: round(x / gb, 3) for d, x in dd.items()} for c, dd in m["by_dtype"].items()
                                   if c in ("mlp", "gdn_proj", "attn_proj", "lm_head")})
        print(f"  matmul weights {m['linear_weights'] / 1e9:.3f} G -> {m['flops_per_token_linear'] / 1e9:.1f} GFLOPs per token")
        if v == "bf16":
            ref = json.loads((C.EXP02 / "raw" / "model_bytes.json").read_text())
            same = all(m[k] == ref[k] for k in ("w_step", "state_bytes_seq", "kv_bytes_token", "embed_row",
                                                 "logits_row", "resident_bytes"))
            print(f"  equal to exp02's model_bytes.json: {same} (w_step {m['w_step']} vs {ref['w_step']})")
            rc |= 0 if same else 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
