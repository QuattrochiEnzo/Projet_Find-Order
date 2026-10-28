/*
 * heuristic_blanck.cpp
 *
 * Pre-shot skeleton: all useful data is loaded and preprocessed before entering the heuristic.
 * Goal: implement assignment logic only, without dealing with parsing, file formats, or I/O.
 *
 * Stable CLI:
 *   heuristic_blanck <instance_dat> <solution_dir>
 *
 * Arguments:
 *   instance_dat   Path to the exported instance file (instance.dat).
 *   solution_dir   Output directory where rack_product_assignment.txt is written.
 *
 * Behavior:
 *   - Load instance from instance_dat.
 *   - Build PreparedData (precomputed helpers).
 *   - Run heuristic_blanck(prepared).
 *   - Write rack_product_assignment.txt to solution_dir.
 *
 * Notes:
 *   - No validation is performed here (checked externally by checker.py).
 *   - A picking-cost estimate is logged for quick debugging.
 */

#include "../common/instance.hpp"
#include "../common/solution.hpp"
#include "../common/heuristic_utils.hpp"

#include <algorithm>
#include <chrono>
#include <filesystem>
#include <iostream>
#include <numeric>
#include <stdexcept>
#include <string>
#include <unordered_set>
#include <vector>

using Clock = std::chrono::steady_clock;

/*
 * Pre-shot data: everything needed to implement a heuristic.
 *
 * This struct holds references to the loaded instance and exposes derived helper structures
 * so that heuristics can focus on assignment logic only.
 */
struct PreparedData
{
    /* ------------------------------
     * Raw data (references to Instance)
     * ------------------------------ */
    const Instance &I;
    const int num_racks;
    const int num_products;
    const int num_circuits;
    const int num_aisles;
    const int num_orders;

    /* Always standardized in [0, 1]. */
    const double aeration_frac;

    const std::vector<int> &rack_capacity;             /* [rack] */
    const std::vector<std::vector<int>> &aisles_racks; /* [aisle] -> racks */
    const std::vector<std::vector<int>> &adjacency;    /* distances */
    const std::vector<std::vector<int>> &orders;       /* [order] -> products */

    /* ------------------------------
     * Normalized / cleaned copies
     * ------------------------------ */
    std::vector<int> product_circuit_norm; /* [product] in 0..C-1 */

    /* ------------------------------
     * Generic precomputations (helpers)
     * ------------------------------ */
    std::vector<int> rack_to_aisle;                    /* [rack] -> aisle (or -1) */
    std::vector<std::vector<int>> products_by_circuit; /* [circuit] -> products */
    std::vector<int> racks_usable;                     /* racks with capacity > 0 (START/END excluded) */
    std::vector<int> aisle_total_capacity;             /* [aisle] sum capacities */
    std::vector<int> aisle_min_aeration;               /* [aisle] ceil(total_cap * aeration_frac) */
    std::vector<int> aisle_usable_capacity;            /* [aisle] total_cap - min_aeration */

