#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional


# Declare the raw-instance file contract (pre-export, no instance.dat required).
REQUIRED_INSTANCE_FILES = [
    "metadata.txt",
    "rack_capacity.txt",
    "rack_adjacency_matrix.txt",
    "aisle_racks.txt",
    "product_circuit.txt",
    "orders.txt",
]


def has_raw_instance_files(d: Path) -> bool:
    """Check whether a directory contains all required raw instance files.

    Args:
        d: Candidate instance directory.

    Returns:
        True if d is a directory and contains every file in REQUIRED_INSTANCE_FILES.
    """
    return d.is_dir() and all((d / f).is_file() for f in REQUIRED_INSTANCE_FILES)


def has_instance_dat(d: Path) -> bool:
    """Check whether a directory already contains an exported instance.dat.

    Args:
        d: Candidate instance directory.

    Returns:
        True if d/instance.dat exists as a file, else False.
    """
    return (d / "instance.dat").is_file()


def is_bench_candidate_dir(d: Path) -> bool:
    """Identify benchmark sub-instance directories by naming convention.

    Args:
        d: Candidate path.

    Returns:
        True if d is a directory and its name starts with "sub__".
    """
    return d.is_dir() and d.name.startswith("sub__")


def is_instance_dir(d: Path) -> bool:
    """Validate whether a directory can be treated as an instance directory.

    An instance is considered usable if it contains either:
        - an exported instance.dat, or
        - the full set of raw instance files

    Args:
        d: Candidate directory.

    Returns:
        True if d satisfies the instance contract, else False.
    """
    return d.is_dir() and (has_instance_dat(d) or has_raw_instance_files(d))


def iter_bench_instances_from_csv(csv_path: Path) -> List[Path]:
    """Load benchmark instance directories from a catalog CSV.

    The CSV is expected to include a "sub_dir" column with directory paths.

    Args:
        csv_path: Path to the catalog CSV.

    Returns:
        A list of raw Path entries as read from the CSV.
    """
    out: List[Path] = []
    with csv_path.open("r", encoding="utf-8") as f:
        r = csv.DictReader(f)
        if "sub_dir" not in (r.fieldnames or []):
            raise ValueError(f"Invalid CSV (missing 'sub_dir' column): {csv_path}")
        for row in r:
            out.append(Path(row["sub_dir"]))
    return out


def iter_bench_instances_by_scan(bench_root: Path) -> List[Path]:
    """Scan bench_root and return sub__* candidate directories.

    Args:
        bench_root: Root directory containing benchmark sub-instances.

    Returns:
        Candidate directories under bench_root matching the naming convention.
    """
    return sorted([p for p in bench_root.iterdir() if is_bench_candidate_dir(p)])


def run(cmd: List[str], cwd: Optional[Path] = None) -> None:
    """Run a subprocess command and fail fast on errors.

    Args:
        cmd: Command and arguments as a list of strings.
        cwd: Optional working directory for the subprocess.

    Raises:
        subprocess.CalledProcessError: If the command returns a non-zero status.
    """
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=True)


def resolve_csv_path(p: Path, project_root: Path, bench_root: Path) -> Optional[Path]:
    """Resolve a CSV-provided sub_dir into an existing directory.

    Resolution strategy:
        - Absolute path: accept if it exists
        - Relative path: try project_root / p, then bench_root / p, then bench_root / p.name

    Args:
        p: Raw path entry from the CSV.
        project_root: Project root directory.
        bench_root: Root directory containing benchmark sub-instances.

    Returns:
        The resolved path if it exists, else None.
    """
    if p.is_absolute():
        return p.resolve() if p.exists() else None

    # (1) Resolve relative to project root.
    cand1 = (project_root / p).resolve()
    if cand1.exists():
        return cand1

    # (2) Resolve relative to bench_root (e.g., "sub__..." or "bench_milp/sub__...").
    cand2 = (bench_root / p).resolve()
    if cand2.exists():
        return cand2

    # (3) Resolve by directory name only for fully-qualified CSV entries.
    cand3 = (bench_root / p.name).resolve()
    if cand3.exists():
        return cand3

    return None


