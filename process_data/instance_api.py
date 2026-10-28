from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class InstanceAPI:
    """
    Minimal normalized view of a warehouse instance for data-processing steps.

    Attributes:
        n_products: Total number of products in the instance.
        orders: List of orders, where each order is a list of product ids.
        product_circuit: Mapping product -> circuit id.
        n_circuits: Total number of circuits (derived from product_circuit).
    """
    n_products: int
    orders: List[List[int]]
    product_circuit: List[int]
    n_circuits: int


def adapt(instance) -> InstanceAPI:
    """
    Adapt a raw instance object into a minimal, validated InstanceAPI.

    This adapter:
    - validates required attributes exist,
    - enforces basic type expectations,
    - derives n_products and n_circuits from product_circuit.

    Args:
        instance: Warehouse instance-like object exposing "orders" and "product_circuit".

    Returns:
        A validated InstanceAPI wrapper.
    """
    # Validate required attributes on the input instance.
    if not hasattr(instance, "orders"):
        raise AttributeError("WarehouseInstance.orders manquant.")
    if not hasattr(instance, "product_circuit"):
        raise AttributeError("WarehouseInstance.product_circuit manquant.")

    orders = instance.orders
    product_circuit = instance.product_circuit

    # Validate basic types to fail fast in downstream processing.
    if not isinstance(orders, list):
        raise TypeError("WarehouseInstance.orders doit être une list.")
    if not isinstance(product_circuit, list) or not product_circuit:
        raise TypeError("WarehouseInstance.product_circuit doit être une list non vide.")

    # Derive core dimensions from the product-to-circuit mapping.
    n_products = len(product_circuit)
    n_circuits = max(product_circuit) + 1 if product_circuit else 0
    return InstanceAPI(n_products=n_products, orders=orders, product_circuit=product_circuit, n_circuits=n_circuits)