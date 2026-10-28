#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import subprocess
import sys
from pathlib import Path
from typing import List, Optional


# Define the minimal contract for a benchmark instance directory.
REQUIRED_INSTANCE_FILES = [
    "metadata.txt",
    "rack_capacity.txt",
    "rack_adjacency_matrix.txt",
    "aisle_racks.txt",
    "product_circuit.txt",
    "orders.txt",
]


def missing_instance_files(d: Path) -> List[str]:
    """List required instance files missing from a directory.

    Args:
        d: Candidate instance directory.

    Returns:
        Filenames from REQUIRED_INSTANCE_FILES that are not present as regular files in d.
    """
    return [f for f in REQUIRED_INSTANCE_FILES if not (d / f).is_file()]


def is_instance_dir(d: Path, verbose: bool = False) -> bool:
    """Validate whether a directory satisfies the instance file contract.

    Args:
        d: Candidate directory.
        verbose: Print skip reasons when the directory is invalid.

    Returns:
        True if d is a directory and all required files are present, else False.
    """
    if not d.is_dir():
        return False
    miss = missing_instance_files(d)
    if miss:
        if verbose:
            print(f"[SKIP] {d} missing: {miss}")
        return False
    return True


def resolve_subdir(raw: str, bench_root: Path, project_root: Path) -> List[Path]:
    """Build candidate paths for a CSV-provided sub-instance directory reference.

    The CSV may contain:
        - an absolute path
        - a path relative to bench_root (e.g., "sub__...")
        - a path relative to project_root (e.g., "data/bench_milp/sub__...")

    Args:
        raw: Raw CSV value from the "sub_dir" column.
        bench_root: Root directory containing benchmark sub-instances.
        project_root: Project root directory.

    Returns:
        A de-duplicated list of candidate paths to try, in priority order.
    """
    raw = raw.strip()
    if not raw:
        return []

    p = Path(raw)

    cands: List[Path] = []

    # (1) Resolve absolute paths directly.
    if p.is_absolute():
        cands.append(p)
        return cands

    # (2) Resolve relative to bench_root.
    cands.append((bench_root / p).resolve())

    # (3) Resolve relative to project_root.
    cands.append((project_root / p).resolve())

    # (4) Resolve by name under bench_root for loose CSV entries.
    cands.append((bench_root / p.name).resolve())

    # De-duplicate candidates while preserving order.
    uniq: List[Path] = []
    seen = set()
    for c in cands:
        s = str(c)
        if s not in seen:
            uniq.append(c)
            seen.add(s)
    return uniq


def iter_bench_instances_from_csv(
    csv_path: Path, bench_root: Path, project_root: Path, verbose: bool
) -> List[Path]:
    """Collect valid benchmark instance directories using a CSV catalog.

    The CSV is expected to contain a "sub_dir" column pointing to sub-instance
    directories. Each entry is resolved through multiple strategies and kept
    only if it satisfies the required instance file contract.

    Args:
        csv_path: Path to the catalog CSV.
        bench_root: Root directory containing benchmark sub-instances.
        project_root: Project root directory for alternative resolution.
        verbose: Print skip reasons for invalid catalog rows.

    Returns:
        A de-duplicated list of valid instance directories.
    """
    out: List[Path] = []
    with csv_path.open("r", encoding="utf-8") as f:
        r = csv.DictReader(f)
        if "sub_dir" not in (r.fieldnames or []):
            raise ValueError(f"Invalid CSV (missing 'sub_dir' column): {csv_path}")

        for row in r:
            raw = (row.get("sub_dir") or "").strip()
            if not raw:
                continue

            cands = resolve_subdir(raw, bench_root, project_root)
            chosen: Optional[Path] = None
            for c in cands:
                if is_instance_dir(c, verbose=False):
                    chosen = c
                    break

            if chosen is None:
                if verbose:
                    tried = cands[:3]
                    suffix = "..." if len(cands) > 3 else ""
                    print(f"[CSV-SKIP] sub_dir='{raw}' -> no valid candidate. tried={tried}{suffix}")
                continue

            out.append(chosen)

    # De-duplicate across the full output while preserving order.
    uniq: List[Path] = []
    seen = set()
    for p in out:
        s = str(p.resolve())
        if s not in seen:
            uniq.append(p.resolve())
            seen.add(s)
    return uniq


def iter_bench_instances_by_scan(bench_root: Path, verbose: bool) -> List[Path]:
    """Scan bench_root and keep valid sub__* instance directories.

    Args:
        bench_root: Root directory containing benchmark sub-instances.
        verbose: Print discovery diagnostics and validation status.

    Returns:
        A list of valid instance directories found under bench_root.
    """
    # Keep only sub__* directories (benchmark sub-instances).
    cands = sorted([p for p in bench_root.iterdir() if p.is_dir() and p.name.startswith("sub__")])

    if verbose:
        print(f"[DEBUG] found {len(cands)} candidate dirs in bench_root")
        shown = 0
        for p in cands:
            if shown >= 12:
                break
            miss = missing_instance_files(p)
            print(f"[DEBUG] {p.name} ok={len(miss)==0} missing_txt={miss}")
            shown += 1

    return [p for p in cands if is_instance_dir(p, verbose=verbose)]


