# RL/training/infer.py
"""
Inference script for trained DQN policies.

Goal:
- Load a trained DQN model (if available)
- Run a short improvement trajectory on a POIP instance
- Optionally evaluate multiple actions per step ("try-all" / "tries") and commit the best feasible move
- Export best snapshots and final best solution to disk

Important implementation note:
This script performs *simulation* of candidate actions by taking a full environment snapshot,
running env.step(action), checking feasibility + cost, then restoring the snapshot for the next action.
"""

from __future__ import annotations

import argparse
import copy
import os
from typing import List

import numpy as np

from RL.env import PoipEnv
from RL.env.poip_env import _run_full_checker, _call_calculate_cost
from RL.dqn import DQNAgent
from RL.operators import DestroyRepairOperator, SwapCircuitsOperator, SwapProductsOperator


def _build_init_pool(instance_dir: str, extra_rl_dir: str) -> list[str]:
    """Collect candidate initial solutions from instance `solutions/` and optional RL seed directory."""
    solutions_dir = os.path.join(instance_dir, "solutions")                         # Instance baseline solutions
    init: list[str] = []                                                           # Accumulator for file paths

    if os.path.isdir(solutions_dir):
        for fn in os.listdir(solutions_dir):
            if fn.endswith(".txt"):
                init.append(os.path.join(solutions_dir, fn))                        # Add instance solution

    if os.path.isdir(extra_rl_dir):
        for fn in os.listdir(extra_rl_dir):
            if fn.endswith(".txt"):
                init.append(os.path.join(extra_rl_dir, fn))                         # Add RL-generated solution

    seen = set()                                                                    # De-duplication set
    init = [os.path.normpath(p) for p in init]                                      # Normalize path forms
    init = [p for p in init if not (p in seen or seen.add(p))]                      # Remove duplicates
    init = [p for p in init if os.path.exists(p)]                                   # Keep only existing files

    if not init:
        raise FileNotFoundError("[infer] No .txt solutions found in solutions/ or seed dir.")

    print(f"[INFO] Init pool size = {len(init)}")                                   # Trace candidate pool size
    return init


def _make_operator(kind: str, topk: int):
    """Instantiate the requested operator used as environment action primitive."""
    if kind == "destroy":
        return DestroyRepairOperator(topk=topk)                                     # Destroy + repair neighborhood
    if kind == "swap_circuits":
        return SwapCircuitsOperator(topk=topk)                                      # Circuit interval swaps
    if kind == "swap_products":
        return SwapProductsOperator(topk=topk)                                      # Product-level swaps
    raise ValueError(f"Unknown operator={kind}")                                    # Guard invalid CLI value


