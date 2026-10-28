#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from warehouse_loader import WarehouseLoader, WarehouseInstance
from parser import resolve_instance_path


# ============================================================
# Export .dat
# ============================================================

def export_metrics(f, tag: str, metrics: dict, expected_size: int | None = None):
    # Write metric blocks in a single, parser-friendly format (scalar vs vector)                         # keep output backward compatible
    for name, values in metrics.items():                                                                 # iterate over provided metrics
        if tag == "SMET":                                                                                # scalar-only namespace
            if not isinstance(values, (int, float)):                                                     # enforce scalar contract
                raise ValueError(f"SMET {name} must be scalar, got {type(values)}")                       # fail fast on malformed metric
            f.write(f"{tag} {name} {values}\n")                                                           # emit scalar line
            continue                                                                                      # skip generic vector handling

        if isinstance(values, (int, float)):                                                              # allow scalar in non-SMET tags
            f.write(f"{tag} {name} {values}\n")                                                           # emit scalar line
            continue                                                                                      # done for this metric

        if expected_size is not None and len(values) != expected_size:                                    # validate vector length if required
            raise ValueError(f"{tag} {name}: expected size {expected_size}, got {len(values)}")           # keep errors explicit and actionable

        vals = " ".join(f"{v:.6g}" for v in values)                                                       # compact numeric formatting
        f.write(f"{tag} {name} {len(values)} {vals}\n")                                                   # emit vector line: size + payload


def read_vector_file(path: Path) -> list[float]:
    """
    Strict vector file format:
      - line 1: integer n
      - next n non-empty lines: exactly one scalar token per line
    Returns a list[float] of length n.
    """
    with path.open("r", encoding="utf-8") as f:                                                           # open as UTF-8 text
        lines = [ln.strip() for ln in f if ln.strip()]                                                    # drop empty lines, keep order

    if not lines:                                                                                         # guard against empty inputs
        raise ValueError(f"Empty file: {path}")                                                           # explicit error for debugging

    n = int(lines[0])                                                                                     # read declared vector size
    vals: list[float] = []                                                                                # accumulate parsed values
    for ln in lines[1:]:                                                                                  # parse payload lines
        parts = ln.split()                                                                                # tokenize (should be single token)
        if len(parts) != 1:                                                                               # enforce strictness
            raise ValueError(f"Not a strict vector file (line has {len(parts)} tokens): {path.name}")     # reject ambiguous formats
        vals.append(float(parts[0]))                                                                      # parse scalar value

    if len(vals) != n:                                                                                    # validate declared size
        raise ValueError(f"{path.name}: expected {n} values, got {len(vals)}")                             # fail fast on mismatch

    return vals                                                                                            # return parsed vector


def load_vector_metrics_filtered(
    enriched_dir: Path,
    expected_size: int,
    include_prefixes: tuple[str, ...] | None = None,
    exclude_prefixes: tuple[str, ...] = (),
) -> dict[str, list[float]]:
    """
    Load strict vector metrics from <enriched_dir>/*.txt with optional stem filtering:
      - include_prefixes: keep only stems starting with one of these prefixes (if provided)
      - exclude_prefixes: drop stems starting with one of these prefixes
    Only vectors with length == expected_size are returned.
    """
    if not enriched_dir.exists():                                                                          # tolerate missing enrichment
        return {}                                                                                           # keep pipeline optional

    metrics: dict[str, list[float]] = {}                                                                    # map metric_name -> vector
    for p in sorted(enriched_dir.glob("*.txt")):                                                            # deterministic file traversal
        stem = p.stem                                                                                       # filename without suffix

        if include_prefixes is not None and not stem.startswith(include_prefixes):                          # apply allow-list filter
            continue                                                                                        # skip unrelated stems
        if exclude_prefixes and stem.startswith(exclude_prefixes):                                          # apply deny-list filter
            continue                                                                                        # skip reserved namespaces

        try:
            vals = read_vector_file(p)                                                                      # parse strict vector format
        except Exception:
            continue                                                                                        # ignore non-vector files (edges/lines/etc)

        if len(vals) != expected_size:                                                                      # ensure semantic alignment (products/racks/...)
            continue                                                                                        # ignore size-mismatched metrics

        metrics[stem] = vals                                                                                # store accepted metric

    return metrics                                                                                           # return collected metrics


