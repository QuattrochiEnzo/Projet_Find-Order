#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
from pathlib import Path

from export_instance import export_one
from parser import resolve_instance_path
from warehouse_loader import WarehouseLoader

# Keep exactly the same validity checks as the visualization pipeline                               # ensure consistent feasibility logic
from checker import (
    check_all_products_assigned,
    check_rack_capacity,
    check_aeration,
    check_circuit_contiguity,
    calculate_cost,
)

from solution_state import SolutionState

# ============================================================
# Paths
# ============================================================

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))                                     # project root (two levels above tools/)
DEFAULT_CPP_SOLVER = os.path.join(ROOT_DIR, "metaheuristics", "build", "bin", "heuristic_greedy_circuit_smart")  # main C++ solver binary
DEFAULT_ALIASES = ["toy", "market", "family", "category"]                                                  # standard dataset aliases
DEFAULT_CPP_NS = os.path.join(ROOT_DIR, "metaheuristics", "build", "bin", "heuristic_aeration_ns")         # optional improvement (NS) binary
OUTPUT_RE = re.compile(r"\boutput=([^\s]+)")                                                               # stdout marker: output=<filename>


def _extract_output_filename(stdout: str) -> str | None:
    m = OUTPUT_RE.search(stdout)                                                                           # locate output marker in solver logs
    return m.group(1) if m else None                                                                       # return filename or None


def _evaluate_with_visu_checks(instance, solution_path: Path) -> tuple[bool, int | None, str]:
    """Run the same unit checks as the visualization + compute cost (shared definition of validity)."""
    try:
        state = SolutionState.from_solution_file(instance, solution_path)                                  # load solution into in-memory state
        rack_of_product = state.to_solution_vector(default_rack=0)                                         # convert state to assignment vector

        check_all_products_assigned(instance, rack_of_product)                                             # validate rack indices + vector length
        check_rack_capacity(instance, rack_of_product)                                                     # validate capacity constraints
        check_aeration(instance, rack_of_product)                                                          # validate aeration constraints
        check_circuit_contiguity(instance, rack_of_product)                                                # validate circuit interval constraints

        cost = calculate_cost(instance, rack_of_product)                                                   # compute objective value
        return True, cost, f"OK | cost={cost}"                                                             # success status string
    except ValueError as e:
        msg = str(e)                                                                                       # capture checker message
        if len(msg) > 140:
            msg = msg[:137] + "..."                                                                        # keep logs compact (single-line summary)
        return False, None, f"INVALID | {msg}"                                                             # invalid status string


def _visu_script_path() -> Path | None:
    tools_dir = Path(__file__).resolve().parent                                                            # locate tools directory
    p1 = tools_dir / "visualisation_solution.py"                                                           # preferred visualization script name
    if p1.exists():
        return p1                                                                                          # use if present
    p2 = tools_dir / "visu.py"                                                                             # legacy / alternate visualization script
    if p2.exists():
        return p2                                                                                          # use if present
    return None                                                                                            # no visualization script available


def _run_visualisation(instance_value: str, solution_path: Path) -> None:
    """Generate SVG/HTML visualization for the given solution (optional step)."""
    visu_script = _visu_script_path()                                                                      # find visualization entrypoint
    if visu_script is None:
        print("[warn] Visualisation non lancée: aucun script visu trouvé (visualisation_solution.py / visu.py).")
        return                                                                                             # keep pipeline robust if missing

    cmd = [
        "python",                                                                                          # run with default python interpreter
        str(visu_script),
        "--instance",
        instance_value,
        "--solution",
        str(solution_path),
        "--html",
    ]                                                                                                      # build visualization command
    print("cmd:", " ".join(cmd))                                                                           # echo command for reproducibility
    subprocess.run(cmd, check=True)                                                                        # run and raise on failure


def _override_arg(argv: list[str], name: str, value: str) -> list[str]:
    """
    Replace (or append) a '--name value' pair inside argv.
    This keeps all other solver args unchanged.
    """
    out: list[str] = []                                                                                    # rebuilt argv
    i = 0                                                                                                  # manual scan index
    while i < len(argv):
        if argv[i] == name:                                                                                # matched argument name
            out.append(name)                                                                               # keep name
            out.append(value)                                                                              # override value
            i += 1                                                                                          # move to value position
            if i < len(argv) and not argv[i].startswith("--"):
                i += 1                                                                                      # skip old value token
            continue                                                                                        # continue scanning
        out.append(argv[i])                                                                                # passthrough unchanged token
        i += 1                                                                                              # advance scan

    if name not in out:
        out += [name, value]                                                                               # append if argument did not exist
    return out                                                                                              # return updated argv


