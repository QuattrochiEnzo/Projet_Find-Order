#!/usr/bin/env python3
from __future__ import annotations

"""
Solution state manager (in-memory).

Goal:
- Keep a clean, central state for interactive assignment:
    * product -> rack
    * rack -> products
- Provide helpers for:
    * add/remove assignments
    * counts per rack
    * dominant circuit per rack (for coloring)
    * export to rack_product_assignment.txt (spec format)

This module is intentionally UI-agnostic:
- You can plug it into SVG generation, Streamlit, FastAPI, etc.

Where to put:
    tools/solution_state.py
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from warehouse_loader import WarehouseInstance


@dataclass
class SolutionState:
    """
    Minimal state:
    - assignment[p] = r, for assigned products (includes debug placements if allow_special=True)
    - rack_contents[r] = [p1, p2, ...] (ONLY for valid, non-special, cap>0 racks)
    """
    n_products: int
    n_racks: int
    rack_capacity: List[int]

    assignment: Dict[int, int] = field(default_factory=dict)
    rack_contents: Dict[int, List[int]] = field(default_factory=dict)

    # Debug / "no-sanitize" support:
    # products placed on special/zero-capacity racks are stored here
    invalid_products: Dict[int, int] = field(default_factory=dict)  # p -> r

    @classmethod
    def empty_from_instance(cls, instance: WarehouseInstance) -> "SolutionState":
        n_racks = int(instance.metadata["num_racks"])
        n_products = int(instance.metadata["num_products"])
        rack_capacity = list(instance.rack_capacity)
        return cls(n_products=n_products, n_racks=n_racks, rack_capacity=rack_capacity)

    # -----------------------------
    # Core operations
    # -----------------------------

    def is_special_rack(self, rack_id: int) -> bool:
        # convention projet: 0 = START, n_racks-1 = END
        return rack_id == 0 or rack_id == (self.n_racks - 1)

    def rack_load(self, rack_id: int) -> int:
        return len(self.rack_contents.get(rack_id, []))

    def remaining_capacity(self, rack_id: int) -> int:
        return int(self.rack_capacity[rack_id]) - self.rack_load(rack_id)

    def assign_product(
        self,
        product_id: int,
        rack_id: int,
        *,
        allow_overflow: bool = False,
        allow_special: bool = False,
    ) -> None:
        """
        Assign product -> rack.
        If product was already assigned, it is moved.

        By default:
        - refuses special racks (START/END) and any rack with cap <= 0
        - refuses capacity overflow

        For visualization/debug:
        - allow_special=True lets you keep assignments on special/zero-capacity racks
          without crashing. These assignments are tracked in invalid_products and
          do NOT contribute to rack_contents (so they do not affect load/capacity).
        """
        self._check_product_id(product_id)
        self._check_rack_id(rack_id)

        # Always remove current placement first (keeps rack_contents and invalid_products consistent)
        if product_id in self.assignment:
            if self.assignment[product_id] == rack_id:
                return
            self.unassign_product(product_id)

        # Special/zero-capacity racks
        if self.is_special_rack(rack_id) or int(self.rack_capacity[rack_id]) <= 0:
            if allow_special:
                # Keep assignment for debug/visu only (do not touch rack_contents)
                self.assignment[product_id] = rack_id
                self.invalid_products[product_id] = rack_id
                return
            raise ValueError(f"Cannot assign to special/zero-capacity rack: {rack_id}")

        # Capacity overflow
        if (not allow_overflow) and self.remaining_capacity(rack_id) <= 0:
            raise ValueError(f"Rack {rack_id} is full (cap={int(self.rack_capacity[rack_id])}).")

        # Normal valid assignment
        self.assignment[product_id] = rack_id
        self.invalid_products.pop(product_id, None)
        self.rack_contents.setdefault(rack_id, []).append(product_id)

    def unassign_product(self, product_id: int) -> None:
        """Remove product assignment if present (from both valid and debug containers)."""
        old = self.assignment.pop(product_id, None)
        self.invalid_products.pop(product_id, None)

        if old is None:
            return

        lst = self.rack_contents.get(old)
        if not lst:
            return

        try:
            lst.remove(product_id)
        except ValueError:
            pass

        if not lst:
            self.rack_contents.pop(old, None)

    def clear_rack(self, rack_id: int) -> None:
        """Unassign all products currently placed in rack_id (valid placements)."""
        self._check_rack_id(rack_id)

        # Clear valid placements in this rack
        products = list(self.rack_contents.get(rack_id, []))
        for p in products:
            self.assignment.pop(p, None)
            self.invalid_products.pop(p, None)
        self.rack_contents.pop(rack_id, None)

        # Clear debug placements pointing to that rack
        to_drop = [p for p, r in self.invalid_products.items() if r == rack_id]
        for p in to_drop:
            self.assignment.pop(p, None)
            self.invalid_products.pop(p, None)

    # -----------------------------
    # Derivations for visualization
    # -----------------------------

    def rack_product_count(self, rack_id: int) -> int:
        return len(self.rack_contents.get(rack_id, []))

    def dominant_circuit(self, rack_id: int, product_circuit: List[int]) -> Optional[int]:
        """
        Return the dominant circuit id among products in rack_id.
        If empty -> None.
        Tie-breaker: smallest circuit id.
        """
        products = self.rack_contents.get(rack_id)
        if not products:
            return None

        counts: Dict[int, int] = {}
        for p in products:
            c = product_circuit[p]
            counts[c] = counts.get(c, 0) + 1

        best_c: Optional[int] = None
        best_k = -1
        for c, k in counts.items():
            if k > best_k or (k == best_k and (best_c is None or c < best_c)):
                best_c, best_k = c, k
        return best_c

    def circuits_in_rack(self, rack_id: int, product_circuit: List[int]) -> List[int]:
        """
        Return sorted list of circuits present in rack_id.
        Empty rack -> [].
        """
        products = self.rack_contents.get(rack_id, [])
        return sorted({product_circuit[p] for p in products})

    # -----------------------------
    # IO (spec format)
    # -----------------------------

    def to_solution_vector(self, *, default_rack: int = 0) -> List[int]:
        """
        Return a vector of size n_products with rack ids.
        Unassigned products go to default_rack (0 by default).

        Note: rack 0 is START (cap=0) in this project convention, so this is NOT a
              valid final solution unless everything is assigned elsewhere.
        """
        vec = [default_rack] * self.n_products
        for p, r in self.assignment.items():
            vec[p] = r
        return vec

    def export_solution_file(self, out_path: Path, *, require_complete: bool = True) -> None:
        """
        Write rack_product_assignment.txt:
            <n_products>
            <rack_of_product_0>
            ...
        If require_complete: raises if some products are unassigned.
        """
        if require_complete and len(self.assignment) != self.n_products:
            missing = self.n_products - len(self.assignment)
            raise ValueError(f"Cannot export: {missing} products still unassigned.")

        vec = self.to_solution_vector(default_rack=0)

        out_path.parent.mkdir(parents=True, exist_ok=True)
        lines = [str(self.n_products)] + [str(r) for r in vec]
        out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    @classmethod
    def from_solution_file(
        cls,
        instance: WarehouseInstance,
        path: Path,
        *,
        allow_special: bool = False,
        warn_invalid: bool = False,
        max_warn: int = 10,
    ) -> "SolutionState":
        """
        Load rack_product_assignment.txt into state.

        If allow_special=True:
        - do not crash if a product is assigned to a special/zero-capacity rack
        - track such products in state.invalid_products (and still keep assignment[p]=r)

        If warn_invalid=True:
        - print a short warning summary about invalid placements encountered
        """
        state = cls.empty_from_instance(instance)

        lines = [l.strip() for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        if not lines:
            raise ValueError(f"Empty solution file: {path}")

        n = int(lines[0])
        if n != state.n_products:
            raise ValueError(f"Solution n_products={n} != instance n_products={state.n_products}")
        if len(lines) != n + 1:
            raise ValueError(f"Solution has {len(lines)-1} assignments, expected {n}")

        invalid: List[str] = []

        for p, s in enumerate(lines[1:]):
            r = int(s)

            # Convention: 0 may be used as "unassigned" in some intermediate files.
            if r == 0:
                continue

            try:
                state.assign_product(p, r, allow_overflow=True, allow_special=allow_special)
            except ValueError as e:
                # If allow_special=False, this is a hard error; keep behavior explicit.
                raise

            # If allow_special=True and it got stored as invalid, keep a message
            if allow_special and p in state.invalid_products:
                invalid.append(f"(p={p}, r={r}, cap={int(state.rack_capacity[r])})")

        if warn_invalid and invalid:
            head = ", ".join(invalid[:max_warn])
            tail = "" if len(invalid) <= max_warn else f", ... (+{len(invalid) - max_warn})"
            print(f"[warn] invalid placements kept for debug: {len(invalid)} | {head}{tail}")

        return state

    # -----------------------------
    # Internal checks
    # -----------------------------

    def _check_product_id(self, product_id: int) -> None:
        if not (0 <= product_id < self.n_products):
            raise ValueError(f"Invalid product_id={product_id}, expected 0..{self.n_products-1}")

    def _check_rack_id(self, rack_id: int) -> None:
        if not (0 <= rack_id < self.n_racks):
            raise ValueError(f"Invalid rack_id={rack_id}, expected 0..{self.n_racks-1}")
