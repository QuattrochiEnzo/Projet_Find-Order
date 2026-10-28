/*
 * heuristic_greedy_circuit.cpp
 *
 * Baseline heuristic ("POIP-safe"):
 * - Builds the solution circuit-by-circuit to naturally preserve circuit contiguity.
 * - Enforces rack capacities and per-aisle aeration using aisle_usable_capacity.
 * - Product score:
 *     * use PMET popularity_score if available,
 *     * otherwise fallback to order frequency.
 *
 * IMPORTANT normalization:
 * - product_circuit in instance.dat may be 0-based (0..C-1) or 1-based (1..C).
 * - We normalize to 0..C-1 (same convention as gurobi_slotting_toy.cpp via min/max).
 *
 * CLI (same as heuristic_blanck):
 *   heuristic_greedy_circuit <instance_dat> <solution_dir>
 *   heuristic_greedy_circuit <toy|market|family|category|instance_dir>
 */

#include "../common/instance.hpp"
#include "../common/solution.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

using Clock = std::chrono::steady_clock;
namespace fs = std::filesystem;

static void die(const std::string &msg) { throw std::runtime_error(msg); }

static std::size_t idx(int i, const char *what)
{
    if (i < 0)
        die(std::string("negative index for ") + what + ": " + std::to_string(i));
    return static_cast<std::size_t>(i);
}

static std::vector<int> normalize_product_circuit(const std::vector<int> &raw, int num_circuits)
{
    if (raw.empty())
        return {};

    std::vector<int> pc = raw;

    const int mn = *std::min_element(pc.begin(), pc.end());
    const int mx = *std::max_element(pc.begin(), pc.end());

    /*
     * Common export format: circuits in 1..C.
     * Convert to 0..C-1.
     */
    if (mn == 1 && mx == num_circuits)
        for (int &c : pc)
            --c;

    /*
     * Strict validation: after normalization, circuits must lie in 0..C-1.
     */
    for (int c : pc)
        if (!(0 <= c && c < num_circuits))
            die("product_circuit not valid after normalization: c=" + std::to_string(c) +
                " C=" + std::to_string(num_circuits) +
                " (raw min=" + std::to_string(mn) + " raw max=" + std::to_string(mx) + ")");

    return pc;
}

struct PreparedData
{
    const Instance &I;

    const int num_racks;
    const int num_products;
    const int num_circuits;
    const int num_aisles;
    const int num_orders;

    /* Aeration standardized as a fraction in [0,1]. */
    const double aeration_frac;

    const std::vector<int> &rack_capacity;             /* [rack] */
    const std::vector<int> &product_circuit_raw;       /* [product] raw (0-based or 1-based) */
    const std::vector<std::vector<int>> &aisles_racks; /* [aisle] -> racks */
    const std::vector<std::vector<int>> &adjacency;    /* [i][j] */
    const std::vector<std::vector<int>> &orders;       /* [order] -> products */

    /* --- normalized helpers --- */
    std::vector<int> product_circuit;                  /* [product] normalized 0..C-1 */
    std::vector<int> rack_to_aisle;                    /* [rack] -> aisle or -1 */
    std::vector<std::vector<int>> products_by_circuit; /* [circuit] -> products */
    std::vector<int> racks_usable;                     /* racks with capacity > 0 */
    std::vector<int> aisle_total_capacity;             /* [aisle] */
    std::vector<int> aisle_min_aeration;               /* [aisle] */
    std::vector<int> aisle_usable_capacity;            /* [aisle] total - min_aeration */

    explicit PreparedData(const Instance &inst)
        : I(inst),
          num_racks(inst.num_racks),
          num_products(inst.num_products),
          num_circuits(inst.num_circuits),
          num_aisles(inst.num_aisles),
          num_orders(inst.num_orders),
          aeration_frac(standardize_aeration(inst.aeration_rate)),
          rack_capacity(inst.rack_capacity),
          product_circuit_raw(inst.product_circuit),
          aisles_racks(inst.aisles_racks),
          adjacency(inst.adjacency),
          orders(inst.orders)
    {
        build_precomputations();
        sanity_checks();
    }

private:
    static double standardize_aeration(double a)
    {
        /*
         * Spec: aeration rate is typically given as a percentage (e.g., 15),
         * but we also accept a fraction (e.g., 0.15).
         */
        if (a > 1.0)
            return a / 100.0;
        if (a < 0.0)
            return 0.0;
        return a;
    }

