#!/usr/bin/env python3
from __future__ import annotations

"""
visualisation_solution.py — Visualize a solution (rack_product_assignment.txt) as an SVG.

- Loads the instance via WarehouseLoader (no duplicated parsing).
- Loads a .txt solution (spec format).
- Generates an SVG layout based on aisle_racks.txt (deterministic per-aisle layout).
- Colors occupied racks by dominant circuit and displays "+k".

NEW (Gurobi debug):
- --allow-special: do not crash if the solution assigns products to cap=0 racks / START / END.
  (Those placements are stored in state.invalid_products and do not affect rack loads.)

Usage (from tools/):
    python visualisation_solution.py --instance toy
    python visualisation_solution.py --instance family --solution solutions/rack_product_assignment.txt
    python visualisation_solution.py --instance market --solution data/.../solutions/rack_product_assignment.txt
"""

import argparse
import hashlib
from pathlib import Path
from typing import Optional

from warehouse_loader import WarehouseLoader
from aisle_layout import resolve_instance_dir
from aisle_svg import generate_svg
from solution_state import SolutionState
from checker import (
    check_all_products_assigned,
    check_rack_capacity,
    check_aeration,
    check_circuit_contiguity,
    calculate_cost,
)
import re

GAP_RE = re.compile(
    r"\bgap\s*(?:=|:)?\s*([0-9]+(?:\.[0-9]+)?)\s*%\b",
    re.IGNORECASE,
)                                                                                                             # capture "gap=12.34%" patterns


