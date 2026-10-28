# process_data/main_data.py
from __future__ import annotations

import argparse
import importlib
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List

from process_data.io_utils import safe_write, write_edges, write_vector
from process_data.paths import ensure_tools_on_path, resolve_instance_dir
from process_data.aisle_entry_exit import build_aisle_entry_exit_metrics

# --- Bootstrap tools imports (WarehouseLoader lives in tools/) ---
ensure_tools_on_path()
from warehouse_loader import WarehouseLoader  # noqa: E402


MethodFn = Callable[..., List[Any]]

DEFAULT_ALIASES = ["toy", "market", "family", "category"]


def _try_register(registry: Dict[str, MethodFn], name: str, module_path: str, func: str = "compute") -> None:
    """
    Register a preprocessing method if its module is importable.

    This keeps the CLI robust when some optional preprocessing modules are absent
    or temporarily broken: missing methods are simply not registered.

    Args:
        registry: Mutable mapping updated in-place with the resolved callable.
        name: Public method name used in CLI and output logs.
        module_path: Python import path of the module providing the method.
        func: Attribute name of the compute entrypoint in the module.
    """
    try:
        mod = importlib.import_module(module_path)
        fn = getattr(mod, func)
        registry[name] = fn
    except Exception:
        # Skip unavailable/broken modules to keep the registry minimal and usable.
        return


def get_registry() -> Dict[str, MethodFn]:
    """
    Build the preprocessing registry available to the CLI.

    Returns:
        A mapping method_name -> compute_fn for all importable preprocessing modules.
    """
    registry: Dict[str, MethodFn] = {}
    _try_register(registry, "popularity", "process_data.popularity")
    _try_register(registry, "order_stats", "process_data.order_stats")
    _try_register(registry, "neighbors", "process_data.neighbors")
    _try_register(registry, "circuit_stats", "process_data.circuit_stats")
    _try_register(registry, "profiles", "process_data.profiles")
    _try_register(registry, "actionable", "process_data.actionable")
    # Do not depend on an "aisle_stats" module: AMET is computed directly from geometry/distances.
    return registry


def _write_lines(path: Path, lines: Iterable[str]) -> None:
    """
    Write a list of strings to disk using the project "lines" artifact format.

    Format:
    - first line: number of lines,
    - one line per row thereafter.

    Args:
        path: Destination file path.
        lines: Iterable of lines to serialize.
    """
    lines_list = [str(x).rstrip() for x in lines]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        f.write(f"{len(lines_list)}\n")
        for line in lines_list:
            f.write(line + "\n")


def write_artifact(base_dir: Path, artifact: Any, overwrite: bool) -> str:
    """
    Write a single artifact to disk following the project artifact contract.

    Contract:
      - artifact.name: filename
      - artifact.kind: "vector" | "edges" | "lines"
      - artifact.data: payload

    Args:
        base_dir: Output directory where the artifact file is written.
        artifact: Object carrying (name, kind, data) attributes.
        overwrite: Whether to overwrite an existing file.

    Returns:
        A human-readable status string suitable for CLI logs.
    """
    out_path = base_dir / artifact.name

    if artifact.kind == "vector":
        wrote = safe_write(out_path, overwrite, write_vector, artifact.data)
        return f"{'wrote' if wrote else 'skip '} {out_path}"

    if artifact.kind == "edges":
        if out_path.exists() and not overwrite:
            return f"skip  {out_path}"
        m = write_edges(out_path, artifact.data)  # expects iterable[(i,j,w)]
        return f"wrote {out_path} (m={m})"

    if artifact.kind == "lines":
        if out_path.exists() and not overwrite:
            return f"skip  {out_path}"
        _write_lines(out_path, artifact.data)  # expects iterable[str]
        return f"wrote {out_path} (lines={len(list(artifact.data)) if hasattr(artifact.data, '__len__') else 'n/a'})"

    raise ValueError(f"Unknown artifact kind: {artifact.kind}")


def _call_method(compute_fn: MethodFn, instance: Any, args: argparse.Namespace) -> List[Any]:
    """
    Call a preprocessing method with a flexible signature.

    Supported signatures:
      - compute(instance)
      - compute(instance, args)

    Args:
        compute_fn: Target method to execute.
        instance: Loaded instance object.
        args: Parsed CLI arguments forwarded when supported.

    Returns:
        The list of artifacts produced by the method.
    """
    try:
        return compute_fn(instance, args)
    except TypeError:
        return compute_fn(instance)