    static int ceil_int(double x) { return (int)std::ceil(x - 1e-12); }

    void build_precomputations()
    {
        const std::size_t nR = static_cast<std::size_t>(num_racks);
        const std::size_t nP = static_cast<std::size_t>(num_products);
        const std::size_t nC = static_cast<std::size_t>(num_circuits);
        const std::size_t nA = static_cast<std::size_t>(num_aisles);

        /*
         * (0) Normalize product_circuit to 0-based indexing.
         */
        if (product_circuit_raw.size() != nP)
            die("product_circuit size mismatch (precompute)");
        product_circuit = normalize_product_circuit(product_circuit_raw, num_circuits);

        /*
         * (1) Build rack_to_aisle mapping.
         */
        rack_to_aisle.assign(nR, -1);
        for (std::size_t a = 0; a < nA; ++a)
            for (int r_int : aisles_racks[a])
                if (0 <= r_int && r_int < num_racks)
                    rack_to_aisle[idx(r_int, "rack")] = static_cast<int>(a);

        /*
         * (2) Group products by circuit (0..C-1).
         */
        products_by_circuit.assign(nC, {});
        for (std::size_t p = 0; p < nP; ++p)
        {
            const int c_int = product_circuit[p];
            if (!(0 <= c_int && c_int < num_circuits))
                die("Invalid circuit id for product " + std::to_string(p) +
                    " raw=" + std::to_string(product_circuit_raw[p]) +
                    " norm=" + std::to_string(c_int));
            products_by_circuit[idx(c_int, "circuit")].push_back(static_cast<int>(p));
        }

        /*
         * (3) List racks with strictly positive capacity.
         */
        racks_usable.clear();
        racks_usable.reserve(nR);
        for (int r = 0; r < num_racks; ++r)
            if (rack_capacity[idx(r, "rack")] > 0)
                racks_usable.push_back(r);

        /*
         * (4) Aeration per aisle:
         * usable_capacity[a] = total_capacity[a] - ceil(total_capacity[a] * aeration_frac).
         */
        aisle_total_capacity.assign(nA, 0);
        aisle_min_aeration.assign(nA, 0);
        aisle_usable_capacity.assign(nA, 0);

        for (std::size_t a = 0; a < nA; ++a)
        {
            int total_cap = 0;
            for (int r_int : aisles_racks[a])
            {
                if (!(0 <= r_int && r_int < num_racks))
                    die("Invalid rack id in aisles_racks");
                total_cap += rack_capacity[idx(r_int, "rack")];
            }

            aisle_total_capacity[a] = total_cap;
            const int min_aer = ceil_int(total_cap * aeration_frac);
            aisle_min_aeration[a] = min_aer;
            aisle_usable_capacity[a] = std::max(0, total_cap - min_aer);
        }
    }

    void sanity_checks() const
    {
        const std::size_t nR = static_cast<std::size_t>(num_racks);
        const std::size_t nP = static_cast<std::size_t>(num_products);
        const std::size_t nA = static_cast<std::size_t>(num_aisles);
        const std::size_t nK = static_cast<std::size_t>(num_orders);

        if (rack_capacity.size() != nR)
            die("rack_capacity size mismatch");
        if (product_circuit_raw.size() != nP)
            die("product_circuit size mismatch");
        if (aisles_racks.size() != nA)
            die("aisles_racks size mismatch");
        if (orders.size() != nK)
            die("orders size mismatch");
        if (adjacency.empty())
            die("adjacency matrix missing");
    }
};

static std::vector<int> compute_frequency(const PreparedData &P)
{
    std::vector<int> freq(static_cast<std::size_t>(P.num_products), 0);
    for (const auto &ord : P.orders)
        for (int p : ord)
            if (0 <= p && p < P.num_products)
                freq[idx(p, "product")]++;
    return freq;
}

