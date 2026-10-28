# RL/env/poip_env.py
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import gymnasium as gym
from gymnasium import spaces

from tools.warehouse_loader import WarehouseLoader
from tools.checker import (
    check_all_products_assigned,
    check_rack_capacity,
    check_aeration,
    check_circuit_contiguity,
    calculate_cost,
)

from RL.operators.base_operator import Candidate, Operator
from RL.env.observation_builder import build_observation


def _call_calculate_cost(instance: Any, assignment: np.ndarray) -> float:
    try:
        return float(calculate_cost(instance, assignment))
    except TypeError:
        pass
    try:
        return float(calculate_cost(instance, assignment.tolist()))
    except TypeError:
        pass
    try:
        return float(calculate_cost(assignment, instance))
    except TypeError:
        pass
    raise TypeError("[PoipEnv] Unable to call calculate_cost with known signatures.")


def _run_full_checker(instance: Any, assignment: np.ndarray) -> Tuple[bool, List[str]]:
    errors: List[str] = []
    ok = True

    def _safe(fn, name: str):
        nonlocal ok
        try:
            fn()
        except Exception as e:
            ok = False
            errors.append(f"{name}: {e}")

    _safe(lambda: check_all_products_assigned(instance, assignment), "all_products_assigned")
    _safe(lambda: check_rack_capacity(instance, assignment), "rack_capacity")
    _safe(lambda: check_aeration(instance, assignment), "aeration")
    _safe(lambda: check_circuit_contiguity(instance, assignment), "circuit_contiguity")

    return ok, errors


