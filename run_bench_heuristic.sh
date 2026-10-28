#!/usr/bin/env bash
set -euo pipefail

ROOT=/autofs/unitytravail/travail/pleplomb/S9/POIP/poip_groupeb
BENCH="$ROOT/data/bench_milp"
SOLVER="$ROOT/cpp/build/bin/heuristic_greedy_circuit_smart"
EXPORT_PY="$ROOT/tools/export_instance.py"

for d in "$BENCH"/sub__*; do
  [ -d "$d" ] || continue
  inst="$d/instance.dat"
  out="$d/solutions/heuristic_greedy_circuit_smart"

  if [ ! -f "$inst" ]; then
    echo "[EXPORT] $d"
    python3 "$EXPORT_PY" --instance "$d" || { echo "[FAIL-EXPORT] $d"; continue; }
  fi

  if [ -f "$inst" ]; then
    mkdir -p "$out"
    echo "[RUN] $d"
    "$SOLVER" "$inst" "$out" || echo "[FAIL] $d"
  else
    echo "[SKIP] no instance.dat in $d (export failed?)"
  fi
done
