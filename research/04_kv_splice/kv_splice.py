#!/usr/bin/env python3
"""KV-cache splicing for compaction edits, on a local HF causal LM.

The question this answers: when a harness rewrites the MIDDLE of a prompt (a compaction placeholder
replaces a tool result), can the KV cache of everything AFTER the edit be kept, with only the
placeholder prefilled and the stale entries' RoPE rotated to their new positions?  Real providers
recompute from the first changed token; here both are computed side by side on the same model.

Engine API (all positions are explicit, so physical cache order and RoPE position are decoupled):

    eng = SpliceEngine("Qwen/Qwen2.5-1.5B-Instruct")
    st  = eng.prefill(ids)                       # CacheState: ids, logical positions, DynamicCache
    logits = eng.extend(st, more_ids)            # append tokens (fresh compute), positions continue
    st2, stats = eng.splice(st, new_ids, mode)   # reuse matching blocks of st for new_ids
        mode="shift": stale keys rotated by (new_pos - old_pos); new tokens get compacted positions
        mode="gap":   stale keys untouched (keep old positions); new tokens continue after the max
        mode="recompute": control -> recompute from the first differing token (what providers do)
    eng.crop(st, n)                              # drop the last n tokens (undo a decode/probe)

Correctness of the rotation is checked by `python3 kv_splice.py --selftest`.
"""
from __future__ import annotations

import argparse
import difflib
import math
import os
import time
from dataclasses import dataclass, field

import torch

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


@dataclass
class CacheState:
    ids: list[int]
    pos: list[int]                      # logical (RoPE) position of every physical slot
    cache: object                       # transformers DynamicCache
    fresh_tokens: int = 0               # tokens computed by the model for this state
    reused_tokens: int = 0              # tokens copied from another state
    lineage: list[str] = field(default_factory=list)

    def __len__(self):
        return len(self.ids)


