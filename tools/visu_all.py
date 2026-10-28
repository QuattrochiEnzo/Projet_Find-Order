#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()                                                                          # CLI argument parser
    ap.add_argument("--bench-dir", default="data/bench_milp", help="Directory containing sub-instances")    # bench root directory
    ap.add_argument("--limit", type=int, default=0, help="Limit to N instances (0 = all)")                  # optional cap on instances
    ap.add_argument("--skip-if-exists", action="store_true", help="Skip if SVG already exists")             # avoid regenerating outputs
    ap.add_argument(
        "--heuristic-path",
        default="solutions/heuristic_greedy_circuit_smart/rack_product_assignment_greedy_circuit_smart_pdesc_psauto_score_cdesc.txt",
        help="Relative path (from instance_dir) to the heuristic solution",
    )                                                                                                       # heuristic solution path
    ap.add_argument(
        "--gurobi-name",
        default="solutions/solution_gurobi_ws.txt",
        help="Relative path (from instance_dir) to the Gurobi solution",
    )                                                                                                       # Gurobi solution path
    args = ap.parse_args()                                                                                  # parse argv

    root = Path(__file__).resolve().parent.parent                                                           # repository root
    bench_dir = (root / args.bench_dir).resolve()                                                            # resolved bench directory
    visu_script = (root / "tools" / "visualisation_solution.py").resolve()                                   # visualization script path

    if not bench_dir.exists():
        raise FileNotFoundError(f"bench-dir not found: {bench_dir}")                                        # English error message

    inst_dirs = sorted([p for p in bench_dir.iterdir() if p.is_dir() and p.name.startswith("sub__")])       # list sub-instances
    if args.limit and args.limit > 0:
        inst_dirs = inst_dirs[: args.limit]                                                                 # apply limit

    n_ok = 0                                                                                                # successful visualizations
    n_fail = 0                                                                                               # failed visualizations
    n_skip = 0                                                                                               # skipped runs (output already present)

    for inst_path in inst_dirs:
        inst_key = f"bench_milp/{inst_path.name}"                                                           # instance alias for resolve_instance_dir
        out_dir = inst_path / "out"                                                                          # per-instance output directory
        out_dir.mkdir(parents=True, exist_ok=True)                                                           # ensure output directory exists

        # --------
        # 1) Heuristic
        # --------
        heur_sol = inst_path / args.heuristic_path                                                           # heuristic solution file
        heur_out = out_dir / "heuristic_greedy_circuit_smart.svg"                                            # heuristic SVG output

        if heur_sol.exists():
            if args.skip_if_exists and heur_out.exists():
                print(f"[skip] {inst_path.name} (heuristic)")                                                # skip if already generated
                n_skip += 1                                                                                  # update skip counter
            else:
                cmd = [
                    sys.executable,                                                                          # current Python interpreter
                    str(visu_script),
                    "--instance",
                    inst_key,
                    "--solution",
                    str(heur_sol),
                    "--out",
                    str(heur_out),
                ]                                                                                            # visualization command (heuristic)
                print(f"[run]  {inst_path.name} (heuristic) -> {heur_out}")                                  # run banner
                try:
                    subprocess.run(cmd, check=True)                                                          # execute visualization
                    n_ok += 1                                                                                # update success counter
                except subprocess.CalledProcessError as e:
                    n_fail += 1                                                                              # update failure counter
                    print(f"[fail] {inst_path.name} (heuristic): {e}")                                       # report failure
        else:
            print(f"[warn] missing heuristic solution: {heur_sol}")                                          # solution file not found

        # --------
        # 2) Gurobi (allow-special)
        # --------
        gurobi_sol = inst_path / args.gurobi_name                                                             # gurobi solution file
        gurobi_out = out_dir / "solution_gurobi_ws.svg"                                                       # gurobi SVG output

        if gurobi_sol.exists():
            if args.skip_if_exists and gurobi_out.exists():
                print(f"[skip] {inst_path.name} (gurobi)")                                                    # skip if already generated
                n_skip += 1                                                                                  # update skip counter
            else:
                cmd = [
                    sys.executable,                                                                          # current Python interpreter
                    str(visu_script),
                    "--instance",
                    inst_key,
                    "--solution",
                    str(gurobi_sol),
                    "--out",
                    str(gurobi_out),
                    "--allow-special",                                                                       # allow START/END assignments (if needed)
                ]                                                                                            # visualization command (gurobi)
                print(f"[run]  {inst_path.name} (gurobi) -> {gurobi_out}")                                   # run banner
                try:
                    subprocess.run(cmd, check=True)                                                          # execute visualization
                    n_ok += 1                                                                                # update success counter
                except subprocess.CalledProcessError as e:
                    n_fail += 1                                                                              # update failure counter
                    print(f"[fail] {inst_path.name} (gurobi): {e}")                                          # report failure
        else:
            print(f"[warn] missing gurobi solution: {gurobi_sol}")                                           # solution file not found

    print(f"\n[done] ok={n_ok} fail={n_fail} skip={n_skip} bench_dir={bench_dir}")                           # final summary


if __name__ == "__main__":
    main()                                                                                                   # CLI entrypoint