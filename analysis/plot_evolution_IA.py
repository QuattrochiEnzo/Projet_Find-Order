"""
Training / inference log plotting utility.

This script scans one RL run directory and produces two time-series figures:
- feasible_ep*_cost*.txt  -> training-time feasible snapshots (final exact cost)
- infer_step*_cost_*.txt  -> inference-time snapshots (best cost over steps)

Each figure uses:
- file modification time as x-axis
- cost as y-axis (formatted in millions)
- a dynamic y-limits margin to keep the curve readable

Outputs are saved into: ./figures/
"""

import os
import re
import argparse
from datetime import datetime
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import matplotlib.dates as mdates

# ==============================
# Global configuration
# ==============================
base_dir = os.path.dirname(os.path.abspath(__file__))                     # Script directory
output_dir = os.path.join(base_dir, "figures")                            # Output figures directory
os.makedirs(output_dir, exist_ok=True)                                    # Ensure output directory exists

pattern_infer = re.compile(r"infer_step\d+_cost_(\d+)\.txt")              # Inference snapshot filename pattern
pattern_feasible = re.compile(r"feasible_ep\d+_cost(\d+)\.txt")           # Training feasible filename pattern

fmt_date = mdates.DateFormatter("%Y-%m-%d %H:%M")                         # X-axis date format
fmt_millions = ticker.FuncFormatter(lambda x, pos: f"{x/1e6:.1f} M")      # Y-axis formatter (millions)

point_size = 2                                                            # Marker size for scatter-like plots
MARGIN = 100000                                                           # Absolute y-axis margin around min/max


# ==============================
# Core processing function
# ==============================

def traiter_dossier(folder_path: str, instance_name: str, col_feas: str, col_infer: str) -> None:
    """
    Parse one run directory and generate figures for training and inference curves.

    Args:
        folder_path:   path containing *.txt snapshots
        instance_name: short instance name used in output filenames
        col_feas:      color for feasible (training) curve
        col_infer:     color for inference curve
    """
    print(f"\n--- Processing instance: {instance_name.upper()} ---")       # Announce instance processing
    print("Path:", folder_path)                                           # Trace target folder

    if not os.path.exists(folder_path):
        print(f"ERROR: folder does not exist -> {folder_path}")           # Guard missing directory
        return

    times_infer, costs_infer = [], []                                     # Inference data
    times_feas, costs_feas = [], []                                       # Feasible training data

    for filename in os.listdir(folder_path):
        filepath = os.path.join(folder_path, filename)                    # Full path to file
        if not os.path.isfile(filepath):
            continue                                                      # Skip subdirectories

        timestamp = os.path.getmtime(filepath)                            # Use mtime as x-axis time proxy
        dt = datetime.fromtimestamp(timestamp)                            # Convert to datetime object

        match_infer = pattern_infer.match(filename)                       # Detect inference snapshots
        if match_infer:
            costs_infer.append(int(match_infer.group(1)))                 # Extract cost from filename
            times_infer.append(dt)                                        # Store timestamp
            continue

        match_feasible = pattern_feasible.match(filename)                 # Detect training feasible snapshots
        if match_feasible:
            costs_feas.append(int(match_feasible.group(1)))               # Extract cost from filename
            times_feas.append(dt)                                         # Store timestamp

    def sort_data(t, c):
        """Sort (time, cost) pairs by time and return unpacked lists."""
        if not t:
            return [], []
        data = sorted(zip(t, c), key=lambda x: x[0])                      # Sort by datetime
        t_sorted, c_sorted = zip(*data)                                   # Unpack pairs
        return list(t_sorted), list(c_sorted)

    times_infer_sorted, costs_infer_sorted = sort_data(times_infer, costs_infer)  # Sort inference data
    times_feas_sorted, costs_feas_sorted = sort_data(times_feas, costs_feas)      # Sort feasible data

    if not costs_infer and not costs_feas:
        print("No data found (neither infer nor feasible).")              # Early exit on empty folder
        return

    # ==============================
    # Feasible curve (training snapshots)
    # ==============================
    if times_feas_sorted:
        y_min_feas = min(costs_feas) - MARGIN                              # Lower y-limit with margin
        y_max_feas = max(costs_feas) + MARGIN                              # Upper y-limit with margin

        plt.figure(figsize=(12, 6))                                        # Create figure
        plt.plot(
            times_feas_sorted, costs_feas_sorted,                          # X, Y values
            marker="o", markersize=point_size,                             # Point markers
            color=col_feas, linestyle="-", linewidth=1.5                   # Line style
        )
        plt.fill_between(times_feas_sorted, costs_feas_sorted, color=col_feas, alpha=0.15)  # Light area fill

        plt.ylim(y_min_feas, y_max_feas)                                   # Apply y-limits

        plt.xlabel("Time")                                                 # X-axis label
        plt.ylabel("Cost")                                                 # Y-axis label
        plt.gca().xaxis.set_major_formatter(fmt_date)                       # Format timestamps
        plt.gca().yaxis.set_major_formatter(fmt_millions)                   # Format in millions
        plt.xticks(rotation=45)                                             # Improve date readability
        plt.grid(True, linestyle="--", alpha=0.6)                           # Add background grid
        plt.tight_layout()                                                  # Avoid label clipping

        filename_feas = f"entrainement_{instance_name}_IA.png"              # Output filename
        path_feas = os.path.join(output_dir, filename_feas)                 # Output path
        plt.savefig(path_feas, dpi=300)                                     # Save high-res image
        plt.close()                                                         # Close figure to free memory
        print(f"Saved feasible plot: {path_feas}")                          # Trace output
    else:
        print(f"No 'feasible' data found for {instance_name}")              # Trace missing data

    # ==============================
    # Inference curve
    # ==============================
    if times_infer_sorted:
        y_min_infer = min(costs_infer) - MARGIN                             # Lower y-limit with margin
        y_max_infer = max(costs_infer) + MARGIN                             # Upper y-limit with margin

        plt.figure(figsize=(12, 6))                                         # Create figure
        plt.plot(
            times_infer_sorted, costs_infer_sorted,                          # X, Y values
            marker="o", markersize=point_size,                               # Point markers
            color=col_infer, linestyle="-", linewidth=1.5                    # Line style
        )
        plt.fill_between(times_infer_sorted, costs_infer_sorted, color=col_infer, alpha=0.2)  # Light area fill

        plt.ylim(y_min_infer, y_max_infer)                                   # Apply y-limits

        plt.xlabel("Time")                                                   # X-axis label
        plt.ylabel("Cost")                                                   # Y-axis label
        plt.gca().xaxis.set_major_formatter(fmt_date)                         # Format timestamps
        plt.gca().yaxis.set_major_formatter(fmt_millions)                     # Format in millions
        plt.xticks(rotation=45)                                               # Improve date readability
        plt.grid(True, linestyle="--", alpha=0.6)                             # Add background grid
        plt.tight_layout()                                                    # Avoid label clipping

        filename_infer = f"evolution_cost_infer_{instance_name}.png"          # Output filename
        path_infer = os.path.join(output_dir, filename_infer)                 # Output path
        plt.savefig(path_infer, dpi=300)                                      # Save high-res image
        plt.close()                                                           # Close figure to free memory
        print(f"Saved infer plot: {path_infer}")                              # Trace output
    else:
        print(f"No 'infer' data found for {instance_name}")                   # Trace missing data