def _pretty_run_tag(product_dir: str, circuit_dir: str, product_score: str) -> str:
    return f"p={product_dir} c={circuit_dir} score={product_score}"                                        # compact config identifier


def _run_aeration_ns(
    instance_dat_path: Path,
    solution_in: Path,
    solution_dir: Path,
    ns_solver: str,
    ns_args: list[str],
) -> Path | None:
    """
    Run heuristic_aeration_ns on an existing solution.
    Returns the improved solution path on success, otherwise None.
    """
    cmd = [ns_solver, *ns_args, str(instance_dat_path), str(solution_in), str(solution_dir)]               # build NS command line
    print("cmd:", " ".join(cmd))                                                                           # echo command for reproducibility

    proc = subprocess.run(cmd, capture_output=True, text=True)                                             # run and capture logs

    if proc.stdout.strip():
        print(proc.stdout.strip())                                                                         # forward stdout
    if proc.stderr.strip():
        print(proc.stderr.strip())                                                                         # forward stderr

    if proc.returncode != 0:
        print(f"[warn] aeration_ns exited code={proc.returncode}")                                         # non-zero exit => ignore improvement
        return None                                                                                        # treat as no improvement

    out_name = _extract_output_filename(proc.stdout)                                                       # parse output filename from stdout
    if out_name:
        out_path = solution_dir / out_name                                                                 # resolve to solution directory
        if out_path.exists():
            return out_path                                                                                # return detected output file

    fallback = solution_dir / "rack_product_assignment_aeration_ns.txt"                                     # fallback conventional filename
    if fallback.exists():
        return fallback                                                                                    # return fallback file if present

    print("[warn] aeration_ns: no output file detected")                                                   # last-resort warning
    return None                                                                                            # no output found


# ============================================================
# Core pipeline
# ============================================================

def _run_solver_once(
    instance_value: str,
    instance,
    inst_dir: Path,
    cpp_solver: str,
    base_solver_args: list[str],
    product_dir: str,
    circuit_dir: str,
    product_score: str,
    do_visu: bool,
    fail_on_invalid: bool,
    do_improve: bool,
    ns_solver: str,
    ns_args: list[str],
) -> tuple[bool, int | None, str, Path]:
    """
    Run one configuration, evaluate it, optionally improve it, optionally visualize it.
    Returns the best solution path for this configuration (greedy or improved).
    """
    instance_dat_path = inst_dir / "instance.dat"                                                          # solver input file
    solution_dir = inst_dir / "solutions"                                                                   # all outputs stored here
    solution_dir.mkdir(parents=True, exist_ok=True)                                                         # ensure output directory exists

    solver_args = list(base_solver_args)                                                                    # copy base args (do not mutate caller)
    solver_args = _override_arg(solver_args, "--product-dir", product_dir)                                  # override product ordering dir
    solver_args = _override_arg(solver_args, "--circuit-dir", circuit_dir)                                  # override circuit ordering dir
    solver_args = _override_arg(solver_args, "--product-score", product_score)                              # override scoring strategy

    tag = _pretty_run_tag(product_dir, circuit_dir, product_score)                                          # config label
    print(f"\n--- RUN {tag} ---")                                                                           # run banner

    cmd = [cpp_solver, *solver_args, str(instance_dat_path), str(solution_dir)]                             # build solver command line
    print("cmd:", " ".join(cmd))                                                                            # echo command for reproducibility

    proc = subprocess.run(cmd, capture_output=True, text=True)                                              # run solver and capture logs

    if proc.stdout.strip():
        print(proc.stdout.strip())                                                                          # forward stdout
    if proc.stderr.strip():
        print(proc.stderr.strip())                                                                          # forward stderr

    if proc.returncode != 0:
        status = f"[FAIL] solver exited code={proc.returncode}"                                             # failure status string
        print(status)                                                                                       # print failure
        if fail_on_invalid:
            raise SystemExit(status)                                                                        # fail fast if requested
        return False, None, status, solution_dir                                                            # return placeholder path (dir)

    out_name = _extract_output_filename(proc.stdout)                                                        # extract output filename from logs
    if not out_name:
        raise RuntimeError(
            "Impossible de récupérer le nom du fichier généré par le solver.\n"
            "Attendu dans stdout: 'output=<filename>'.\n"
            "Stdout reçu:\n" + proc.stdout
        )                                                                                                   # keep error explicit for debugging

    out_path = solution_dir / out_name                                                                      # resolve solver output path
    if not out_path.exists():
        raise FileNotFoundError(f"Le solver annonce output={out_name} mais le fichier n'existe pas: {out_path}")  # detect solver misreporting

    ok, cost, status = _evaluate_with_visu_checks(instance, out_path)                                       # evaluate greedy solution
    print(status)                                                                                           # print evaluation summary

    if (not ok) and fail_on_invalid:
        raise SystemExit(f"[FAIL] Solution invalide ({tag}): {status}")                                     # fail fast on invalid solution

    best_path = out_path                                                                                    # best path (current)
    best_ok = ok                                                                                            # best validity flag
    best_cost = cost                                                                                        # best cost
    best_status = status                                                                                    # best status text

    if do_improve and best_ok:
        print("--- IMPROVE (aeration_ns) ---")                                                              # improvement banner
        improved_path = _run_aeration_ns(
            instance_dat_path=instance_dat_path,
            solution_in=out_path,
            solution_dir=solution_dir,
            ns_solver=ns_solver,
            ns_args=list(ns_args),                                                                          # pass only NS args (no solver args)
        )

        if improved_path is not None:
            ok2, cost2, status2 = _evaluate_with_visu_checks(instance, improved_path)                       # evaluate improved solution
            print(f"IMPROVED: {status2}")                                                                   # print improved evaluation

            if ok2 and cost2 is not None and (best_cost is None or cost2 < best_cost):                      # keep best valid solution (min cost)
                best_path = improved_path                                                                   # update best path
                best_ok = ok2                                                                               # update best validity flag
                best_cost = cost2                                                                           # update best cost
                best_status = f"OK | improved cost={cost2}"                                                 # update best status string

    if do_visu and best_ok:
        print("--- VISUALISATION (BEST CONFIG) ---")                                                        # visualization banner
        _run_visualisation(instance_value, best_path)                                                       # generate HTML/SVG for best config

    return best_ok, best_cost, best_status, best_path                                                       # return best result for this config