static std::vector<double> build_product_score(const PreparedData &P)
{
    /*
     * Prefer PMET popularity_score if available.
     */
    auto it = P.I.product_metrics.find("popularity_score");
    if (it != P.I.product_metrics.end())
    {
        if ((int)it->second.size() != P.num_products)
            die("PMET popularity_score bad size");
        return it->second;
    }

    /*
     * Fallback: use frequency of appearance in orders.
     */
    const auto freq = compute_frequency(P);
    std::vector<double> score(static_cast<std::size_t>(P.num_products), 0.0);
    for (int p = 0; p < P.num_products; ++p)
    {
        const std::size_t pp = idx(p, "product");
        score[pp] = (double)freq[pp];
    }
    return score;
}

static bool rack_is_usable(const PreparedData &P, int r)
{
    return (0 <= r && r < P.num_racks && P.rack_capacity[idx(r, "rack")] > 0);
}

/*
 * Circuit-wise greedy placement:
 * - Circuits are processed in 0..C-1 after normalization.
 * - For each circuit, reserve a contiguous rack-id interval by scanning increasing rack ids.
 * - Enforce rack capacity and per-aisle aeration budgets.
 * - Assign products in decreasing score order according to the reserved per-rack quantities.
 */
std::vector<int> heuristic_greedy_circuit(const PreparedData &P)
{
    std::vector<int> assignment(static_cast<std::size_t>(P.num_products), -1);

    std::vector<int> rem_rack(static_cast<std::size_t>(P.num_racks), 0);
    for (int r = 0; r < P.num_racks; ++r)
        rem_rack[idx(r, "rack")] = P.rack_capacity[idx(r, "rack")];

    std::vector<int> rem_aisle(static_cast<std::size_t>(P.num_aisles), 0);
    for (int a = 0; a < P.num_aisles; ++a)
        rem_aisle[idx(a, "aisle")] = P.aisle_usable_capacity[idx(a, "aisle")];

    const auto score = build_product_score(P);

    /* Next circuit starts scanning from this rack id (chained intervals). */
    int current_rack = 0;

    for (int c = 0; c < P.num_circuits; ++c)
    {
        const auto &products_ref = P.products_by_circuit[idx(c, "circuit")];
        if (products_ref.empty())
            continue;

        std::vector<int> products = products_ref;

        std::sort(products.begin(), products.end(), [&](int a, int b)
                  {
            const double sa = score[idx(a, "product")];
            const double sb = score[idx(b, "product")];
            if (sa != sb)
                return sa > sb;
            return a < b; });

        const int need = (int)products.size();
        int remaining = need;

        int L = -1, R = -1;
        std::vector<int> take_per_rack(static_cast<std::size_t>(P.num_racks), 0);

        /*
         * (1) Reserve a contiguous interval [L, R] by scanning rack ids.
         */
        for (int r = current_rack; r < P.num_racks && remaining > 0; ++r)
        {
            if (!rack_is_usable(P, r))
                continue;

            const int a = P.rack_to_aisle[idx(r, "rack")];
            if (a < 0 || a >= P.num_aisles)
                continue;

            const std::size_t rr = idx(r, "rack");
            const std::size_t aa = idx(a, "aisle");

            const int can_rack = rem_rack[rr];
            const int can_aisle = rem_aisle[aa];
            const int take = std::min({can_rack, can_aisle, remaining});
            if (take <= 0)
                continue;

            if (L == -1)
                L = r;
            R = r;

            take_per_rack[rr] = take;
            rem_rack[rr] -= take;
            rem_aisle[aa] -= take;
            remaining -= take;
        }

        if (remaining > 0 || L == -1)
            die("Cannot place circuit " + std::to_string(c) + " (capacity/aeration)");

        /* Touching is allowed: next circuit starts at R. */
        current_rack = R;

        /*
         * (2) Assign products according to take_per_rack.
         */
        std::size_t idx_prod = 0;
        const std::size_t need_sz = products.size();
        for (int r = L; r <= R && idx_prod < need_sz; ++r)
        {
            const int take = take_per_rack[idx(r, "rack")];
            for (int t = 0; t < take; ++t)
            {
                const int p = products[idx_prod++];
                assignment[idx(p, "product")] = r;
            }
        }
        if (idx_prod != need_sz)
            die("Internal error: reservation ok but assignment incomplete");
    }

    /*
     * Final completeness check: every product must be assigned.
     */
    for (int p = 0; p < P.num_products; ++p)
        if (assignment[idx(p, "product")] < 0)
            die("Unassigned product " + std::to_string(p));

    return assignment;
}

