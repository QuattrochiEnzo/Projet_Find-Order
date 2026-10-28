"""Warehouse instance data loader."""

import os
from dataclasses import dataclass
from typing import Dict, List


@dataclass
class WarehouseInstance:
    """Fully loaded slotting problem instance."""
    adjacency: List[List[int]]                                                                                # rack-to-rack distance matrix
    rack_capacity: List[int]                                                                                  # capacity per rack (number of products)
    product_circuit: List[int]                                                                                # circuit id per product
    aisles_racks: List[List[int]]                                                                             # rack ids grouped by aisle
    orders: List[List[int]]                                                                                   # list of orders (each order is a list of product ids)
    metadata: Dict[str, float]                                                                                # instance metadata (mix of ints and floats)


class WarehouseLoader:
    """Load warehouse instance files from a directory on disk."""

    def __init__(self, warehouse_dir: str):
        self.dir = warehouse_dir                                                                              # instance directory path

    def _path(self, filename: str) -> str:
        return os.path.join(self.dir, filename)                                                               # build file path inside instance directory

    def _read_lines(self, filename: str) -> List[str]:
        with open(self._path(filename), encoding="utf-8") as f:                                               # open file (UTF-8)
            return [l.strip() for l in f if l.strip() and not l.startswith("...")]                            # strip lines, drop empties and "..." comments

    def load_adjacency_matrix(self) -> List[List[int]]:
        lines = self._read_lines("rack_adjacency_matrix.txt")                                                 # read adjacency file
        n = int(lines[0])                                                                                     # declared matrix size
        return [list(map(int, lines[i + 1].split())) for i in range(n)]                                       # parse n rows

    def load_rack_capacity(self) -> List[int]:
        lines = self._read_lines("rack_capacity.txt")                                                         # read rack capacities file
        return list(map(int, lines[1:]))                                                                      # skip header count, parse capacities

    def load_product_circuits(self) -> List[int]:
        lines = self._read_lines("product_circuit.txt")                                                       # read product->circuit file
        return list(map(int, lines[1:]))                                                                      # skip header count, parse circuits

    def load_aisles_racks(self) -> List[List[int]]:
        lines = self._read_lines("aisle_racks.txt")                                                           # read aisle definition file
        # New format: <k> <rack_id_1> <rack_id_2> ...  (skip the leading k token)                             # parse spec: first token is count
        return [list(map(int, l.split()))[1:] for l in lines[1:]]                                             # skip header count line, parse each aisle line

    def load_orders(self) -> List[List[int]]:
        lines = self._read_lines("orders.txt")                                                                # read orders file
        return [list(map(int, l.split()))[1:] for l in lines[1:]]                                             # skip header count line, drop leading k per order

    def load_metadata(self) -> Dict[str, float]:
        lines = self._read_lines("metadata.txt")                                                              # read metadata file
        keys = ["num_racks", "total_slots", "aeration_rate",
                "num_products", "num_circuits", "num_aisles", "num_orders"]                                   # fixed metadata schema (file order)
        return {k: float(lines[i]) if k == "aeration_rate" else int(lines[i])
                for i, k in enumerate(keys)}                                                                  # parse aeration as float, others as int

    def load_all(self) -> WarehouseInstance:
        return WarehouseInstance(
            adjacency=self.load_adjacency_matrix(),                                                           # load adjacency matrix
            rack_capacity=self.load_rack_capacity(),                                                          # load rack capacities
            product_circuit=self.load_product_circuits(),                                                     # load product circuits
            aisles_racks=self.load_aisles_racks(),                                                            # load aisle->racks mapping
            orders=self.load_orders(),                                                                        # load orders
            metadata=self.load_metadata(),                                                                    # load metadata
        )                                                                                                     # aggregate into a single instance object