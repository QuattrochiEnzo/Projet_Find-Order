# RL/training/run_agent.py
"""
Training entrypoint for the DQN agent.

This script:
- builds a POIP environment configured with a chosen operator (destroy / swap_circuits / swap_products)
- trains a DQN agent for a fixed number of episodes
- periodically saves:
  - best model according to cumulative episode reward
  - best feasible solution according to final exact cost (when available)

Notes:
- Hyperparameters are currently defined as module-level constants for quick iteration.
- The environment is assumed to expose a Gym-like API: reset(), step(), action_space, etc.
"""

from __future__ import annotations

import os
import sys
import random
from collections import Counter

from RL.env import PoipEnv
from RL.dqn import DQNAgent
from RL.operators import DestroyRepairOperator, SwapCircuitsOperator, SwapProductsOperator


# ============================================================
# Training configuration (quick-tweak constants)
# ============================================================

INSTANCE_DIR = "data/warehouse_big_/warehouse_big_market"             # Instance directory (contains solutions/)
MODELS_DIR = "dqn_models_market"                                      # Root folder for model checkpoints
RESUME_MODEL_PATH = None                                              # Optional checkpoint to resume from
# RESUME_MODEL_PATH = "dqn_models_category/swap_products/seed_0/best.pt"
# RESUME_MODEL_PATH = "dqn_models_category/swap_products/seed_1/best.pt"
# RESUME_MODEL_PATH = "dqn_models_category/swap_products/seed_2/best.pt"

EPISODES = 300                                                        # Number of training episodes
SEED_FROM_CLI = True                                                  # Read seed from CLI if available
SEED_FIXED = 0                                                        # Fallback seed value

RANDOM_EP_PROB = 0.1                                                  # Probability to force full-random episode

TOPK = 512                                                            # Operator candidate list size
MAX_STEPS = 20                                                        # Max steps per episode

INVALID_PENALTY = -1000.0                                             # Reward for invalid transitions
FINAL_COST_SCALE = 1e6                                                # Cost normalization factor

OPERATOR_KIND = "swap_products"                                       # Operator type: destroy / swap_circuits / swap_products

# DQN hyperparameters
GAMMA = 0.99                                                          # Discount factor
ALPHA = 5e-4                                                          # Learning rate (RMSprop)
EPSILON_START = 1                                                     # Initial epsilon for exploration
EPSILON_DECAY = 0.9997                                                # Epsilon multiplicative decay per update
MIN_EPSILON = 0.1                                                     # Minimum exploration rate

BUFFER_SIZE = 100000                                                  # Replay buffer capacity
BATCH_SIZE = 64                                                       # Mini-batch size
TARGET_UPDATE_FREQ = 400                                              # Hard target sync period (in updates)
UPDATE_EVERY_N_STEPS = 4                                              # Train every N environment steps

PRINT_EVERY_EP = 1                                                    # Print episode stats every N episodes
ERROR_STATS_EVERY = 50                                                # Print infeasibility histogram every N episodes
# ============================================================


def _build_init_pool(instance_dir: str, extra_rl_dir: str) -> list[str]:
    """Collect candidate initial solutions from instance `solutions/` directory."""
    solutions_dir = os.path.join(instance_dir, "solutions")            # Instance baseline solution folder
    init: list[str] = []                                               # Accumulator for solution paths

    if os.path.isdir(solutions_dir):
        for fn in os.listdir(solutions_dir):
            if fn.endswith(".txt"):
                init.append(os.path.join(solutions_dir, fn))           # Add one solution candidate

    # Optional: include RL-generated solutions as additional initializations
    # if os.path.isdir(extra_rl_dir):
    #     for fn in os.listdir(extra_rl_dir):
    #         if fn.endswith(".txt"):
    #             init.append(os.path.join(extra_rl_dir, fn))

    seen = set()                                                       # De-duplication set
    init = [os.path.normpath(p) for p in init]                         # Normalize path forms
    init = [p for p in init if not (p in seen or seen.add(p))]         # Remove duplicates
    init = [p for p in init if os.path.exists(p)]                      # Keep only existing files

    if not init:
        raise FileNotFoundError("[run_agent] No .txt solutions found in solutions/ or RL directory.")

    print(f"[INFO] Init pool size = {len(init)}")                      # Trace pool size
    return init


def _make_operator(kind: str, topk: int):
    """Instantiate the neighborhood operator selected by OPERATOR_KIND."""
    if kind == "destroy":
        return DestroyRepairOperator(topk=topk)                        # Destroy + repair neighborhood
    if kind == "swap_circuits":
        return SwapCircuitsOperator(topk=topk)                         # Circuit block swap neighborhood
    if kind == "swap_products":
        return SwapProductsOperator(topk=topk)                         # Product swap neighborhood
    raise ValueError(f"Unknown OPERATOR_KIND={kind}")                  # Guard invalid configuration