static double compute_solution_cost(const PreparedData &P, const std::vector<int> &assignment)
{
    const int START = 0;
    const int END = P.num_racks - 1;

    double total_cost = 0.0;

    for (const auto &order : P.orders)
    {
        std::vector<int> visited;
        visited.reserve(order.size());

        for (int p : order)
        {
            if (!(0 <= p && p < P.num_products))
                die("Invalid product id in orders for cost");
            const int r = assignment[idx(p, "product")];
            if (r <= 0 || r >= END)
                continue;
            visited.push_back(r);
        }

        if (visited.empty())
        {
            total_cost += P.adjacency[idx(START, "rack")][idx(END, "rack")];
            continue;
        }

        std::sort(visited.begin(), visited.end());
        visited.erase(std::unique(visited.begin(), visited.end()), visited.end());

        int prev = START;
        for (int r : visited)
        {
            total_cost += P.adjacency[idx(prev, "rack")][idx(r, "rack")];
            prev = r;
        }
        total_cost += P.adjacency[idx(prev, "rack")][idx(END, "rack")];
    }

    return total_cost;
}

int main(int argc, char **argv)
{
    /*
     * Modes:
     *  - long:  heuristic_greedy_circuit <instance_dat> <solution_dir>
     *  - short: heuristic_greedy_circuit <toy|market|family|category|instance_dir>
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

    auto first_existing = [](const std::vector<fs::path> &cands) -> fs::path
    {
        for (const auto &p : cands)
            if (fs::exists(p))
                return p;
        return fs::path();
    };

    if (argc == 3)
    {
        instance_dat = fs::path(argv[1]);
        solution_dir = fs::path(argv[2]);
    }
    else
    {
        const std::string key(argv[1]);

        /*
         * Locate the project root by walking up until a "data/" directory is found.
         */
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

        if (key == "toy")
            inst_dir = first_existing({data_dir / "warehouse_toy", data_dir / "wharehouse_toy"});
        else if (key == "market")
            inst_dir = first_existing({
                data_dir / "wharehouse_big_market",
                data_dir / "warehouse_big_market",
                data_dir / "wharehouse_big_" / "wharehouse_big_market",
                data_dir / "warehouse_big_" / "warehouse_big_market",
            });
        else if (key == "family")
            inst_dir = first_existing({
                data_dir / "wharehouse_big_family",
                data_dir / "warehouse_big_family",
                data_dir / "wharehouse_big_" / "wharehouse_big_family",
                data_dir / "warehouse_big_" / "warehouse_big_family",
            });
        else if (key == "category")
            inst_dir = first_existing({
                data_dir / "wharehouse_big_category",
                data_dir / "warehouse_big_category",
                data_dir / "wharehouse_big_" / "wharehouse_big_category",
                data_dir / "warehouse_big_" / "warehouse_big_category",
            });
        else
        {
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

    /*
     * Named output file (useful when comparing multiple heuristics).
     */
    const fs::path out_path = solution_dir / "rack_product_assignment_greedy_circuit_idiot.txt";

    try
    {
        auto t0 = Clock::now();

        fs::create_directories(solution_dir);
        if (!fs::exists(instance_dat))
            die("Missing instance.dat: " + instance_dat.string());

        Instance instance = load_instance_dat(instance_dat.string());
        PreparedData prepared(instance);

        auto assignment = heuristic_greedy_circuit(prepared);
        const double cost = compute_solution_cost(prepared, assignment);

        write_solution_file(out_path.string(), assignment);

        auto t1 = Clock::now();
        const double ms = std::chrono::duration<double, std::milli>(t1 - t0).count();
        std::cout << "heuristic=greedy_circuit"
                  << " cost=" << cost
                  << " time_ms=" << ms
                  << " output=" << out_path.filename().string()
                  << " status=written\n";
        return 0;
    }
    catch (const std::exception &e)
    {
        std::cerr << "ERROR: " << e.what() << "\n";
        return 1;
    }
}