def _read_text_maybe(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")                                                # best-effort text read
    except Exception:
        return ""                                                                                              # missing/unreadable log => empty


def _extract_last_gap_percent(log_text: str) -> Optional[float]:
    matches = list(GAP_RE.finditer(log_text))                                                                  # find all "gap=...%" matches
    if matches:
        try:
            return float(matches[-1].group(1))                                                                 # return last reported gap
        except Exception:
            return None                                                                                        # parsing failed

    # Very tolerant fallback (in case '%' is missing or formatting differs)
    # Example: "gap=100" or "Gap 12.34"
    alt = re.findall(r"\bgap\s*(?:=|:)?\s*([0-9]+(?:\.[0-9]+)?)\b", log_text, flags=re.IGNORECASE)            # fallback numeric capture
    if alt:
        try:
            return float(alt[-1])                                                                              # return last captured number
        except Exception:
            return None                                                                                        # parsing failed
    return None                                                                                                # no gap info found


def resolve_gurobi_log_path(solution_path: Path, instance_dir: Path) -> Optional[Path]:
    """
    Search for a Gurobi log located "next to the solution file".
    For compatibility, also try instance_dir/solutions/gurobi.log.
    """
    cand1 = solution_path.parent / "gurobi.log"                                                                # preferred: same folder as solution
    if cand1.exists():
        return cand1                                                                                           # return if present
    cand2 = instance_dir / "solutions" / "gurobi.log"                                                          # compatibility fallback
    if cand2.exists():
        return cand2                                                                                           # return if present
    return None                                                                                                # no log found


def build_optimality_label(
    solution_path: Optional[Path],
    instance_dir: Path,
    *,
    gap_epsilon_percent: float = 0.01,  # 0.01% ~ "almost optimal"
) -> Optional[str]:
    """
    Returns:
      - "OPTI" if gap <= epsilon
      - "gap=XX.XXXX%" otherwise
      - None if no log / no gap info
    """
    if solution_path is None:
        return None                                                                                            # no solution => no label

    log_path = resolve_gurobi_log_path(solution_path, instance_dir)                                            # locate gurobi.log
    print(f"[debug] log_path={log_path}")                                                                      # debug trace (English)
    if log_path is None:
        return None                                                                                            # no log => no gap info

    txt = _read_text_maybe(log_path)                                                                           # read log content
    g = _extract_last_gap_percent(txt)                                                                         # parse last gap value
    print(f"[debug] parsed_gap={g}")                                                                           # debug trace (English)
    if g is None:
        return None                                                                                            # no gap found

    if g <= gap_epsilon_percent:
        return "OPTI"                                                                                          # treat as optimal within tolerance
    if g >= 1.0:
        return f"gap={g:.2f}%"                                                                                 # stable formatting for large gaps
    if g >= 0.1:
        return f"gap={g:.3f}%"                                                                                 # stable formatting for mid gaps
    return f"gap={g:.4f}%"                                                                                     # stable formatting for tiny gaps


def evaluate_solution(instance, rack_of_product):
    """
    Return (is_valid, cost_or_none, msg).
    msg is short and meant to be displayed in the SVG.
    """
    try:
        check_all_products_assigned(instance, rack_of_product)                                                 # validate rack indices + vector length
        check_rack_capacity(instance, rack_of_product)                                                         # validate capacity constraints
        check_aeration(instance, rack_of_product)                                                              # validate aeration constraints
        check_circuit_contiguity(instance, rack_of_product)                                                    # validate circuit interval constraints
        cost = calculate_cost(instance, rack_of_product)                                                       # compute objective value
        return True, cost, f"OK | cost={cost}"                                                                 # short OK label
    except ValueError as e:
        msg = str(e)                                                                                           # capture validation error
        if len(msg) > 90:
            msg = msg[:87] + "..."                                                                             # keep overlay text short
        return False, None, f"INVALID | {msg}"                                                                 # short INVALID label


def _default_solution_path(instance_dir: Path) -> Path:
    return instance_dir / "solutions" / "rack_product_assignment.txt"                                          # conventional default solution


def _as_path_maybe_relative(p: str) -> Path:
    pp = Path(p).expanduser()                                                                                  # expand ~
    return pp.resolve() if pp.is_absolute() else pp                                                            # resolve only if already absolute


def resolve_solution_path(instance_dir: Path, user_arg: str) -> Path:
    """
    Accept:
    - direct path (absolute or relative)
    - filename placed in instance_dir/solutions/
    - name without extension -> tries .txt
    - directory name -> tries <dir>/rack_product_assignment.txt (and same inside instance_dir/solutions/)
    """
    raw = _as_path_maybe_relative(user_arg)                                                                    # parse raw user path

    candidates = [raw]                                                                                        # candidate list (in priority order)

    if not raw.is_absolute():
        candidates.append(instance_dir / "solutions" / raw)                                                   # try relative to instance solutions/

    if raw.suffix == "":
        candidates.append(raw.with_suffix(".txt"))                                                            # try adding .txt
        if not raw.is_absolute():
            candidates.append((instance_dir / "solutions" / raw).with_suffix(".txt"))                         # try <solutions>/<name>.txt

    for base in ([raw, instance_dir / "solutions" / raw] if not raw.is_absolute() else [raw]):
        candidates.append(base / "rack_product_assignment.txt")                                               # try directory -> default filename

    for c in candidates:
        if c.exists():
            return c                                                                                           # return first existing candidate

    tried = ", ".join(str(c) for c in candidates[:8])                                                         # compact error context
    raise FileNotFoundError(f"Solution not found: {user_arg} (tried: {tried} ...)")                           # English error message


def _write_text(path: Path, s: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)                                                             # ensure output directory exists
    path.write_text(s, encoding="utf-8")                                                                       # write UTF-8 text


def _wrap_html(svg: str, title: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <style>
    body {{ margin: 0; font-family: Arial, sans-serif; }}
    .top {{ padding: 10px 12px; background: #f8f9fa; border-bottom: 1px solid #dee2e6; }}
    .wrap {{ padding: 12px; }}
    svg {{ max-width: 100%; height: auto; display: block; }}
  </style>
</head>
<body>
  <div class="top"><b>{title}</b></div>
  <div class="wrap">
    {svg}
  </div>
</body>
</html>
"""                                                                                                           # minimal HTML wrapper for quick sharing


def _short_hash(p: Path) -> str:
    h = hashlib.sha1(str(p).encode("utf-8")).hexdigest()                                                      # stable hash from path string
    return h[:8]                                                                                               # keep short for filenames


def _default_out_path(instance_key: str, solution_path: Optional[Path]) -> Path:
    out_dir = Path(__file__).resolve().parent / "out"                                                         # default tools/out directory
    out_dir.mkdir(parents=True, exist_ok=True)                                                                # ensure it exists

    if solution_path is None:
        stem = "layout_only"                                                                                  # layout-only rendering tag
        return out_dir / f"visu_{instance_key}_{stem}.svg"                                                    # default filename for layout-only

    stem = solution_path.stem                                                                                 # use solution stem
    stem = f"{stem}_{_short_hash(solution_path)}"                                                             # add short hash to avoid collisions
    return out_dir / f"visu_{instance_key}_{stem}.svg"                                                        # default output path


def main() -> None:
    ap = argparse.ArgumentParser()                                                                            # CLI argument parser
    ap.add_argument("--instance", required=True, help="Alias (toy/family/market/category) or path inside data/")  # instance selector
    ap.add_argument("--solution", default="", help="Path to rack_product_assignment.txt (default: instance_dir/solutions/...)")  # solution selector
    ap.add_argument("--allow-special", action="store_true",
                    help="Debug: do not crash if solution assigns to cap=0 racks / START / END (raw visualization).")  # tolerate special racks
    ap.add_argument("--cell-px", type=int, default=18)                                                        # cell size
    ap.add_argument("--col-gap-px", type=int, default=20)                                                     # column gap (corridor)
    ap.add_argument("--top-pad-px", type=int, default=6)                                                      # outer padding
    ap.add_argument("--no-labels", action="store_true")                                                       # disable rack labels
    ap.add_argument("--no-directions", action="store_true")                                                   # disable direction arrows
    ap.add_argument("--out", default="", help="Output path (.svg or .html). Default: tools/out/visu_<instance>_<solutionstem>.svg")  # output path
    ap.add_argument("--html", action="store_true", help="Also output an .html wrapper next to the svg (or if --out ends with .html).")  # add HTML wrapper
    args = ap.parse_args()                                                                                    # parse argv

    instance_dir = resolve_instance_dir(args.instance)                                                        # resolve alias/path to directory
    loader = WarehouseLoader(str(instance_dir))                                                               # build loader
    inst = loader.load_all()                                                                                  # load instance data

    solution_path: Optional[Path] = None                                                                      # optional solution path
    if args.solution:
        solution_path = resolve_solution_path(instance_dir, args.solution)                                    # resolve user-provided solution path
    else:
        p = _default_solution_path(instance_dir)                                                              # try default solution file
        if p.exists():
            solution_path = p                                                                                 # use default if present

    if solution_path is not None:
        state = SolutionState.from_solution_file(inst, solution_path, allow_special=args.allow_special)       # load solution state
        sol_label = f"solution={solution_path}"                                                               # display label
    else:
        state = SolutionState.empty_from_instance(inst)                                                       # layout-only rendering
        sol_label = "solution=<none> (empty state)"                                                           # display label

    status_text = None                                                                                        # optional status overlay
    if solution_path is not None:
        rack_of_product = state.to_solution_vector(default_rack=0)                                            # convert to solution vector
        _, _, status_text = evaluate_solution(inst, rack_of_product)                                          # run validity checks + cost

        opti_label = build_optimality_label(
            solution_path,
            instance_dir,
            gap_epsilon_percent=0.01,                                                                         # tweak threshold here (e.g., 0.1 for 0.1%)
        )
        if opti_label:
            status_text = f"{status_text} | {opti_label}"                                                     # append optimality label

    instance_key = "toy" if args.instance == "toy" else args.instance                                         # keep legacy "toy" key

    svg = generate_svg(
        inst,
        instance_dir=instance_dir,
        instance_key=instance_key,
        cell_px=args.cell_px,
        col_gap_px=args.col_gap_px,
        top_pad_px=args.top_pad_px,
        show_labels=not args.no_labels,
        draw_directions=not args.no_directions,
        state=state,
        status_text=status_text,
    )                                                                                                         # generate SVG content

    out_path = Path(args.out).expanduser() if args.out else _default_out_path(instance_key, solution_path)    # determine output path
    out_path.parent.mkdir(parents=True, exist_ok=True)                                                        # ensure output directory exists

    if out_path.suffix.lower() == ".html":
        html = _wrap_html(svg, title=f"Visu {args.instance} — {sol_label}")                                   # wrap SVG in HTML
        _write_text(out_path, html)                                                                           # write HTML output
        print(f"[ok] wrote: {out_path}")                                                                      # success log
        return                                                                                                # stop here (HTML requested)

    _write_text(out_path, svg)                                                                                # write SVG output
    print(f"[ok] wrote: {out_path}")                                                                          # success log

    if args.html:
        html_path = out_path.with_suffix(".html")                                                             # sibling .html output path
        html = _wrap_html(svg, title=f"Visu {args.instance} — {sol_label}")                                   # wrap SVG in HTML
        _write_text(html_path, html)                                                                          # write HTML wrapper
        print(f"[ok] wrote: {html_path}")                                                                     # success log


if __name__ == "__main__":
    main()                                                                                                    # CLI entrypoint