def run(cmd: List[str], cwd: Optional[Path] = None, dry_run: bool = False) -> None:
    """Execute a subprocess command with optional dry-run logging.

    Args:
        cmd: Command and arguments as a list of strings.
        cwd: Optional working directory for the subprocess.
        dry_run: If True, only print the command without executing it.

    Raises:
        subprocess.CalledProcessError: If the command fails and dry_run is False.
    """
    if dry_run:
        print("DRY:", " ".join(map(str, cmd)))
        return
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=True)


def main() -> None:
    """Run the C++ greedy heuristic on all benchmark sub-instances.

    Workflow:
        1) Resolve project paths and validate inputs.
        2) Load instance directories from the catalog CSV if available, else scan bench_root.
        3) For each instance, require an existing instance.dat and run the solver.
        4) Validate that a solution file was produced and summarize results.
    """
    ap = argparse.ArgumentParser(description="Run heuristic_greedy_circuit_smart on all benchmark sub-instances.")
    ap.add_argument("--bench-root", default="data/bench_milp", help="Directory containing benchmark sub-instances")
    ap.add_argument(
        "--catalog-csv",
        default="data/bench_milp/benchmark_instances.csv",
        help="CSV generated by generate_benchmark.py (optional). If missing, scan bench-root.",
    )
    ap.add_argument(
        "--cpp-solver",
        default="cpp/build/bin/heuristic_greedy_circuit_smart",
        help="Path to the compiled C++ binary (after build)",
    )
    ap.add_argument("--product-dir", default="asc", choices=["asc", "desc"])
    ap.add_argument("--circuit-dir", default="asc", choices=["asc", "desc"])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    bench_root = (project_root / args.bench_root).resolve()
    catalog_csv = (project_root / args.catalog_csv).resolve()
    cpp_solver = (project_root / args.cpp_solver).resolve()

    print(f"[INFO] project_root={project_root}")
    print(f"[INFO] bench_root={bench_root}")
    print(f"[INFO] cpp_solver={cpp_solver}")

    if not bench_root.exists():
        raise SystemExit(f"ERROR: bench-root not found: {bench_root}")
    if not cpp_solver.is_file():
        raise SystemExit(f"ERROR: heuristic binary not found: {cpp_solver} (missing build?)")

    instances: List[Path] = []

    # Prefer the catalog CSV when present; fall back to a directory scan otherwise.
    if catalog_csv.is_file():
        print(f"[INFO] using catalog_csv={catalog_csv}")
        instances = iter_bench_instances_from_csv(catalog_csv, bench_root, project_root, verbose=args.verbose)
        print(f"[INFO] CSV yielded {len(instances)} valid instances")
        if not instances:
            print("[WARN] CSV yielded 0 instances -> fallback to scanning bench_root")
            instances = iter_bench_instances_by_scan(bench_root, verbose=args.verbose)
    else:
        print("[INFO] catalog_csv missing -> scanning bench_root")
        instances = iter_bench_instances_by_scan(bench_root, verbose=args.verbose)

    print(f"[INFO] found {len(instances)} valid instances")
    if not instances:
        raise SystemExit(f"ERROR: no valid instance found (bench_root={bench_root}).")

    # Build the solver command for a given instance.
    def heuristic_cmd(instance_dat: Path, solution_dir: Path) -> List[str]:
        return [
            str(cpp_solver),
            "--product-dir",
            args.product_dir,
            "--circuit-dir",
            args.circuit_dir,
            str(instance_dat),
            str(solution_dir),
        ]

    ok, fail = 0, 0

    for inst_dir in instances:
        print(f"\n=== BENCH INSTANCE: {inst_dir.name} ===")

        instance_dat = inst_dir / "instance.dat"
        if not instance_dat.is_file():
            # Enforce explicit export: benchmark directories may not ship instance.dat by default.
            fail += 1
            print(f"[ERR] instance.dat missing: {instance_dat} (export it first, or add export_instance.py here)")
            continue

        solutions_dir = inst_dir / "solutions"
        solutions_dir.mkdir(parents=True, exist_ok=True)

        print("[STEP] run heuristic")
        try:
            run(heuristic_cmd(instance_dat, solutions_dir), cwd=project_root, dry_run=args.dry_run)
        except subprocess.CalledProcessError:
            fail += 1
            print("[ERR] heuristic crashed")
            continue

        if args.dry_run:
            ok += 1
            continue

        produced = solutions_dir / "rack_product_assignment.txt"
        if not produced.is_file():
            fail += 1
            print(f"[ERR] no solution file produced: {produced}")
            continue

        print(f"[OK] solution -> {produced}")
        ok += 1

    print("\n[OK] heuristic bench finished.")
    print(f"     ok={ok} failed={fail}")


if __name__ == "__main__":
    main()