def run_one(
    instance_value: str,
    cpp_solver: str,
    solver_args: list[str],
    do_visu: bool,
    fail_on_invalid: bool,
    do_improve: bool,
    ns_solver: str,
    ns_args: list[str],
    use_corrmix: bool = False,
    alpha_step: float = 0.05,
) -> None:
    inst_dir = resolve_instance_path(instance_value)                                                        # resolve alias/path to instance directory
    inst_dir = Path(inst_dir)                                                                               # normalize to Path
    print(f"\n=== INSTANCE: {inst_dir.name} ===")                                                           # instance banner

    export_one(instance_value)                                                                              # (re)generate instance.dat (incl. enriched)

    instance = WarehouseLoader(str(inst_dir)).load_all()                                                     # load instance for evaluation/checks

    combos_dirs: list[tuple[str, str]] = [
        ("asc", "asc"),
        ("asc", "desc"),
        ("desc", "asc"),
        ("desc", "desc"),
    ]                                                                                                       # product/circuit direction combinations

    if use_corrmix:
        scores: list[str] = ["corrmix"]                                                                     # only corrmix when alpha sweep is enabled
        step = float(alpha_step)                                                                            # normalize CLI arg
        if step <= 0 or step > 1:
            raise SystemExit(f"Invalid --alpha-step={step} (must be in (0,1])")                             # guard invalid step sizes
        n = int(round(1.0 / step))                                                                          # number of steps to cover [0,1]
        alpha_grid = [round(i * step, 10) for i in range(n + 1)]                                            # build alpha grid
        if alpha_grid[-1] < 1.0 - 1e-9:
            alpha_grid.append(1.0)                                                                          # ensure endpoint is included
        alpha_grid[-1] = 1.0                                                                                # force exact endpoint
    else:
        scores = ["actionable", "popularity"]                                                               # default product score modes
        alpha_grid = [None]                                                                                # no alpha sweep

    results: list[tuple[str, bool, int | None, str, Path]] = []                                             # (tag, ok, cost, status, best_path)

    solution_dir = inst_dir / "solutions"                                                                   # output directory for this instance
    solution_dir.mkdir(parents=True, exist_ok=True)                                                         # ensure it exists

    best_global_cost: int | None = None                                                                     # best cost found so far
    best_global_tag: str | None = None                                                                      # label of best config
    best_global_frozen: Path | None = None                                                                  # snapshot file for best solution
    best_global_idx = 0                                                                                     # snapshot counter (monotonic)

    total_runs = len(scores) * len(combos_dirs) * len(alpha_grid)                                           # total configurations to evaluate
    print(f"\n--- GRID SEARCH ({total_runs} configs) ---")                                                  # grid search banner

    for alpha in alpha_grid:
        base_args = list(solver_args)                                                                       # copy user-provided solver args
        if alpha is not None:
            base_args = _override_arg(base_args, "--alpha", str(alpha))                                     # override alpha for this run

        for product_score in scores:
            for product_dir, circuit_dir in combos_dirs:
                tag = _pretty_run_tag(product_dir, circuit_dir, product_score)                              # base config tag
                if alpha is not None:
                    tag = f"{tag} alpha={alpha:g}"                                                          # extend tag with alpha value

                ok, cost, status, best_path = _run_solver_once(
                    instance_value=instance_value,
                    instance=instance,
                    inst_dir=inst_dir,
                    cpp_solver=cpp_solver,
                    base_solver_args=base_args,
                    product_dir=product_dir,
                    circuit_dir=circuit_dir,
                    product_score=product_score,
                    do_visu=do_visu,
                    fail_on_invalid=fail_on_invalid,
                    do_improve=do_improve,
                    ns_solver=ns_solver,
                    ns_args=ns_args,
                )
                results.append((tag, ok, cost, status, best_path))                                          # record run result

                if ok and cost is not None and (best_global_cost is None or cost < best_global_cost):
                    best_global_cost = cost                                                                 # update best global cost
                    best_global_tag = tag                                                                   # update best global tag
                    best_global_idx += 1                                                                    # bump snapshot counter

                    frozen = solution_dir / f"__BEST_SNAPSHOT_{best_global_idx}.txt"                        # snapshot file name
                    shutil.copy2(best_path, frozen)                                                         # freeze best solution immediately
                    best_global_frozen = frozen                                                             # remember snapshot path

                    print(f"[BEST UPDATE] cost={best_global_cost} -> {frozen.name}")                        # log global update

    print("\n=== SUMMARY ===")                                                                              # summary banner

    def _key(x: tuple[str, bool, int | None, str, Path]):
        tag, ok, cost, _, _ = x                                                                             # unpack tuple
        return (0 if ok else 1, cost if cost is not None else 10**18, tag)                                  # valid first, then cost, then tag

    for tag, ok, cost, status, _path in sorted(results, key=_key):
        if ok:
            print(f"[OK]   {tag}  cost={cost}")                                                             # report valid configuration
        else:
            print(f"[BAD]  {tag}  {status}")                                                               # report invalid configuration

    if best_global_frozen is not None and best_global_cost is not None and best_global_tag is not None:
        print(f"\nBEST: {best_global_tag} cost={best_global_cost}")                                         # print best global config

        best_out = solution_dir / "rack_product_assignment_greedy_circuit_smart__BEST.txt"                  # canonical best output file
        shutil.copy2(best_global_frozen, best_out)                                                          # write canonical best file
        print(f"[SAVED] {best_out}")                                                                        # confirm save

        if do_visu:
            ok_eval, _c, _st = _evaluate_with_visu_checks(instance, best_out)                               # sanity-check saved file
            if ok_eval:
                print("--- VISUALISATION (BEST GLOBAL) ---")                                                # visualization banner
                _run_visualisation(instance_value, best_out)                                                # visualize best global solution
            else:
                print("[warn] BEST file saved but fails checks (unexpected).")                              # should never happen
    else:
        print("\nBEST: none (no valid solution)")
        if fail_on_invalid:
            raise SystemExit("[FAIL] no valid configuration")                                                # propagate failure if requested


