"""SmolVLA rollout helpers (client-side replan cadence)."""

from __future__ import annotations


def should_reset_action_queue(*, infer_index: int, reset_every: int) -> bool:
    """Whether this successful infer step should clear the server action queue.

    ``reset_every=0`` keeps the legacy SmolVLA behavior: reset only on the first
    successful infer of an episode, then let the server drain its full chunk
    (often ``n_action_steps=50``) before regenerating.

    ``reset_every=N`` (N>0) mirrors ACT blocking replan: clear the queue every N
    executed infer steps so a fresh observation seeds a new action chunk.
    """
    if int(infer_index) < 0:
        raise ValueError("infer_index must be >= 0")
    if int(reset_every) < 0:
        raise ValueError("reset_every must be >= 0")
    if int(infer_index) == 0:
        return True
    if int(reset_every) == 0:
        return False
    return int(infer_index) % int(reset_every) == 0
