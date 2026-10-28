# python Analyses/plot_solution_values.py

import sys
import os

# =========================
# Add project root to sys.path
# =========================
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(PROJECT_ROOT)

import matplotlib.pyplot as plt
import numpy as np

from tools.warehouse_loader import WarehouseLoader
from tools.checker import checker


# =========================
# Configuration
# =========================

# Map display names to expected solution filenames in each instance "solutions/" directory.
ALGORITHMS = {
    "Greedy": "rack_product_assignment_greedy_circuit_idiot.txt",
    "Greedy_Smart": "rack_product_assignment_greedy_circuit_smart__BEST.txt",
    "Tabu": "rack_product_assignment_tabu_BEST.txt",
}

# Resolve the BIG instances to compare (relative to the project root).
INSTANCES = {
    "category": "data/warehouse_big_/warehouse_big_category",
    "family": "data/warehouse_big_/warehouse_big_family",
    "market": "data/warehouse_big_/warehouse_big_market",
}

# Use consistent colors for cost plots (one color per instance).
INSTANCE_COLORS = {
    "category": "#6DA87D",
    "family": "#f5a4de",
    "market": "#92bcfc",
}

# Use a dedicated palette for runtime plots (one color per instance).
TIME_COLORS = {
    "category": "#ABE8A6",
    "family": "#EDC9FE",
    "market": "#8ad4e7",
}

# Store measured execution times (seconds) for BIG instances (0.0 means "unknown / not recorded").
EXECUTION_TIMES_BIG = {
    "Greedy": {
        "category": 0.445954,
        "family": 0.341695,
        "market": 0.5513
    },
    "Greedy_Smart": {
        "category": 32.70,
        "family": 59.71,
        "market": 28.92
    },
    "Tabu": {
        "category": 0.0,
        "family": 0.0,
        "market": 0.0
    },
}

# Write analysis artifacts (stats and plots) into this folder.
OUTPUT_DIR = "Analyses"


# =========================
# Results collection
# =========================

def collect_results():
    """
    Collect picking costs for each (algorithm, instance) pair when the corresponding solution file exists.

    The function:
    - loads each instance once via WarehouseLoader,
    - checks each algorithm's solution file under "<instance>/solutions/",
    - computes the total picking cost using the central checker helper,
    - returns a sparse dict containing only valid, available costs.

    Returns:
        dict[str, dict[str, float]]: Nested mapping algo -> instance -> cost (only for valid solutions found).
    """
    results = {algo_name: {} for algo_name in ALGORITHMS.keys()}

    for instance_name, instance_path in INSTANCES.items():

        if not os.path.isdir(instance_path):
            continue

        print(f"\n=== Instance : {instance_name} ===")

        loader = WarehouseLoader(instance_path)
        instance_obj = loader.load_all()

        solutions_dir = os.path.join(instance_path, "solutions")

        if not os.path.isdir(solutions_dir):
            continue

        for algo_name, filename in ALGORITHMS.items():

            solution_path = os.path.join(solutions_dir, filename)

            if not os.path.isfile(solution_path):
                continue

            cost = checker(instance_obj, solutions_dir, filename)

            if cost is not None:
                print(f"{algo_name} -> {cost}")
                results[algo_name][instance_name] = cost
            else:
                print(f"{algo_name} -> invalide")

    return results


# =========================
# Per-instance stats export
# =========================

def save_stats_per_instance(results):
    """
    Export per-instance summary statistics to text files.

    For each instance:
    - keep only algorithms with available valid costs,
    - compute best and mean cost across valid algorithms,
    - compute relative gaps to best and mean (percent),
    - compute rank (1 = best),
    - write a compact stats file under OUTPUT_DIR.

    Args:
        results (dict[str, dict[str, float]]): Nested mapping algo -> instance -> cost.
    """
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    instance_names = list(INSTANCES.keys())
    algo_names = list(ALGORITHMS.keys())

    for instance in instance_names:
        # Filter valid methods for this instance.
        valid_results = {algo: results[algo][instance] for algo in algo_names if instance in results[algo]}
        if not valid_results:
            print(f"Aucune donnée valide pour l'instance {instance}, fichier de stats non créé.")
            continue

        best_value = min(valid_results.values())
        mean_value = np.mean(list(valid_results.values()))
        # Rank methods (1 = best).
        sorted_algos = sorted(valid_results.items(), key=lambda x: x[1])
        ranks = {algo: rank + 1 for rank, (algo, _) in enumerate(sorted_algos)}

        lines = [f"Stats instance {instance}\n"]
        lines.append("Algo | Cout | Ecart relatif meilleur (%) | Ecart relatif moyenne (%) | Rang")
        lines.append("-" * 70)
        for algo, val in valid_results.items():
            rel_best = (val - best_value) / best_value * 100
            rel_mean = (val - mean_value) / mean_value * 100
            rank = ranks[algo]
            lines.append(f"{algo} | {val} | {rel_best:.2f} | {rel_mean:.2f} | {rank}")

        output_file = os.path.join(OUTPUT_DIR, f"stat_{instance}.txt")
        with open(output_file, "w") as f:
            f.write("\n".join(lines))

        print(f"Stats sauvegardées dans {output_file}")


