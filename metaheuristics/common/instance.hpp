#pragma once
#include <string>
#include <vector>
#include <unordered_map>

struct Instance
{
    int num_racks = 0, total_slots = 0, num_products = 0, num_circuits = 0, num_aisles = 0, num_orders = 0;
    double aeration_rate = 0.0;

    std::vector<int> rack_capacity, product_circuit;
    std::vector<std::vector<int>> aisles_racks, adjacency, orders;

    // Optional metrics (loaded if present in instance.dat)
    // PMET <name> <num_products> v0 v1 ... v(n-1)
    std::unordered_map<std::string, std::vector<double>> product_metrics;

    // RMET <name> <num_racks> v0 v1 ... v(n-1)
    std::unordered_map<std::string, std::vector<double>> rack_metrics;

    // SMET <name> <value>
    std::unordered_map<std::string, double> scalar_metrics;

    // AMET <name> <num_aisles> v0 ... v(n-1)
    std::unordered_map<std::string, std::vector<double>> aisle_metrics;

    // CMET <name> <num_circuits> v0 ... v(n-1)
    std::unordered_map<std::string, std::vector<double>> circuit_metrics;
};

Instance load_instance_dat(const std::string &path);