def _run_one_instance(
    registry: Dict[str, MethodFn],
    instance_value: str,
    out: str,
    methods: List[str],
    overwrite: bool,
    args: argparse.Namespace,
) -> None:
    """
    Run selected preprocessing methods on a single instance and write artifacts.

    Steps:
    - resolve the instance directory from an alias or a path,
    - load the instance once,
    - optionally compute AMET aisle entry/exit metrics,
    - run each requested method and write its artifacts.

    Args:
        registry: Mapping method_name -> compute_fn.
        instance_value: Alias (toy/market/...) or path to an instance directory.
        out: Output subdirectory name inside the instance directory.
        methods: List of method names to execute.
        overwrite: Whether to overwrite existing artifact files.
        args: Parsed CLI arguments passed to methods when supported.
    """
    instance_dir = resolve_instance_dir(instance_value)
    out_dir = instance_dir / out if out else instance_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    instance = WarehouseLoader(instance_dir).load_all()
    print(f"\n[OK] instance={instance_dir} methods={methods} out={out_dir}")

    # --- Geometric AMET (entry/exit) generation when requested ---
    # Goal: provide a stable aisle placement signal for greedy heuristics.
    if args.add_aisle_entry_exit:
        build_aisle_entry_exit_metrics(instance_dir=Path(instance_dir), out_dir=out_dir)
        print(" - wrote", out_dir / "aisle_entry_exit_score.txt")
        print(" - wrote", out_dir / "aisle_entry_exit_rank.txt")

    for name in methods:
        compute_fn = registry.get(name)
        if compute_fn is None:
            raise KeyError(f"Méthode '{name}' introuvable. Méthodes dispo: {sorted(registry.keys())}")

        artifacts = _call_method(compute_fn, instance, args)

        print(f"\n== {name} ==")
        for art in artifacts:
            print(" -", write_artifact(out_dir, art, overwrite))


def main() -> None:
    """
    CLI entrypoint to preprocess POIP instances into enriched data files.

    Modes:
    - --instance: run on a single alias/path,
    - --all: run on all standard aliases defined in DEFAULT_ALIASES.

    The command selects preprocessing methods, executes them, and writes their
    artifacts under the configured output directory.
    """
    registry = get_registry()
    if not registry:
        raise RuntimeError("Aucune méthode disponible dans process_data. Vérifie tes fichiers/modules.")

    ap = argparse.ArgumentParser(description="Preprocess POIP instances into enriched data files.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--instance", help="Alias (toy/market/...) or path to instance dir.")
    g.add_argument("--all", action="store_true", help="Run on all standard instances (toy/market/family/category).")

    ap.add_argument("--out", default="enriched", help="Output subdir inside instance (default: 'enriched').")
    ap.add_argument("--methods", nargs="+", default=["popularity"], choices=sorted(registry.keys()))
    ap.add_argument("--overwrite", action="store_true", help="Overwrite existing files.")

    # Add aisle metric based on geometry (entry/exit).
    ap.add_argument(
        "--add-aisle-entry-exit",
        action="store_true",
        help="Compute aisle_entry_exit_score/rank (AMET) from distances start->aisle->end.",
    )

    # Common knobs (used by neighbors/profiles/actionable; harmless otherwise).
    ap.add_argument("--k", type=int, default=10, help="Top-K voisins par produit (neighbors/actionable).")
    ap.add_argument("--alpha", type=float, default=0.7, help="Poids popularity dans actionable score.")
    ap.add_argument("--affinity", choices=["jaccard", "lift"], default="jaccard", help="Score d'affinité produit-produit.")
    ap.add_argument("--top-patterns", type=int, default=50, help="Nb de motifs circuits conservés (profiles).")
    ap.add_argument("--max-order-size", type=int, default=200, help="Skip commande si trop grande (pairs explosion).")
    ap.add_argument("--min-cooc", type=int, default=1, help="Filtre cooccurrence minimale (neighbors).")

    args = ap.parse_args()

    targets = DEFAULT_ALIASES if args.all else [args.instance]

    for inst_value in targets:
        _run_one_instance(
            registry=registry,
            instance_value=inst_value,
            out=args.out,
            methods=args.methods,
            overwrite=args.overwrite,
            args=args,
        )


if __name__ == "__main__":
    main()