def rotate_half(x):
    x1 = x[..., : x.shape[-1] // 2]
    x2 = x[..., x.shape[-1] // 2:]
    return torch.cat((-x2, x1), dim=-1)


class SpliceEngine:
    def __init__(self, model_id: str, dtype=torch.float32, threads: int | None = None,
                 attn_impl: str = "sdpa", chunk: int = 1024, device: str = "cpu", explicit_mask: bool = False):
        from transformers import AutoModelForCausalLM, AutoTokenizer
        if threads:
            torch.set_num_threads(threads)
        self.tok = AutoTokenizer.from_pretrained(model_id)
        self.model = AutoModelForCausalLM.from_pretrained(model_id, dtype=dtype, attn_implementation=attn_impl)
        self.model.eval().to(device)
        self.device = device
        self.dtype = dtype
        self.chunk = chunk
        self.explicit_mask = explicit_mask
        self.config = self.model.config
        self.rotary = self.model.model.rotary_emb
        rp = getattr(self.config, "rope_parameters", None) or {}
        rope_type = rp.get("rope_type", getattr(self.config, "rope_scaling", None) or "default")
        if rope_type not in ("default", None):
            raise RuntimeError(f"splice assumes plain RoPE (rotation composes additively); model uses {rope_type}")

    # -- primitives --------------------------------------------------------------------------
    def new_cache(self):
        from transformers import DynamicCache
        return DynamicCache(config=self.config)

    def _mask(self, past: int, q: int) -> torch.Tensor:
        total = past + q
        allowed = torch.arange(total, device=self.device)[None, :] <= (past + torch.arange(q, device=self.device))[:, None]
        mask = torch.zeros((q, total), dtype=self.dtype, device=self.device)
        mask[~allowed] = torch.finfo(self.dtype).min
        return mask[None, None]

    @torch.no_grad()
    def _forward(self, cache, ids: list[int], pos: list[int], keep_logits: str):
        """Run `ids` at logical positions `pos` on top of `cache` (appended physically). Returns logits."""
        outs = []
        for s in range(0, len(ids), self.chunk):
            c_ids = ids[s:s + self.chunk]
            c_pos = pos[s:s + self.chunk]
            past = cache.get_seq_length()
            kwargs = dict(input_ids=torch.tensor([c_ids], device=self.device),
                          position_ids=torch.tensor([c_pos], device=self.device),
                          attention_mask=self._mask(past, len(c_ids)) if self.explicit_mask else None,
                          past_key_values=cache, use_cache=True)
            last_chunk = s + self.chunk >= len(ids)
            if keep_logits == "all" or (keep_logits == "last" and last_chunk):
                out = self.model(**kwargs, logits_to_keep=0 if keep_logits == "all" else 1)
                outs.append(out.logits[0].float())
            else:
                self.model(**kwargs, logits_to_keep=1)
        if not outs:
            return None
        return torch.cat(outs, 0)

    def prefill(self, ids: list[int], pos: list[int] | None = None, keep_logits: str = "last") -> tuple[CacheState, torch.Tensor]:
        pos = list(pos) if pos is not None else list(range(len(ids)))
        cache = self.new_cache()
        logits = self._forward(cache, ids, pos, keep_logits)
        return CacheState(list(ids), pos, cache, fresh_tokens=len(ids), lineage=["prefill"]), logits

    def extend(self, st: CacheState, ids: list[int], pos: list[int] | None = None, keep_logits: str = "all") -> torch.Tensor:
        """Append `ids` to st (fresh compute). Default positions continue after the max logical position."""
        if pos is None:
            start = (max(st.pos) + 1) if st.pos else 0
            pos = list(range(start, start + len(ids)))
        logits = self._forward(st.cache, ids, pos, keep_logits)
        st.ids.extend(ids)
        st.pos.extend(pos)
        st.fresh_tokens += len(ids)
        return logits

    def crop(self, st: CacheState, n: int):
        if n <= 0:
            return
        st.cache.crop(-n)          # negative = remove n tokens (positive is the legacy "keep n" form)
        del st.ids[-n:]
        del st.pos[-n:]

    def clone(self, st: CacheState) -> CacheState:
        cache = self.new_cache()
        for src, dst in zip(st.cache.layers, cache.layers):
            dst.update(src.keys.clone(), src.values.clone())
        return CacheState(list(st.ids), list(st.pos), cache, st.fresh_tokens, st.reused_tokens, list(st.lineage))

    # -- rotation ----------------------------------------------------------------------------
    @torch.no_grad()
    def rotate_keys(self, keys: torch.Tensor, delta: int) -> torch.Tensor:
        """Move post-RoPE keys from position m to m+delta: R((m+d)t) k = R(dt) R(mt) k."""
        if delta == 0:
            return keys
        cos, sin = self.rotary(keys, torch.tensor([[delta]], device=self.device))   # [1,1,hd]
        cos = cos.to(keys.dtype).unsqueeze(1)
        sin = sin.to(keys.dtype).unsqueeze(1)
        return keys * cos + rotate_half(keys) * sin

    # -- splice ------------------------------------------------------------------------------
    @torch.no_grad()
    def splice(self, old: CacheState, new_ids: list[int], mode: str = "shift",
               ops: list[tuple] | None = None) -> tuple[CacheState, dict]:
        """Build a state for new_ids, reusing blocks of `old` that survive the edit.

        mode: "shift" (rotate stale keys to their compacted positions), "gap" (keep stale positions,
        new tokens continue after the largest position), "recompute" (provider behaviour: reuse only
        the common prefix, recompute everything after the first differing token).
        """
        if ops is None:
            sm = difflib.SequenceMatcher(None, old.ids, new_ids, autojunk=False)
            ops = sm.get_opcodes()
        if mode == "recompute":
            first = ops[0]
            prefix = first[2] if first[0] == "equal" else 0
            ops = ([("equal", 0, prefix, 0, prefix)] if prefix else []) + \
                  [("replace", prefix, len(old.ids), prefix, len(new_ids))] if prefix < len(new_ids) or prefix < len(old.ids) else ops
        cache = self.new_cache()
        st = CacheState([], [], cache, lineage=old.lineage + [f"splice:{mode}"])
        blocks = []
        for tag, i1, i2, j1, j2 in ops:
            if tag == "equal":
                if mode == "shift":
                    delta = j1 - i1
                    pos = list(range(j1, j2))
                else:
                    delta = 0
                    pos = old.pos[i1:i2]
                for src, dst in zip(old.cache.layers, cache.layers):
                    k = src.keys[:, :, i1:i2, :]
                    if delta:
                        k = self.rotate_keys(k, delta)
                    dst.update(k.clone() if not delta else k, src.values[:, :, i1:i2, :].clone())
                st.ids.extend(new_ids[j1:j2])
                st.pos.extend(pos)
                st.reused_tokens += i2 - i1
                blocks.append({"op": "reuse", "old": [i1, i2], "new": [j1, j2], "delta": (j1 - i1)})
            elif tag in ("replace", "insert"):
                ids = new_ids[j1:j2]
                if mode == "shift" or mode == "recompute":
                    pos = list(range(j1, j2))
                else:
                    start = (max(st.pos) + 1) if st.pos else 0
                    pos = list(range(start, start + len(ids)))
                if ids:
                    self._forward(cache, ids, pos, "none")
                st.ids.extend(ids)
                st.pos.extend(pos)
                st.fresh_tokens += len(ids)
                blocks.append({"op": "fresh", "old": [i1, i2], "new": [j1, j2]})
            else:  # delete
                blocks.append({"op": "drop", "old": [i1, i2], "new": [j1, j2]})
        assert st.ids == list(new_ids), "splice must reproduce new_ids exactly"
        stats = {"mode": mode, "reused": st.reused_tokens, "fresh": st.fresh_tokens,
                 "old_len": len(old.ids), "new_len": len(new_ids), "blocks": blocks}
        return st, stats

    # -- decoding helpers --------------------------------------------------------------------
    @torch.no_grad()
    def greedy(self, st: CacheState, prompt_ids: list[int], max_new: int = 64, stop_ids: set[int] | None = None) -> list[int]:
        """Greedy-decode after appending prompt_ids; the cache is restored afterwards."""
        added = 0
        logits = self.extend(st, prompt_ids, keep_logits="last")
        added += len(prompt_ids)
        out = []
        stop_ids = stop_ids or set()
        for _ in range(max_new):
            nxt = int(logits[-1].argmax())
            out.append(nxt)
            if nxt in stop_ids:
                break
            logits = self.extend(st, [nxt], keep_logits="last")
            added += 1
        self.crop(st, added)
        return out

    @torch.no_grad()
    def teacher_force(self, st: CacheState, ids: list[int]) -> tuple[torch.Tensor, int]:
        """Append ids, return logits for all appended positions (logits[i] predicts ids[i+1]) and the count added."""
        logits = self.extend(st, ids, keep_logits="all")
        return logits, len(ids)


# -- self test -----------------------------------------------------------------------------------
def selftest(model_id: str, threads: int | None):
    eng = SpliceEngine(model_id, threads=threads)
    tok = eng.tok
    text_a = "The quick brown fox jumps over the lazy dog. " * 6
    text_b = "Meanwhile, the committee reviewed forty pages of budget tables in silence. " * 8
    text_c = "Afterwards everyone agreed the plan was reasonable and went home for dinner. " * 6
    A, B, C = (tok(t, add_special_tokens=False).input_ids for t in (text_a, text_b, text_c))
    P = tok("[note: middle block removed]", add_special_tokens=False).input_ids
    old_ids = A + B + C
    new_ids = A + P + C
    t0 = time.time()
    old, _ = eng.prefill(old_ids)
    print(f"prefill {len(old_ids)} tokens: {time.time() - t0:.1f}s")

    # 1. identity splice reproduces fresh logits
    same, stats = eng.splice(old, old_ids, "shift")
    assert stats["fresh"] == 0
    probe = tok(" The", add_special_tokens=False).input_ids
    l_fresh = eng.extend(eng.clone(old), probe, keep_logits="last")[-1]
    l_same = eng.extend(same, probe, keep_logits="last")[-1]
    print("identity splice max|dlogit| =", float((l_fresh - l_same).abs().max()))

    # 2. rotation: uniformly shifting a whole sequence's positions must equal rotating its keys
    shifted, _ = eng.prefill(A, pos=list(range(7, 7 + len(A))))
    base, _ = eng.prefill(A)
    errs = []
    for layer_s, layer_b in zip(shifted.cache.layers, base.cache.layers):
        rot = eng.rotate_keys(layer_b.keys, 7)
        errs.append(float((rot - layer_s.keys).abs().max() / (layer_s.keys.abs().max() + 1e-9)))
    print("rotation vs true shifted keys, max rel err per layer: max =", max(errs), "median =", sorted(errs)[len(errs) // 2])
    # values must be identical (no position dependence) when relative geometry is unchanged
    verr = max(float((s.values - b.values).abs().max()) for s, b in zip(shifted.cache.layers, base.cache.layers))
    print("values shifted vs base max abs diff =", verr)

    # 3. edit splice: prefix block must be bit-identical to a fresh prefill of new_ids; suffix is stale
    fresh_new, l_new = eng.prefill(new_ids)
    sp, stats = eng.splice(old, new_ids, "shift")
    print("splice stats:", {k: v for k, v in stats.items() if k != "blocks"}, stats["blocks"])
    pre = len(A)
    d_prefix = max(float((s.keys[:, :, :pre] - f.keys[:, :, :pre]).abs().max()) for s, f in zip(sp.cache.layers, fresh_new.cache.layers))
    suf0 = len(A) + len(P)
    cos = []
    for s, f in zip(sp.cache.layers, fresh_new.cache.layers):
        ks, kf = s.keys[0, :, suf0:], f.keys[0, :, suf0:]
        cos.append(float(torch.nn.functional.cosine_similarity(ks.flatten(0, 0), kf.flatten(0, 0), dim=-1).mean()))
    print(f"prefix keys max abs diff = {d_prefix:.2e}; stale-suffix key cosine to recomputed (per layer, mean): min={min(cos):.3f} max={max(cos):.3f}")
    gp, gstats = eng.splice(old, new_ids, "gap")
    print("gap positions tail:", gp.pos[-3:], "shift positions tail:", sp.pos[-3:])
    l_sp = eng.extend(sp, probe, keep_logits="last")[-1]
    l_gp = eng.extend(gp, probe, keep_logits="last")[-1]
    l_fr = eng.extend(fresh_new, probe, keep_logits="last")[-1]
    kl = lambda p, q: float(torch.sum(torch.softmax(p, -1) * (torch.log_softmax(p, -1) - torch.log_softmax(q, -1))))
    print(f"KL(fresh||shift)={kl(l_fr, l_sp):.4f} KL(fresh||gap)={kl(l_fr, l_gp):.4f} top1 fresh={tok.decode([int(l_fr.argmax())])!r} shift={tok.decode([int(l_sp.argmax())])!r} gap={tok.decode([int(l_gp.argmax())])!r}")
    # explicit mask vs HF mask path must agree, including non-contiguous (gap) positions
    eng.explicit_mask = True
    gp2, _ = eng.splice(old, new_ids, "gap")
    l_gp2 = eng.extend(gp2, probe, keep_logits="last")[-1]
    eng.explicit_mask = False
    print("gap mode: explicit mask vs HF mask max|dlogit| =", float((l_gp2 - l_gp).abs().max()))
    # recompute control equals fresh prefill
    rc, rstats = eng.splice(old, new_ids, "recompute")
    l_rc = eng.extend(rc, probe, keep_logits="last")[-1]
    print("recompute-control stats:", {k: v for k, v in rstats.items() if k != "blocks"}, "max|dlogit| vs fresh =", float((l_rc - l_fr).abs().max()))
    # greedy decode must restore the cache exactly
    before = len(fresh_new.ids)
    l_a = eng.extend(eng.clone(fresh_new), probe, keep_logits="last")[-1]
    eng.greedy(fresh_new, probe, max_new=5)
    assert len(fresh_new.ids) == before and fresh_new.cache.get_seq_length() == before, "greedy did not restore the cache"
    l_b = eng.extend(fresh_new, probe, keep_logits="last")[-1]
    print("greedy restores cache: max|dlogit| =", float((l_a - l_b).abs().max()))
    print("selftest done")


def timing(model_id: str, threads: int | None, n: int):
    eng = SpliceEngine(model_id, threads=threads)
    ids = eng.tok("lorem ipsum dolor sit amet " * (n // 5), add_special_tokens=False).input_ids[:n]
    t0 = time.time()
    st, _ = eng.prefill(ids)
    t1 = time.time()
    print(f"threads={torch.get_num_threads()} prefill {len(ids)} tok: {t1 - t0:.1f}s = {len(ids) / (t1 - t0):.0f} tok/s")
    t0 = time.time()
    eng.greedy(st, eng.tok("Hello", add_special_tokens=False).input_ids, max_new=16)
    print(f"decode 16 tok on {len(ids)} ctx: {(time.time() - t0) / 16 * 1000:.0f} ms/tok")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="Qwen/Qwen2.5-1.5B-Instruct")
    ap.add_argument("--threads", type=int, default=None)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--timing", type=int, default=0, help="prefill N tokens and report tok/s")
    a = ap.parse_args()
    if a.selftest:
        selftest(a.model, a.threads)
    if a.timing:
        timing(a.model, a.threads, a.timing)