class PoipEnv(gym.Env):
    metadata = {"render_modes": ["human"]}

    def __init__(
        self,
        instance_dir: str,
        operator: Operator,
        topk: int = 8,
        max_steps: int = 40,
        init_solution_paths: Optional[List[str]] = None,
        invalid_penalty: float = -1000.0,
        final_cost_scale: float = 1e6,
        seed: int = 0,
        render_mode: Optional[str] = None,
    ):
        super().__init__()
        self.instance_dir = instance_dir
        self.operator = operator
        self.topk = int(topk)
        self.max_steps = int(max_steps)
        self.invalid_penalty = float(invalid_penalty)
        self.final_cost_scale = float(final_cost_scale)
        self.render_mode = render_mode

        self.rng = np.random.default_rng(seed)

        # Load instance (central loader)
        self.loader = WarehouseLoader(instance_dir)
        self.instance = self.loader.load_all()

        self.rack_capacities = np.asarray(self.loader.load_rack_capacity(), dtype=np.int64)
        self.product_circuit = np.asarray(self.loader.load_product_circuits(), dtype=np.int64)
        self.aisles_racks = self.loader.load_aisles_racks()

        self.num_racks = int(self.rack_capacities.shape[0])
        self.num_products = int(self.product_circuit.shape[0])

        # normalize circuits to 0-index
        min_c = int(self.product_circuit.min())
        if min_c == 1:
            self.product_circuit = self.product_circuit - 1
        elif min_c != 0:
            self.product_circuit = self.product_circuit - min_c
        self.num_circuits = int(self.product_circuit.max()) + 1

        # rack_to_aisle
        self.rack_to_aisle = np.full(self.num_racks, -1, dtype=np.int64)
        for a, racks in enumerate(self.aisles_racks):
            for r in racks:
                self.rack_to_aisle[int(r)] = int(a)

        missing = np.where(self.rack_to_aisle < 0)[0]
        for r in missing:
            if int(self.rack_capacities[int(r)]) > 0:
                raise ValueError(f"[PoipEnv] rack {int(r)} cap>0 but no aisle mapping.")
            self.rack_to_aisle[int(r)] = 0

        self.num_aisles = int(len(self.aisles_racks))

        self.init_solution_paths = init_solution_paths or []
        if len(self.init_solution_paths) == 0:
            raise ValueError("[PoipEnv] init_solution_paths is empty (need >=1 feasible start solution).")

        # Gym spaces
        obs_dim = 2 + self.num_aisles
        self.observation_space = spaces.Box(low=-1.0, high=1.0, shape=(obs_dim,), dtype=np.float32)
        self.action_space = spaces.Discrete(self.topk)

        # state
        self.assignment = np.full(self.num_products, -1, dtype=np.int64)
        self.rack_remaining = self.rack_capacities.copy()

        self.cur_step = 0
        self._episode_start_cost: Optional[float] = None

        self._last_candidates: List[Candidate] = []
        self.last_destroyed_size: int = 0

    # ---------------- IO ----------------
    def _load_solution_file(self, path: str) -> np.ndarray:
        with open(path, "r") as f:
            lines = [ln.strip() for ln in f if ln.strip() != ""]
        n = int(lines[0])
        if n != self.num_products:
            raise ValueError(f"Solution header n={n} != instance num_products={self.num_products}")
        arr = np.array([int(x) for x in lines[1:]], dtype=np.int64)
        if arr.shape[0] != self.num_products:
            raise ValueError(f"Solution lines={arr.shape[0]} != num_products={self.num_products}")
        return arr

    def _recompute_remaining_from_assignment(self):
        self.rack_remaining = self.rack_capacities.copy()
        for r in self.assignment:
            rr = int(r)
            if rr >= 0:
                self.rack_remaining[rr] -= 1

    def export_solution(self, out_path: str):
        import os
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w") as f:
            f.write(f"{int(self.num_products)}\n")
            for r in self.assignment.tolist():
                f.write(f"{int(r)}\n")

    # ---------------- helpers for operators ----------------
    def pick_destroy_circuit(self) -> int:
        sizes = np.array([np.sum(self.product_circuit == c) for c in range(self.num_circuits)], dtype=np.float64)
        sizes = np.maximum(1.0, sizes)
        probs = sizes / sizes.sum()
        return int(self.rng.choice(np.arange(self.num_circuits), p=probs))

    def destroy_circuit(self, circuit: int):
        prods = np.where(self.product_circuit == int(circuit))[0]
        for p in prods:
            r = int(self.assignment[int(p)])
            if r >= 0:
                self.rack_remaining[r] += 1
                self.assignment[int(p)] = -1

    def repair_circuit_interval(self, circuit: int, cand: Candidate) -> Tuple[bool, Dict[str, Any]]:
        if cand.kind != "interval":
            return False, {"error": "bad_candidate_kind", "kind": cand.kind}

        L = int(cand.payload["L"])
        R = int(cand.payload["R"])
        if L < 0 or R < 0 or L > R:
            return False, {"error": "bad_interval", "L": L, "R": R}

        prods = np.where(self.product_circuit == int(circuit))[0].astype(np.int64)
        demand = int(prods.size)
        if demand == 0:
            return True, {"circuit": int(circuit), "L": L, "R": R, "note": "empty"}

        racks = np.arange(L, R + 1, dtype=np.int64)
        racks = racks[self.rack_capacities[racks] > 0]
        if racks.size == 0:
            return False, {"circuit": int(circuit), "L": L, "R": R, "error": "no_valid_racks"}

        for p in prods.tolist():
            placed = False
            for r in racks.tolist():
                if self.rack_remaining[int(r)] >= 1:
                    self.assignment[int(p)] = int(r)
                    self.rack_remaining[int(r)] -= 1
                    placed = True
                    break
            if not placed:
                return False, {"circuit": int(circuit), "L": L, "R": R, "error": "cannot_fit_all_products"}

        return True, {"circuit": int(circuit), "L": L, "R": R, "placed": int(demand)}

    def get_circuit_interval(self, circuit: int) -> tuple[int | None, int | None]:
        prods = np.where(self.product_circuit == int(circuit))[0]
        racks = self.assignment[prods]
        racks = racks[racks >= 0]
        if racks.size == 0:
            return None, None
        return int(racks.min()), int(racks.max())

    # ---------------- Gym API ----------------
    def reset(self, *, seed: Optional[int] = None, options: Optional[dict] = None):
        super().reset(seed=seed)
        if seed is not None:
            self.rng = np.random.default_rng(seed)

        init_path = self.init_solution_paths[int(self.rng.integers(0, len(self.init_solution_paths)))]
        self.assignment = self._load_solution_file(init_path)
        self._recompute_remaining_from_assignment()

        self.cur_step = 0

        # candidats du 1er step
        self._last_candidates = self.operator.prepare(self)
        # sync taille détruite si opérateur le propose
        self.last_destroyed_size = int(getattr(self.operator, "last_destroyed_size", 0))

        ok0, _ = _run_full_checker(self.instance, self.assignment)
        self._episode_start_cost = _call_calculate_cost(self.instance, self.assignment) if ok0 else None

        obs = build_observation(self)
        info = {"init_solution": init_path, "operator": getattr(self.operator, "name", "operator")}
        return obs, info

    def step(self, action: int):
        terminated = False
        truncated = False
        info: Dict[str, Any] = {"operator": getattr(self.operator, "name", "operator")}

        if not self._last_candidates:
            self._last_candidates = self.operator.prepare(self)

        idx = int(np.clip(int(action), 0, len(self._last_candidates) - 1))
        cand = self._last_candidates[idx]

        # rollback backups
        assign_backup = self.assignment.copy()
        rem_backup = self.rack_remaining.copy()

        ok, det = self.operator.apply(self, cand)
        # --- après ok, det = self.operator.apply(self, cand)

        step_feasible = False
        step_errors = []

        if ok:
            step_feasible, step_errors = _run_full_checker(self.instance, self.assignment)
            if not step_feasible:
                ok = False  # on force l'échec du move

        if not ok:
            self.assignment = assign_backup
            self.rack_remaining = rem_backup

        info["step_feasible"] = bool(step_feasible)
        if step_errors:
            info["step_errors"] = step_errors[:3]


        info.update(det)

        # step reward
        reward = +1.0 if ok else -5.0

        self.cur_step += 1

        # next candidates
        self._last_candidates = self.operator.prepare(self)
        self.last_destroyed_size = int(getattr(self.operator, "last_destroyed_size", 0))

        if self.cur_step >= self.max_steps:
            truncated = True

        if truncated or terminated:
            ok_final, errors = _run_full_checker(self.instance, self.assignment)
            info["final_feasible"] = bool(ok_final)
            info["final_errors"] = errors

            if ok_final:
                cost = _call_calculate_cost(self.instance, self.assignment)
                info["final_cost"] = float(cost)
                reward += -float(cost) / self.final_cost_scale
                if self._episode_start_cost is not None:
                    info["delta_cost"] = float(cost - self._episode_start_cost)
            else:
                info["final_cost"] = None
                reward += self.invalid_penalty

        obs = build_observation(self)
        return obs, float(reward), terminated, truncated, info

    def render(self):
        if self.render_mode != "human":
            return
        print(f"[PoipEnv] step={self.cur_step}/{self.max_steps} operator={getattr(self.operator, 'name', 'operator')}")
