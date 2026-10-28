# python Analysis/plot_solution_values_toy.py

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

# Map display names to expected solution filenames in the instance "solutions/" directory.
ALGORITHMS = {
    "Greedy": "rack_product_assignment_greedy_circuit_idiot.txt",
    "Greedy_Smart": "rack_product_assignment_greedy_circuit_smart__BEST.txt",
    "Tabu": "rack_product_assignment_tabu_BEST.txt",
    "Gurobi": "rack_product_assignment_gurobi.txt",
}

# Store measured execution times (seconds). Missing entries are intentionally omitted.
EXECUTION_TIMES = {
    "Greedy": 0.008438,
    "Greedy_Smart": 1.70,
    "Tabu": 2.11611,
    #"Gurobi": 0.0  # Default value omitted because it is not provided.
}

# Target instance metadata.
INSTANCE_NAME = "toy"
INSTANCE_PATH = "data/warehouse_toy"
OUTPUT_STATS_FILE = "analysis/stat_toy.txt"

# Use a single color for cost bars to keep the toy chart compact and readable.
BAR_COLOR = "#c2a5fc"


# =========================
# Results collection
# =========================

def collect_results():
    """
    Collect picking costs for each algorithm on the TOY instance.

    The function:
    - loads the TOY instance once via WarehouseLoader,
    - scans "<instance>/solutions/" for the expected solution filenames,
    - computes the total picking cost using the central checker helper,
    - returns a dict containing only valid, available costs.

    Returns:
        dict[str, float]: Mapping algo -> cost for valid solutions found.
    """
    results = {}

    if not os.path.isdir(INSTANCE_PATH):
        print("Instance toy introuvable.")
        return results

    print(f"\n=== Instance : {INSTANCE_NAME} ===")

    loader = WarehouseLoader(INSTANCE_PATH)
    instance_obj = loader.load_all()

    solutions_dir = os.path.join(INSTANCE_PATH, "solutions")

    if not os.path.isdir(solutions_dir):
        print("Dossier solutions introuvable.")
        return results

    for algo_name, filename in ALGORITHMS.items():

        solution_path = os.path.join(solutions_dir, filename)

        if not os.path.isfile(solution_path):
            print(f"{algo_name} -> fichier manquant")
            continue

        cost = checker(instance_obj, solutions_dir, filename)

        if cost is not None:
            print(f"{algo_name} -> {cost}")
            results[algo_name] = cost
        else:
            print(f"{algo_name} -> invalide")

    return results


# =========================
# Stats export
# =========================

def save_stats(results, output_file):
    """
    Export a compact stats summary for the TOY instance to a text file.

    The report includes:
    - absolute cost,
    - relative gap to best (percent),
    - relative gap to mean (percent),
    - rank (1 = best).

    Args:
        results (dict[str, float]): Mapping algo -> cost for valid solutions.
        output_file (str): Destination path for the stats file.
    """
    if not results:
        print("⚠️ Aucune donnée valide pour calcul des stats.")
        return

    best_value = min(results.values())
    mean_value = np.mean(list(results.values()))
    # Rank methods (1 = best).
    sorted_algos = sorted(results.items(), key=lambda x: x[1])
    ranks = {algo: rank + 1 for rank, (algo, _) in enumerate(sorted_algos)}

    lines = [f"Stats instance {INSTANCE_NAME}\n"]
    lines.append("Algo | Cout | Ecart relatif meilleur (%) | Ecart relatif moyenne (%) | Rang")
    lines.append("-" * 70)
    for algo, val in results.items():
        rel_best = (val - best_value) / best_value * 100
        rel_mean = (val - mean_value) / mean_value * 100
        rank = ranks[algo]
        lines.append(f"{algo} | {val} | {rel_best:.2f} | {rel_mean:.2f} | {rank}")

    os.makedirs(os.path.dirname(output_file), exist_ok=True)

    with open(output_file, "w") as f:
        f.write("\n".join(lines))

    print(f"Stats sauvegardées dans {output_file}")


# =========================
# Plotting
# =========================

def plot_costs(results):
    """
    Plot total picking cost comparisons for the TOY instance.

    The plot is a simple bar chart:
    - x-axis: algorithms (only those with valid costs),
    - y-axis: total picking cost.

    Args:
        results (dict[str, float]): Mapping algo -> cost for valid solutions.
    """
    if not results:
        print("⚠️ Aucune donnée valide trouvée.")
        return

    algo_names = list(results.keys())
    values = [results[a] for a in algo_names]

    x = np.arange(len(algo_names))

    fig, ax = plt.subplots(figsize=(8, 5))

    ax.bar(
        x,
        values,
        color=BAR_COLOR,   # Single color for a compact toy comparison.
        edgecolor="black"
    )

    ax.set_xlabel("Algorithmes")
    ax.set_ylabel("Coût total de picking")
    # ax.set_title("Comparaison des solutions — Instance TOY")

    ax.set_xticks(x)
    ax.set_xticklabels(algo_names)

    ax.grid(axis="y", linestyle="--", alpha=0.5)

    plt.tight_layout()
    plt.show()


def plot_times(times):
    """
    Plot and save execution time comparisons for the TOY instance.

    Behavior:
    - keep only algorithms with a strictly positive time value,
    - switch to log scale when time ratios are extremely large,
    - save the figure under "analysis/" and close it.

    Args:
        times (dict[str, float]): Mapping algo -> duration in seconds.
    """
    # Keep only algorithms with a measured time (> 0).
    algos = [a for a in times.keys() if times[a] > 0]
    durations = [times[a] for a in algos]

    plt.figure(figsize=(8, 6))
    plt.bar(algos, durations, color="#9C80A4", edgecolor="black")
    plt.ylabel("Temps d'exécution (secondes)")
    # plt.title(f"Comparaison des Temps d'exécution - {INSTANCE_NAME}")

    # Use log scale when the time spread is massive (e.g., Greedy vs Tabu).
    if max(durations) / (min(durations) + 1e-9) > 100:
        plt.yscale('log')
        plt.ylabel("Temps d'exécution (s) - Échelle Log")

    output_path = "analysis/times_comparison_toy.png"
    plt.savefig(output_path)
    plt.close()
    print(f"⏱️ Graphique des temps : {output_path}")


# =========================
# Main
# =========================

if __name__ == "__main__":
    # Collect costs from the solution .txt files.
    res = collect_results()

    if res:
        # Export the combined stats report.
        save_stats(res, OUTPUT_STATS_FILE)

        # Generate the two separate charts.
        plot_costs(res)
        plot_times(EXECUTION_TIMES)