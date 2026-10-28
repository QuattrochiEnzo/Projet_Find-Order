#include "common/instance.hpp"
#include <algorithm>
#include <iostream>
#include <string>

static void print_range(const std::string &name, const std::vector<double> &v)
{
    if (v.empty())
    {
        std::cout << name << " (empty)\n";
        return;
    }
    double mn = v[0], mx = v[0];
    for (double x : v)
    {
        mn = std::min(mn, x);
        mx = std::max(mx, x);
    }
    std::cout << name << " size=" << v.size() << " range=[" << mn << "," << mx << "]\n";
}

int main(int argc, char **argv)
{
    if (argc < 2)
    {
        std::cerr << "Usage: test_load <path_to_instance.dat>\n";
        return 1;
    }

    try
    {
        const auto I = load_instance_dat(argv[1]);

        std::cout << "[OK] loaded instance\n";
        std::cout << "num_products=" << I.num_products
                  << " num_racks=" << I.num_racks
                  << " num_aisles=" << I.num_aisles
                  << " num_circuits=" << I.num_circuits
                  << " num_orders=" << I.num_orders
                  << "\n";

        std::cout << "PMET=" << I.product_metrics.size()
                  << " RMET=" << I.rack_metrics.size()
                  << " AMET=" << I.aisle_metrics.size()
                  << " CMET=" << I.circuit_metrics.size()
                  << " SMET=" << I.scalar_metrics.size()
                  << "\n";

        // --- list metrics names + sizes (optional but useful) ---
        for (const auto &kv : I.product_metrics)
            std::cout << "PMET " << kv.first << " size=" << kv.second.size() << "\n";
        for (const auto &kv : I.rack_metrics)
            std::cout << "RMET " << kv.first << " size=" << kv.second.size() << "\n";
        for (const auto &kv : I.aisle_metrics)
            std::cout << "AMET " << kv.first << " size=" << kv.second.size() << "\n";
        for (const auto &kv : I.circuit_metrics)
            std::cout << "CMET " << kv.first << " size=" << kv.second.size() << "\n";

        // --- spot checks ranges (what we actually care about) ---
        if (auto it = I.product_metrics.find("popularity_score"); it != I.product_metrics.end())
            print_range("popularity_score", it->second);
        else
            std::cout << "popularity_score missing (PMET)\n";

        if (auto it = I.product_metrics.find("turnover_score"); it != I.product_metrics.end())
            print_range("turnover_score", it->second);
        else
            std::cout << "turnover_score missing (PMET)\n";

        if (auto it = I.aisle_metrics.find("aisle_entry_exit_score"); it != I.aisle_metrics.end())
            print_range("aisle_entry_exit_score", it->second);
        else
            std::cout << "aisle_entry_exit_score missing (AMET)\n";

        if (auto it = I.aisle_metrics.find("aisle_entry_exit_rank"); it != I.aisle_metrics.end())
            print_range("aisle_entry_exit_rank", it->second);
        else
            std::cout << "aisle_entry_exit_rank missing (AMET)\n";

        // --- sanity: expected sizes if present ---
        if (!I.aisle_metrics.empty())
        {
            for (const auto &kv : I.aisle_metrics)
                if ((int)kv.second.size() != I.num_aisles)
                    std::cerr << "WARNING: AMET " << kv.first << " has size "
                              << kv.second.size() << " but num_aisles=" << I.num_aisles << "\n";
        }
        if (!I.circuit_metrics.empty())
        {
            for (const auto &kv : I.circuit_metrics)
                if ((int)kv.second.size() != I.num_circuits)
                    std::cerr << "WARNING: CMET " << kv.first << " has size "
                              << kv.second.size() << " but num_circuits=" << I.num_circuits << "\n";
        }

        return 0;
    }
    catch (const std::exception &e)
    {
        std::cerr << "ERROR: " << e.what() << "\n";
        return 1;
    }
}