def main():
    """Run training loop and persist best model / best feasible solution snapshots."""
    if SEED_FROM_CLI:
        seed = int(sys.argv[1]) if len(sys.argv) > 1 else SEED_FIXED   # Read seed from argv or fallback
    else:
        seed = SEED_FIXED                                              # Use fixed seed only

    print(f"[INFO] Running with seed={seed}")                          # Trace RNG seed
    print(f"[INFO] INSTANCE_DIR={INSTANCE_DIR}")                       # Trace instance configuration
    print(f"[INFO] OPERATOR_KIND={OPERATOR_KIND}")                     # Trace operator configuration

    out_dir = os.path.join(MODELS_DIR, f"{OPERATOR_KIND}", f"seed_{seed}")  # Per-run output folder
    os.makedirs(out_dir, exist_ok=True)                                # Ensure output folder exists

    init_solutions = _build_init_pool(INSTANCE_DIR, extra_rl_dir=out_dir)   # Build init pool

    operator = _make_operator(OPERATOR_KIND, TOPK)                     # Build operator backend

    env = PoipEnv(
        instance_dir=INSTANCE_DIR,                                     # POIP instance directory
        operator=operator,                                             # Neighborhood operator
        topk=int(TOPK),                                                # Candidate actions count
        max_steps=int(MAX_STEPS),                                      # Episode length limit
        init_solution_paths=init_solutions,                            # Initial solution candidates
        invalid_penalty=float(INVALID_PENALTY),                        # Penalty for invalid moves
        final_cost_scale=float(FINAL_COST_SCALE),                      # Cost normalization factor
        seed=int(seed),                                                # Environment RNG seed
    )

    agent = DQNAgent(
        env=env,                                                       # Bind agent to environment spaces
        gamma=float(GAMMA),                                            # Discount factor
        alpha=float(ALPHA),                                            # Learning rate
        epsilon=float(EPSILON_START),                                  # Exploration start
        epsilon_decay=float(EPSILON_DECAY),                            # Exploration decay
        min_epsilon=float(MIN_EPSILON),                                # Exploration floor
        buffer_size=int(BUFFER_SIZE),                                  # Replay capacity
        batch_size=int(BATCH_SIZE),                                    # Mini-batch size
        target_update_freq=int(TARGET_UPDATE_FREQ),                    # Target sync period
    )

    if RESUME_MODEL_PATH is not None and os.path.exists(RESUME_MODEL_PATH):
        agent.load(RESUME_MODEL_PATH)                                  # Restore weights
        print(f"[INFO] Resumed from: {RESUME_MODEL_PATH}")             # Trace resume

    best_reward = -1e18                                                # Best cumulative reward (for checkpointing)
    best_feasible_cost = float("inf")                                  # Best final feasible cost (exact)
    best_feasible_path = os.path.join(out_dir, "best_feasible_solution.txt")  # Best feasible solution file
    best_model_path = os.path.join(out_dir, "best.pt")                 # Best model file

    err_counter = Counter()                                            # Histogram of infeasibility reasons

    for ep in range(int(EPISODES)):
        state, info0 = env.reset()                                     # Reset episode
        init_sol = info0.get("init_solution")                          # Trace selected initial solution

        done = False                                                   # Episode termination flag
        total = 0.0                                                    # Cumulative reward
        step_i = 0                                                     # Step counter
        last_info = {}                                                 # Keep last transition metadata

        force_random = (random.random() < float(RANDOM_EP_PROB))       # Optionally force random policy

        while not done:
            a = env.action_space.sample() if force_random else agent.play(state)  # Choose action
            next_state, r, terminated, truncated, info = env.step(a)              # Apply action
            done = terminated or truncated                                        # Update termination flag

            if step_i % int(UPDATE_EVERY_N_STEPS) == 0:
                agent.update(state, a, r, next_state, done)            # Periodic training update

            state = next_state                                         # Advance state
            total += float(r)                                          # Accumulate reward
            step_i += 1                                                # Advance step counter
            last_info = info                                           # Keep last info (final metrics)

        if total > best_reward:
            best_reward = total                                        # Update reward-based best
            agent.save(best_model_path)                                # Save best model checkpoint

        if last_info.get("final_feasible"):
            cost = int(last_info["final_cost"])                        # Read final exact cost
            path = os.path.join(out_dir, f"feasible_ep{ep}_cost{cost}.txt")  # Episode snapshot path
            env.export_solution(path)                                  # Save feasible episode solution

            if cost < best_feasible_cost:
                best_feasible_cost = cost                              # Update best feasible cost
                env.export_solution(best_feasible_path)                # Save best feasible solution
                print(f"[SAVE] New best feasible cost={best_feasible_cost} -> {best_feasible_path}")

        errs = last_info.get("final_errors") or []                      # Read feasibility errors (if any)
        for e in errs:
            key = e.split(":", 1)[0].strip()                           # Coarse error category prefix
            err_counter[key] += 1                                      # Update histogram

        if PRINT_EVERY_EP > 0 and (ep % int(PRINT_EVERY_EP) == 0):
            print(
                f"Episode {ep} | Reward: {total:.3f} | Eps: {agent.epsilon:.3f} | "
                f"init={os.path.basename(init_sol) if init_sol else None} | "
                f"feasible={last_info.get('final_feasible')} cost={last_info.get('final_cost')} "
                f"delta={last_info.get('delta_cost')}"
            )                                                          # Compact per-episode trace

        if ERROR_STATS_EVERY > 0 and (ep + 1) % int(ERROR_STATS_EVERY) == 0:
            print("[STATS] infeasible reasons top5:", err_counter.most_common(5))  # Debug infeasibility causes

    print("[DONE] Training finished.")
    print(f"[DONE] best_reward={best_reward}")
    print(f"[DONE] best_feasible_cost={best_feasible_cost if best_feasible_cost < float('inf') else None}")
    print(f"[DONE] out_dir={out_dir}")
    print(f"[DONE] best_model_path={best_model_path}")
    print(f"[DONE] best_feasible_path={best_feasible_path}")


if __name__ == "__main__":
    main()