def main() -> None:
    """Run gurobi_model2 with warm-start baselines over benchmark sub-instances.

    Workflow:
        1) Collect instance directories from a CSV catalog (optional) or by scanning bench_root.
        2) Ensure each instance has an instance.dat (export if enabled and missing).
        3) Require a baseline solution and call gurobi_model2 with --warm-start.
        4) Copy the produced solution under a stable name and summarize outcomes.
    """
    ap = argparse.ArgumentParser(
        description="Run gurobi_model2 with warm-start baseline on benchmark sub-instances."
    )
    ap.add_argument("--bench-root", default="data/bench_milp", help="Directory containing benchmark sub-instances")
    ap.add_argument(
        "--catalog-csv",
        default="data/bench_milp/benchmark_instances.csv",
        help="CSV generated by generate_benchmark.py (optional).",
    )
    ap.add_argument(
        "--ignore-csv",
        action="store_true",
        help="Ignore the CSV even if present, and scan bench-root instead.",
    )
    ap.add_argument(
        "--gurobi-bin",
        default="cpp/build/bin/gurobi_model2",
        help="Path to the C++ gurobi_model2 binary",
    )
    ap.add_argument(
        "--skip-export",
        action="store_true",
        help="Skip tools/export_instance.py (use when instance.dat already exists everywhere)",
    )

    ap.add_argument(
        "--baseline-name",
        required=True,
        help="Baseline filename in <instance>/solutions/ (warm start)",
    )
    ap.add_argument(
        "--fail-on-missing-baseline",
        action="store_true",
        help="Stop if the baseline is missing",
    )

    ap.add_argument("--time-limit", type=float, default=300.0, help="TimeLimit (sec)")
    ap.add_argument("--arc-window", type=int, default=15, help="Arc window (0 = unlimited)")
    ap.add_argument("--max-racks", type=int, default=250, help="Rack safety cap")
    ap.add_argument("--max-orders", type=int, default=500, help="Order safety cap")

    ap.add_argument(
        "--solution-name",
        default="solution_gurobi_ws.txt",
        help="Name of the copied solution in <instance>/solutions/",
    )
    ap.add_argument(
        "--fail-on-nosolution",
        action="store_true",
        help="Stop if the solver does not produce rack_product_assignment.txt",
    )

    args = ap.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    bench_root = (project_root / args.bench_root).resolve()
    catalog_csv = (project_root / args.catalog_csv).resolve()
    gurobi_bin = (project_root / args.gurobi_bin).resolve()

    print(f"[INFO] project_root={project_root}")
    print(f"[INFO] bench_root={bench_root}")
    print(f"[INFO] gurobi_bin={gurobi_bin}")

    if not bench_root.exists():
        raise SystemExit(f"bench-root not found: {bench_root}")
    if not gurobi_bin.is_file():
        raise SystemExit(f"gurobi_model2 binary not found: {gurobi_bin} (missing build?)")

    # Collect instance directories from CSV when available, otherwise scan bench_root.
    instances: List[Path] = []

    use_csv = (catalog_csv.is_file() and not args.ignore_csv)
    if use_csv:
        raw = iter_bench_instances_from_csv(catalog_csv)
        resolved: List[Path] = []
        for p in raw:
            rp = resolve_csv_path(p, project_root, bench_root)
            if rp is not None:
                resolved.append(rp)
        instances = [p for p in resolved if is_instance_dir(p)]

        if not instances:
            print("[WARN] CSV present but produced 0 valid instances -> fallback scan bench_root")
            use_csv = False

    if not use_csv:
        cands = iter_bench_instances_by_scan(bench_root)
        instances = [p.resolve() for p in cands if is_instance_dir(p)]

    if not instances:
        cands = sorted([p for p in bench_root.iterdir() if is_bench_candidate_dir(p)])
        print(f"[DEBUG] found {len(cands)} sub__ candidate dirs in bench_root")
        for p in cands[:10]:
            print(f"[DEBUG] {p.name} instance.dat={has_instance_dat(p)} raw_files={has_raw_instance_files(p)}")
        raise SystemExit(f"No valid instance found (bench_root={bench_root}).")

    print(f"[INFO] found {len(instances)} valid instances")

    def export_cmd(instance_dir: Path) -> List[str]:
        """Build the export_instance.py command for a given instance directory."""
        return [
            sys.executable,
            str(project_root / "tools" / "export_instance.py"),
            "--instance",
            str(instance_dir),
        ]

    def solve_cmd(instance_dat: Path, solution_dir: Path, baseline_path: Path) -> List[str]:
        """Build the gurobi_model2 command line with warm-start parameters."""
        return [
            str(gurobi_bin),
            str(instance_dat),
            str(solution_dir),
            "--time-limit",
            str(args.time_limit),
            "--arc-window",
            str(args.arc_window),
            "--max-racks",
            str(args.max_racks),
            "--max-orders",
            str(args.max_orders),
            "--warm-start",
            str(baseline_path),
        ]

    ok, skipped_baseline, nosol, fail = 0, 0, 0, 0

    for inst_dir in instances:
        print(f"\n=== BENCH INSTANCE: {inst_dir} ===")

        solutions_dir = inst_dir / "solutions"
        solutions_dir.mkdir(parents=True, exist_ok=True)

        instance_dat = inst_dir / "instance.dat"

        # Export instance.dat when enabled and missing.
        if (not args.skip_export) and (not instance_dat.is_file()):
            print("[STEP] export instance.dat")
            run(export_cmd(inst_dir), cwd=project_root)

        if not instance_dat.is_file():
            raise RuntimeError(
                f"instance.dat not found: {instance_dat} (use --skip-export to disable exporting)"
            )

        baseline_path = solutions_dir / args.baseline_name
        if not baseline_path.is_file():
            skipped_baseline += 1
            msg = f"[WARN] missing baseline: {baseline_path}"
            print(msg)
            if args.fail_on_missing_baseline:
                raise RuntimeError(msg)
            continue

        print("[STEP] solve (gurobi_model2 warm-start)")
        try:
            run(solve_cmd(instance_dat, solutions_dir, baseline_path), cwd=project_root)
        except subprocess.CalledProcessError:
            fail += 1
            print("[ERR] solver crashed")
            if args.fail_on_nosolution:
                raise
            continue

        produced = solutions_dir / "rack_product_assignment.txt"
        if not produced.is_file():
            nosol += 1
            msg = f"[WARN] no solution file produced: {produced}"
            print(msg)
            if args.fail_on_nosolution:
                raise RuntimeError(msg)
            continue

        target_solution = solutions_dir / args.solution_name
        shutil.copy2(produced, target_solution)
        print(f"[OK] new solution -> {target_solution}")
        ok += 1

    print("\n[OK] benchmark warm-start solve finished.")
    print(f"     ok={ok} skipped_baseline={skipped_baseline} nosolution={nosol} crashed={fail}")


if __name__ == "__main__":
    main()