    /*
     * Build the pre-shot view of the instance.
     *
     * Steps:
     *   - Normalize circuit identifiers into 0..C-1.
     *   - Build helper structures used by heuristics.
     *   - Run lightweight internal sanity checks.
     */
    explicit PreparedData(const Instance &inst)
        : I(inst),
          num_racks(inst.num_racks),
          num_products(inst.num_products),
          num_circuits(inst.num_circuits),
          num_aisles(inst.num_aisles),
          num_orders(inst.num_orders),
          aeration_frac(standardize_aeration(inst.aeration_rate)),
          rack_capacity(inst.rack_capacity),
          aisles_racks(inst.aisles_racks),
          adjacency(inst.adjacency),
          orders(inst.orders)
    {
        /* Normalize circuit ids in a robust way (handles imperfect input). */
        product_circuit_norm = normalize_product_circuit(inst.product_circuit, num_circuits);

        build_precomputations();
        sanity_checks();
    }

private:
    /*
     * Build derived helper structures:
     *   1) rack_to_aisle mapping (with consistency checks)
     *   2) products_by_circuit (circuit -> list of products)
     *   3) racks_usable (exclude sentinel racks 0 and n-1)
     *   4) per-aisle capacities and aeration budgets
     */
    void build_precomputations()
    {
        /* 1) Build rack_to_aisle mapping. */
        rack_to_aisle.assign((std::size_t)num_racks, -1);
        for (std::size_t a = 0; a < aisles_racks.size(); ++a)
        {
            for (int r : aisles_racks[a])
            {
                const int ai = (int)a;

                if (!(0 <= r && r < num_racks))
                    die("aisles_racks contains invalid rack id r=" + std::to_string(r));

                if (rack_to_aisle[(std::size_t)r] != -1 && rack_to_aisle[(std::size_t)r] != ai)
                {
                    die("rack appears in multiple aisles: r=" + std::to_string(r) +
                        " aisles=" + std::to_string(rack_to_aisle[(std::size_t)r]) + "," + std::to_string(a));
                }

                rack_to_aisle[(std::size_t)r] = ai;
            }
        }

        /* 2) Group products by circuit. */
        products_by_circuit.assign((std::size_t)num_circuits, {});
        for (int p = 0; p < num_products; ++p)
        {
            const int c = product_circuit_norm[(std::size_t)p];
            products_by_circuit[(std::size_t)c].push_back(p);
        }

        /* 3) List usable racks: exclude START=0 and END=n-1, keep positive capacity only. */
        racks_usable.clear();
        racks_usable.reserve((std::size_t)std::max(0, num_racks - 2));
        for (int r = 1; r < num_racks - 1; ++r)
        {
            if (rack_capacity[(std::size_t)r] > 0)
                racks_usable.push_back(r);
        }

        /* 4) Per-aisle capacity aggregates and aeration budgets. */
        aisle_total_capacity.assign((std::size_t)num_aisles, 0);
        aisle_min_aeration.assign((std::size_t)num_aisles, 0);
        aisle_usable_capacity.assign((std::size_t)num_aisles, 0);

        for (int a = 0; a < num_aisles; ++a)
        {
            int total_cap = 0;
            for (int r : aisles_racks[(std::size_t)a])
                total_cap += rack_capacity[(std::size_t)r];

            aisle_total_capacity[(std::size_t)a] = total_cap;

            /* aeration_frac is in [0, 1]. */
            const int min_aer = ceil_int((double)total_cap * aeration_frac);

            aisle_min_aeration[(std::size_t)a] = min_aer;
            aisle_usable_capacity[(std::size_t)a] = std::max(0, total_cap - min_aer);
        }
    }

    /*
     * Lightweight structural checks to catch obvious instance inconsistencies early.
     * Full validation is performed by the external checker.
     */
    void sanity_checks() const
    {
        if ((int)rack_capacity.size() != num_racks)
            die("rack_capacity size mismatch");
        if ((int)aisles_racks.size() != num_aisles)
            die("aisles_racks size mismatch");
        if ((int)orders.size() != num_orders)
            die("orders size mismatch");

        if ((int)adjacency.size() != num_racks)
            die("adjacency rows mismatch");
        for (int i = 0; i < num_racks; ++i)
            if ((int)adjacency[(std::size_t)i].size() != num_racks)
                die("adjacency cols mismatch at row i=" + std::to_string(i));

        if (num_racks < 2)
            die("num_racks too small");
        if (num_products < 0 || num_circuits < 0 || num_aisles < 0 || num_orders < 0)
            die("negative dimension in instance");

        if (!(0.0 <= aeration_frac && aeration_frac <= 1.0))
            die("aeration_frac not in [0,1]: " + std::to_string(aeration_frac));
    }
};

/*
 * BLANCK heuristic: implement assignment logic only.
 *
 * Contract:
 *   - Return a vector "assignment" of size prepared.num_products.
 *   - assignment[p] = rack_id.
 *
 * Available prepared data:
 *   - rack_capacity, aisles_racks, adjacency, orders
 *   - rack_to_aisle, products_by_circuit, racks_usable
 *   - aisle_usable_capacity (per-aisle placement budget if you enforce aeration)
 */
std::vector<int> heuristic_blanck(const PreparedData &prepared)
{
    std::vector<int> assignment((std::size_t)prepared.num_products, -1);

    /*
     * Heuristic implementation area.
     *
     * You may use directly:
     *   - prepared.racks_usable
     *   - prepared.products_by_circuit
     *   - prepared.adjacency
     *   - prepared.orders
     *   - prepared.rack_to_aisle
     *   - prepared.aisle_usable_capacity
     *
     * Typical local invariants to maintain:
     *   - remaining_rack_capacity[r]
     *   - used_in_aisle[a] (to enforce aeration via aisle_usable_capacity)
     *   - circuit intervals (min/max rack) if you enforce contiguity
     */

    return assignment;
}