def main():
    """Run inference: pick init solution, follow policy, optionally scan several actions per step."""
    parser = argparse.ArgumentParser()
    parser.add_argument("seed_id", type=int)
    parser.add_argument("--instance-dir", type=str, default="data/warehouse_big_/warehouse_big_category")
    parser.add_argument("--steps", type=int, default=250)
    parser.add_argument("--topk", type=int, default=64)
    parser.add_argument("--tries", type=int, default=24)
    parser.add_argument("--try-all", action="store_true")
    parser.add_argument("--infer-seed", type=int, default=123)
    parser.add_argument("--operator", type=str, default="destroy", choices=["destroy", "swap_circuits", "swap_products"])
    args = parser.parse_args()

    seed_dir = os.path.join("dqn_models_market", args.operator, f"seed_{args.seed_id}")  # Model + outputs folder
    model_path = os.path.join(seed_dir, "best.pt")                                       # Trained weights path

    init_pool = _build_init_pool(args.instance_dir, seed_dir)                            # Candidate initial solutions

    best_init = os.path.join(seed_dir, "infer_step125_cost_136182769.txt")               # Optional forced init
    if os.path.exists(best_init):
        init_pool = [best_init]                                                         # Override pool for debugging
        print("[INFO] Forced init:", best_init)                                          # Trace forced init choice

    operator = _make_operator(args.operator, args.topk)                                  # Create operator backend

    env = PoipEnv(
        instance_dir=args.instance_dir,                                                  # POIP instance directory
        operator=operator,                                                               # Neighborhood operator
        topk=int(args.topk),                                                             # Candidate actions count
        max_steps=int(args.steps),                                                       # Episode length limit
        init_solution_paths=init_pool,                                                   # Initial solution candidates
        invalid_penalty=-1000.0,                                                         # Reward for invalid transitions
        final_cost_scale=1e6,                                                            # Cost normalization factor
        seed=int(args.infer_seed),                                                       # RNG seed for environment
    )

    agent = DQNAgent(env=env)                                                            # Build DQN agent wrapper
    if os.path.exists(model_path):
        agent.load(model_path)                                                           # Restore trained policy
        print(f"[INFO] Loaded model: {model_path}")                                       # Trace model load
    else:
        print(f"[WARN] Model not found: {model_path} -> policy random-ish.")              # Fallback to random policy
    agent.epsilon = 0.0                                                                  # Force greedy action selection

    state, info0 = env.reset()                                                           # Initialize environment
    print("[INFO] Init solution picked:", info0.get("init_solution"))                     # Trace chosen init solution

    ok0, err0 = _run_full_checker(env.instance, env.assignment)                           # Validate init solution
    if not ok0:
        print("[WARN] Initial solution NOT feasible.")                                    # Warn on invalid init
        print("[WARN] Errors:", err0[:3])                                                 # Print first errors only

    start_cost = _call_calculate_cost(env.instance, env.assignment) if ok0 else None      # Compute initial cost
    if start_cost is not None:
        print("[INFO] Start cost:", int(start_cost))                                      # Trace initial cost

    best_cost = float(start_cost) if start_cost is not None else float("inf")             # Best-so-far cost
    best_assignment = env.assignment.copy() if start_cost is not None else None           # Best-so-far snapshot

    rng = np.random.default_rng(int(args.infer_seed))                                     # RNG for action ordering

    done = False                                                                          # Episode termination flag
    step_i = 0                                                                            # External step counter

    while not done:
        # ---------------------------------------------------------------------
        # Snapshot full environment state for action simulation
        # ---------------------------------------------------------------------
        assign_backup = env.assignment.copy()                                              # Backup assignment vector
        rem_backup = env.rack_remaining.copy()                                             # Backup remaining capacities
        last_candidates_backup = copy.deepcopy(env._last_candidates)                       # Backup operator cache
        last_destroyed_size_backup = int(env.last_destroyed_size)                          # Backup operator metadata
        cur_step_backup = int(env.cur_step)                                                # Backup internal step counter

        preferred = agent.play(state)                                                      # Policy-preferred action

        if args.try_all:
            action_order: List[int] = list(range(env.action_space.n))                      # Enumerate all actions
            if preferred in action_order:
                action_order.remove(preferred)                                             # Put preferred action first
                action_order = [preferred] + action_order
        else:
            others = [a for a in range(env.action_space.n) if a != preferred]              # Build alternative list
            rng.shuffle(others)                                                            # Randomize alternatives
            action_order = [preferred] + others[: max(0, int(args.tries) - 1)]             # Limit trials budget

        best_local_cost = None                                                             # Best simulated cost
        best_local_pack = None                                                             # Best simulated snapshot

        # ---------------------------------------------------------------------
        # Explore candidate actions (simulate) and keep the best feasible move
        # IMPORTANT: restore env.cur_step before each simulation
        # ---------------------------------------------------------------------
        for a in action_order:
            env.assignment = assign_backup.copy()                                          # Restore assignment
            env.rack_remaining = rem_backup.copy()                                         # Restore capacities
            env._last_candidates = copy.deepcopy(last_candidates_backup)                   # Restore operator cache
            env.last_destroyed_size = last_destroyed_size_backup                           # Restore operator metadata
            env.cur_step = cur_step_backup                                                 # Restore internal step counter

            next_state, _r, terminated, truncated, info = env.step(int(a))                 # Simulate action

            ok, _errs = _run_full_checker(env.instance, env.assignment)                    # Check feasibility
            if not ok:
                continue                                                                   # Discard infeasible candidate

            cost = _call_calculate_cost(env.instance, env.assignment)                      # Evaluate exact cost
            if (best_local_cost is None) or (cost < best_local_cost):
                best_local_cost = float(cost)                                              # Update best cost
                best_local_pack = (                                                        # Save best snapshot
                    env.assignment.copy(),
                    env.rack_remaining.copy(),
                    next_state,
                    bool(terminated),
                    bool(truncated),
                    info,
                    int(env.cur_step),                                                     # Save corresponding cur_step
                )

        if best_local_pack is None:
            # -----------------------------------------------------------------
            # No feasible action found: restore snapshot and stop
            # -----------------------------------------------------------------
            env.assignment = assign_backup                                                  # Restore assignment
            env.rack_remaining = rem_backup                                                 # Restore capacities
            env._last_candidates = last_candidates_backup                                   # Restore operator cache
            env.last_destroyed_size = last_destroyed_size_backup                            # Restore operator metadata
            env.cur_step = cur_step_backup                                                  # Restore internal step counter
            print(f"[STOP] step {step_i}: no feasible action among tried={len(action_order)} (topk={env.action_space.n})")
            break

        # ---------------------------------------------------------------------
        # Commit best move found during simulation
        # ---------------------------------------------------------------------
        env.assignment, env.rack_remaining, state, terminated, truncated, info, best_cur_step = best_local_pack
        env.cur_step = best_cur_step                                                       # Commit internal step counter
        done = terminated or truncated                                                     # Update termination flag

        if truncated:
            print(f"[DEBUG] truncated at step_i={step_i} env.cur_step={env.cur_step} env.max_steps={env.max_steps}")

        if best_local_cost is not None and best_local_cost < best_cost:
            best_cost = best_local_cost                                                    # Update global best cost
            best_assignment = env.assignment.copy()                                        # Save global best snapshot
            print(f"[BEST] step {step_i}: improved cost -> {int(best_cost)}")              # Trace improvement

        if step_i % 25 == 0:
            print(f"[INFO] step {step_i}: current_best={int(best_cost) if best_cost < float('inf') else None}")
            env.export_solution(os.path.join(seed_dir, f"infer_step{step_i}_cost_{int(best_cost)}.txt"))  # Periodic snapshot

        step_i += 1                                                                        # Advance external counter

    if best_assignment is None:
        okf, errf = _run_full_checker(env.instance, env.assignment)                        # Validate last state
        print("[ERROR] No feasible solution kept during infer.")
        print("Final feasible:", okf)
        print("Final errors:", errf[:3])
        return

    out_path = os.path.join(seed_dir, f"infer_best_cost_{int(best_cost)}.txt")            # Final output file path
    env.assignment = best_assignment                                                       # Restore best assignment
    env._recompute_remaining_from_assignment()                                             # Rebuild capacities from assignment

    ok, errors = _run_full_checker(env.instance, env.assignment)                           # Final feasibility check
    if not ok:
        print("[ERROR] Best snapshot became infeasible unexpectedly.")
        print("Errors:", errors[:3])
        return

    env.export_solution(out_path)                                                          # Export final best solution
    print("[DONE] Exported:", out_path)
    print("[DONE] Best cost:", int(best_cost))


if __name__ == "__main__":
    main()