# ==============================
# CLI entrypoint
# ==============================
def main():
    """Parse CLI arguments, locate run directory, and generate plots."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--instance",
        required=True,
        help="Instance name (e.g. market, family, category, ...)."
    )
    parser.add_argument(
        "--mode",
        choices=["auto", "swap", "destroy"],
        default="auto",
        help="Where to look for files. auto=try swap_products then destroy."
    )
    parser.add_argument("--seed", type=int, default=0, help="Seed directory (seed_0 by default).")
    args = parser.parse_args()

    instance = args.instance.lower()                                        # Normalize instance id
    seed_dir = f"seed_{args.seed}"                                          # Build seed directory name

    colors = {                                                              # Default per-instance colors
        "market": ("#418afa", "#41c2fa"),
        "family": ("#f542c2", "#f5427b"),
        "category": ("#20f75b", "#1ea635"),
    }
    col_feas, col_infer = colors.get(instance, ("#2b8a3e", "#1864ab"))      # Fallback palette

    def build_path(mode_dir: str) -> str:
        """Build absolute path to a run directory for a given mode."""
        return os.path.abspath(
            os.path.join(base_dir, "..", f"dqn_models_{instance}", mode_dir, seed_dir)
        )

    if args.mode == "auto":
        candidates = [build_path("swap_products"), build_path("destroy")]   # Try preferred mode first
    elif args.mode == "swap":
        candidates = [build_path("swap_products")]                          # Swap-products only
    else:
        candidates = [build_path("destroy")]                                # Destroy only

    folder_path = None                                                      # Selected folder
    for p in candidates:
        if os.path.isdir(p):
            folder_path = p                                                 # Choose first existing directory
            break

    if folder_path is None:
        print("ERROR: no directory found among:")                            # Announce failure
        for p in candidates:
            print(" -", p)                                                  # Print candidates for debugging
        return

    traiter_dossier(                                                        # Generate both figures
        folder_path=folder_path,
        instance_name=instance,
        col_feas=col_feas,
        col_infer=col_infer
    )
    print("\nDone! All figures are stored in the figures/ directory.")       # Final completion message


if __name__ == "__main__":
    main()