int main(int argc, char **argv)
{
    namespace fs = std::filesystem;

    /*
     * Modes:
     *   - long:  heuristic_blanck <instance_dat> <solution_dir>
     *   - short: heuristic_blanck <toy|market|family|category|instance_dir>
     */
    if (argc != 2 && argc != 3)
    {
        std::cerr << "Usage:\n"
                  << "  " << argv[0] << " <instance_dat> <solution_dir>\n"
                  << "  " << argv[0] << " <toy|market|family|category|instance_dir>\n";
        return 2;
    }

    fs::path instance_dat;
    fs::path solution_dir;

    if (argc == 3)
    {
        instance_dat = fs::path(argv[1]);
        solution_dir = fs::path(argv[2]);
    }
    else
    {
        const std::string key(argv[1]);

        /* Find project root (parent directory that contains data/). */
        fs::path root = fs::current_path();
        bool found = false;
        for (int up = 0; up < 10; ++up)
        {
            if (fs::exists(root / "data"))
            {
                found = true;
                break;
            }
            if (!root.has_parent_path())
                break;
            root = root.parent_path();
        }
        if (!found)
            die("Cannot locate project root (no data/ found in parent dirs).");

        const fs::path data_dir = root / "data";
        fs::path inst_dir;

        /*
         * Return the first existing path among candidates, or an empty path.
         * Candidate lists include common typos in folder names.
         */
        auto first_existing = [](const std::vector<fs::path> &cands) -> fs::path
        {
            for (std::size_t i = 0; i < cands.size(); ++i)
                if (fs::exists(cands[i]))
                    return cands[i];
            return fs::path();
        };

        if (key == "toy")
        {
            std::vector<fs::path> cands;
            cands.push_back(data_dir / "warehouse_toy");
            cands.push_back(data_dir / "wharehouse_toy");
            inst_dir = first_existing(cands);
        }
        else if (key == "market")
        {
            std::vector<fs::path> cands;
            cands.push_back(data_dir / "wharehouse_big_market");
            cands.push_back(data_dir / "warehouse_big_market");
            cands.push_back(data_dir / "wharehouse_big_" / "wharehouse_big_market");
            cands.push_back(data_dir / "warehouse_big_" / "warehouse_big_market");
            inst_dir = first_existing(cands);
        }
        else if (key == "family")
        {
            std::vector<fs::path> cands;
            cands.push_back(data_dir / "wharehouse_big_family");
            cands.push_back(data_dir / "warehouse_big_family");
            cands.push_back(data_dir / "wharehouse_big_" / "wharehouse_big_family");
            cands.push_back(data_dir / "warehouse_big_" / "warehouse_big_family");
            inst_dir = first_existing(cands);
        }
        else if (key == "category")
        {
            std::vector<fs::path> cands;
            cands.push_back(data_dir / "wharehouse_big_category");
            cands.push_back(data_dir / "warehouse_big_category");
            cands.push_back(data_dir / "wharehouse_big_" / "wharehouse_big_category");
            cands.push_back(data_dir / "warehouse_big_" / "warehouse_big_category");
            inst_dir = first_existing(cands);
        }
        else
        {
            /* Treat the argument as an instance directory path. */
            inst_dir = fs::path(key);
            if (!inst_dir.is_absolute())
                inst_dir = fs::absolute(inst_dir);
            if (!fs::exists(inst_dir))
                die("Instance directory not found: " + inst_dir.string());
        }

        if (inst_dir.empty() || !fs::exists(inst_dir))
            die("Alias not found under data/: " + key);

        instance_dat = inst_dir / "instance.dat";
        solution_dir = inst_dir / "solutions";
    }

    const fs::path out_path = solution_dir / "rack_product_assignment.txt";

    try
    {
        auto t0 = Clock::now();

        fs::create_directories(solution_dir);

        if (!fs::exists(instance_dat))
            die("Missing instance.dat: " + instance_dat.string());

        Instance instance = load_instance_dat(instance_dat.string());
        PreparedData prepared(instance);

        std::vector<int> assignment = heuristic_blanck(prepared);

        /*
         * Quick logging: estimated picking cost (same convention as the checker / spec):
         * START = 0, END = n - 1 are sentinel racks.
         */
        double cost = compute_picking_cost(instance, assignment);

        write_solution_file(out_path.string(), assignment);

        auto t1 = Clock::now();
        const double ms = std::chrono::duration<double, std::milli>(t1 - t0).count();

        std::cout << "heuristic=blanck time_ms=" << ms
                  << " cost=" << cost
                  << " status=written\n";
        return 0;
    }
    catch (const std::exception &e)
    {
        std::cerr << "ERROR: " << e.what() << "\n";
        return 1;
    }
}