def export_instance_dat(instance: WarehouseInstance, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)                                                      # ensure output directory exists

    m = instance.metadata                                                                                   # shorthand for metadata dict

    def w(line: str) -> None:
        f.write(line + "\n")                                                                                # centralize newline handling

    with out_path.open("w", encoding="utf-8") as f:                                                         # write as UTF-8 text
        # ------------------------------------------------------------
        # Core instance payload (must remain stable for the solver)
        # ------------------------------------------------------------
        w(
            "META "
            f"{m['num_racks']} {m['total_slots']} {m['aeration_rate']} "
            f"{m['num_products']} {m['num_circuits']} {m['num_aisles']} {m['num_orders']}"
        )                                                                                                   # emit global instance header

        w("CAP " + " ".join(map(str, instance.rack_capacity)))                                              # rack capacities (per rack)
        w("CIR " + " ".join(map(str, instance.product_circuit)))                                            # product -> circuit assignment

        w(f"AIS {len(instance.aisles_racks)}")                                                              # number of aisles
        for racks in instance.aisles_racks:                                                                 # iterate aisle definitions
            w("A " + str(len(racks)) + " " + " ".join(map(str, racks)))                                     # emit aisle rack list

        n = len(instance.adjacency)                                                                         # adjacency matrix size
        w(f"ADJ {n}")                                                                                       # adjacency header
        for row in instance.adjacency:                                                                      # iterate matrix rows
            w("D " + " ".join(map(str, row)))                                                               # emit row distances/adjacencies

        w(f"ORD {len(instance.orders)}")                                                                    # number of orders
        for products in instance.orders:                                                                    # iterate orders
            w("O " + str(len(products)) + " " + " ".join(map(str, products)))                               # emit order product list

        # ------------------------------------------------------------
        # Optional metrics blocks (backward compatible extensions)
        # ------------------------------------------------------------
        if hasattr(instance, "product_metrics"):                                                            # optional: product-level vectors
            export_metrics(f, "PMET", instance.product_metrics, expected_size=m["num_products"])            # emit PMET block(s)

        if hasattr(instance, "rack_metrics"):                                                               # optional: rack-level vectors
            export_metrics(f, "RMET", instance.rack_metrics, expected_size=m["num_racks"])                  # emit RMET block(s)

        if hasattr(instance, "scalar_metrics"):                                                             # optional: scalar values
            export_metrics(f, "SMET", instance.scalar_metrics)                                              # emit SMET block(s)

        if hasattr(instance, "aisle_metrics"):                                                              # optional: aisle-level vectors
            export_metrics(f, "AMET", instance.aisle_metrics, expected_size=m["num_aisles"])                # emit AMET block(s)

        if hasattr(instance, "circuit_metrics"):                                                            # optional: circuit-level vectors
            export_metrics(f, "CMET", instance.circuit_metrics, expected_size=m["num_circuits"])            # emit CMET block(s)


# ============================================================
# Project export logic (kept aligned with parser.py resolution)
# ============================================================

DEFAULT_ALIASES = ["toy", "market", "family", "category"]                                                   # standard instance keys


def export_one(instance_value: str) -> None:
    """
    Export a single instance (alias or path) to <instance_dir>/instance.dat.
    If present, also appends process_data metrics from <instance_dir>/enriched/.
    """
    inst_dir = resolve_instance_path(instance_value)                                                        # resolve alias/path to instance directory
    inst = WarehouseLoader(str(inst_dir)).load_all()                                                        # load instance payload

    m = inst.metadata                                                                                       # shorthand for metadata dict
    enriched_dir = inst_dir / "enriched"                                                                    # directory containing optional metrics

    reserved = ("circuit_", "aisle_", "rack_")                                                              # reserved namespaces for typed metrics

    inst.product_metrics = load_vector_metrics_filtered(                                                    # PMET: product-sized vectors (generic namespace)
        enriched_dir,                                                                                       # search in <enriched/>
        m["num_products"],                                                                                  # enforce product dimension
        include_prefixes=None,                                                                              # accept any stem...
        exclude_prefixes=reserved,                                                                          # ...except typed namespaces
    )

    inst.rack_metrics = load_vector_metrics_filtered(                                                       # RMET: rack-sized vectors
        enriched_dir,                                                                                       # search in <enriched/>
        m["num_racks"],                                                                                     # enforce rack dimension
        include_prefixes=("rack_",),                                                                        # keep rack_* only
    )

    inst.aisle_metrics = load_vector_metrics_filtered(                                                      # AMET: aisle-sized vectors
        enriched_dir,                                                                                       # search in <enriched/>
        m["num_aisles"],                                                                                    # enforce aisle dimension
        include_prefixes=("aisle_",),                                                                       # keep aisle_* only
    )

    inst.circuit_metrics = load_vector_metrics_filtered(                                                    # CMET: circuit-sized vectors
        enriched_dir,                                                                                       # search in <enriched/>
        m["num_circuits"],                                                                                  # enforce circuit dimension
        include_prefixes=("circuit_",),                                                                     # keep circuit_* only
    )

    inst.scalar_metrics = {}                                                                                # placeholder for future scalar outputs

    out_dat = inst_dir / "instance.dat"                                                                     # target .dat path
    export_instance_dat(inst, out_dat)                                                                      # write instance + optional metrics

    print(
        f"[OK] {inst_dir.name} -> {out_dat} | "
        f"PMET={len(inst.product_metrics)} "
        f"CMET={len(inst.circuit_metrics)} "
        f"AMET={len(inst.aisle_metrics)} "
        f"RMET={len(inst.rack_metrics)}"
    )                                                                                                       # concise export summary


def export_all() -> None:
    """
    Export all standard project instances:
      - toy / market / family / category
    Resolution is intentionally identical to parser.py.
    """
    for key in DEFAULT_ALIASES:                                                                             # iterate standard aliases
        export_one(key)                                                                                     # export each instance


# ============================================================
# CLI
# ============================================================

def main() -> None:
    ap = argparse.ArgumentParser()                                                                          # CLI parser
    ap.add_argument("--instance",
                    help="toy | market | family | category | or path to an instance")                       # single export target
    ap.add_argument("--all", action="store_true",
                    help="Export all standard instances (toy, market, family, category)")                  # batch export mode

    args = ap.parse_args()                                                                                  # parse argv

    if args.all:                                                                                            # batch mode
        export_all()                                                                                        # export all standard instances
        return                                                                                              # stop after batch export

    if not args.instance:                                                                                   # require --instance when not using --all
        raise SystemExit(
            "Usage:\n"
            "  export_instance.py --instance <toy|...|path>\n"
            "  export_instance.py --all"
        )                                                                                                   # keep error message explicit

    export_one(args.instance)                                                                               # export requested instance


if __name__ == "__main__":
    main()                                                                                                  # CLI entrypoint