# =========================
# Plotting
# =========================

def plot_results(results):
    """
    Plot total picking cost comparisons across algorithms for the configured BIG instances.

    The plot is a grouped bar chart:
    - x-axis: algorithms,
    - grouped bars: instances,
    - bars displayed only when a valid cost exists for that (algo, instance) pair.

    Args:
        results (dict[str, dict[str, float]]): Nested mapping algo -> instance -> cost.
    """
    algo_names = list(ALGORITHMS.keys())
    instance_names = list(INSTANCES.keys())

    x = np.arange(len(algo_names))
    width = 0.22  # Slightly increased since we plot 3 bars per group.

    fig, ax = plt.subplots(figsize=(10, 6))

    offsets = np.linspace(-width, width, len(instance_names))

    has_data = False

    for idx, instance in enumerate(instance_names):

        values = []
        positions = []

        for i, algo in enumerate(algo_names):
            if instance in results[algo]:
                values.append(results[algo][instance])
                positions.append(x[i] + offsets[idx])

        if values:
            has_data = True

            color = INSTANCE_COLORS.get(instance, None)

            ax.bar(
                positions,
                values,
                width,
                label=instance,
                color=color,
                edgecolor="black"
            )

    if not has_data:
        print("Aucune donnée valide trouvée.")
        return

    ax.set_xlabel("Algorithmes")
    ax.set_ylabel("Coût total de picking")
    # ax.set_title("Comparaison des solutions (instances BIG uniquement)")
    ax.set_xticks(x)
    ax.set_xticklabels(algo_names)

    ax.legend(title="Instance")
    ax.grid(axis="y", linestyle="--", alpha=0.5)

    plt.tight_layout()
    plt.show()


def plot_times_big(times_dict):
    """
    Generate and save a grouped bar chart comparing execution times across BIG instances.

    Behavior:
    - skip missing entries and non-positive times (t <= 0),
    - use TIME_COLORS for instance-level coloring,
    - save the figure under OUTPUT_DIR and close it (no interactive display).

    Args:
        times_dict (dict[str, dict[str, float]]): Nested mapping algo -> instance -> time in seconds.
    """
    algo_names = list(ALGORITHMS.keys())
    instance_names = list(INSTANCES.keys())

    x = np.arange(len(algo_names))
    width = 0.22
    offsets = np.linspace(-width, width, len(instance_names))

    fig, ax = plt.subplots(figsize=(10, 6))
    has_data = False

    for idx, instance in enumerate(instance_names):
        values = []
        positions = []

        for i, algo in enumerate(algo_names):
            if algo in times_dict and instance in times_dict[algo]:
                t_val = times_dict[algo][instance]
                if t_val > 0:
                    values.append(t_val)
                    positions.append(x[i] + offsets[idx])

        if values:
            has_data = True
            # Use TIME_COLORS instead of INSTANCE_COLORS for runtime plots.
            color = TIME_COLORS.get(instance, "#555555")
            ax.bar(positions, values, width, label=instance, color=color, edgecolor="black")

    if not has_data:
        print("⚠️ Aucune donnée de temps valide pour le graphique.")
        return

    ax.set_xlabel("Algorithmes")
    ax.set_ylabel("Temps d'exécution (secondes)")
    # ax.set_title("Comparaison des temps d'exécution (instances BIG)")
    ax.set_xticks(x)
    ax.set_xticklabels(algo_names)

    # Render a subtle grid for runtime readability.
    ax.grid(axis="y", linestyle=":", alpha=0.7)
    ax.legend(title="Instance")

    plt.tight_layout()

    output_path = os.path.join(OUTPUT_DIR, "times_comparison_big.png")
    plt.savefig(output_path)
    plt.close()
    print(f"⏱️ Graphique des temps enregistré avec les nouvelles couleurs : {output_path}")


# =========================
# Main
# =========================

if __name__ == "__main__":
    # Collect cost results (do not alter any existing stats logic).
    results = collect_results()

    if results:
        # 1) Render the original cost comparison chart.
        plot_results(results)

        # 2) Render the runtime comparison chart (saved separately).
        plot_times_big(EXECUTION_TIMES_BIG)