def run_all(
    cpp_solver: str,
    solver_args: list[str],
    do_visu: bool,
    fail_on_invalid: bool,
    do_improve: bool,
    ns_solver: str,
    ns_args: list[str],
    use_corrmix: bool,
    alpha_step: float,
) -> None:
    for key in DEFAULT_ALIASES:                                                                             # iterate standard instances
        run_one(
            key,
            cpp_solver,
            solver_args,
            do_visu=do_visu,
            fail_on_invalid=fail_on_invalid,
            do_improve=do_improve,
            ns_solver=ns_solver,
            ns_args=ns_args,
            use_corrmix=use_corrmix,
            alpha_step=alpha_step,
        )                                                                                                   # run grid search on each instance


# ============================================================
# CLI
# ============================================================

def main() -> None:
    ap = argparse.ArgumentParser()                                                                          # CLI argument parser
    ap.add_argument("--instance", help="toy | market | family | category | ou chemin vers une instance")    # single instance selector
    ap.add_argument("--all", action="store_true", help="Lance le solveur sur toutes les instances standards")  # run all aliases
    ap.add_argument("--cpp-solver", default=DEFAULT_CPP_SOLVER, help="Chemin vers le binaire C++")          # main solver binary path

    ap.add_argument(
        "--solver-args",
        nargs=argparse.REMAINDER,
        default=[],
        help="Arguments passés au solveur C++ (ex: --circuit-order score --circuit-dir desc ...).",
    )                                                                                                       # passthrough args for C++ solver

    ap.add_argument("--visu", action="store_true", help="Lance aussi la visualisation pour chaque run.")    # enable visualization
    ap.add_argument("--fail-on-invalid", action="store_true", help="Fail fast si une config est invalide.") # stop on first invalid

    ap.add_argument(
        "--use-corrmix",
        action="store_true",
        help="Active le tuning alpha pour --product-score corrmix (alpha balayé pour l'instance).",
    )                                                                                                       # enable alpha sweep for corrmix
    ap.add_argument(
        "--alpha-step",
        type=float,
        default=0.05,
        help="Pas du balayage alpha dans [0,1]. Défaut: 0.05 (utilisé seulement si --use-corrmix).",
    )                                                                                                       # alpha sweep step size

    ap.add_argument(
        "--improve",
        action="store_true",
        help="Après chaque run, lance heuristic_aeration_ns et garde le meilleur.",
    )                                                                                                       # enable improvement phase
    ap.add_argument("--ns-solver", default=DEFAULT_CPP_NS, help="Chemin vers le binaire C++ heuristic_aeration_ns")  # NS binary path
    ap.add_argument(
        "--ns-args",
        nargs=argparse.REMAINDER,
        default=["--iters", "50000", "--k", "2000", "--seed", "0", "--within-aisle", "1", "--safe-interval", "1"],
        help="Arguments passés au solveur NS (ex: --iters 20000 --k 1000 ...).",
    )                                                                                                       # passthrough args for NS solver

    args = ap.parse_args()                                                                                  # parse argv

    if "--solver-args" in args.ns_args:
        cut = args.ns_args.index("--solver-args")                                                           # locate accidental delimiter
        args.ns_args = args.ns_args[:cut]                                                                   # drop trailing tokens to keep NS args clean

    if args.all:
        run_all(
            args.cpp_solver,
            args.solver_args,
            do_visu=args.visu,
            fail_on_invalid=args.fail_on_invalid,
            do_improve=args.improve,
            ns_solver=args.ns_solver,
            ns_args=args.ns_args,
            use_corrmix=args.use_corrmix,
            alpha_step=args.alpha_step,
        )
        return                                                                                              # stop after batch mode

    if not args.instance:
        raise SystemExit(
            "Usage:\n"
            "  python tools/main.py --instance <toy|...|path> [--visu] [--use-corrmix --alpha-step s] [--improve --ns-args ...] --solver-args ...\n"
            "  python tools/main.py --all [--visu] [--use-corrmix --alpha-step s] [--improve --ns-args ...] --solver-args ...\n"
            "Exemples:\n"
            "  python tools/main.py --instance market --use-corrmix --alpha-step 0.05 --improve "
            "--ns-args --iters 20000 --k 1000 --seed 1 --within-aisle 1 --safe-interval 1 "
            "--solver-args --circuit-order score\n"
        )                                                                                                   # keep usage message as-is

    run_one(
        args.instance,
        args.cpp_solver,
        args.solver_args,
        do_visu=args.visu,
        fail_on_invalid=args.fail_on_invalid,
        do_improve=args.improve,
        ns_solver=args.ns_solver,
        ns_args=args.ns_args,
        use_corrmix=args.use_corrmix,
        alpha_step=args.alpha_step,
    )                                                                                                       # run a single instance


if __name__ == "__main__":
    main()                                                                                                  # CLI entrypoint