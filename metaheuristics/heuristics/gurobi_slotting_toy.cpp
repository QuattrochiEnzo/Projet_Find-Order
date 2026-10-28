/*
 * gurobi_slotting_toy.cpp
 *
 * Exact MILP (toy) solver using Gurobi.
 *
 * CLI:
 *   (1) long:  gurobi_slotting_toy <instance_dat> <solution_dir> [--time-limit <sec>] [--max-racks <R>] [--max-orders <K>]
 *   (2) short: gurobi_slotting_toy <toy|market|family|category|instance_dir> [--time-limit <sec>] [--max-racks <R>] [--max-orders <K>]
 *
 * Output:
 *   <solution_dir>/rack_product_assignment_slotting_toy.txt
 *
 * Notes:
 *   - This solver is intentionally "toy" and does not scale to large instances.
 *   - The model may create variables/constraints in O(K * R^2) and/or O(C^2 * R^3).
 *   - Guardrails are provided (max-racks / max-orders) along with a TimeLimit and a LogFile.
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

#include "gurobi_c++.h"

using Clock = std::chrono::steady_clock;

static void die(const std::string &msg) { throw std::runtime_error(msg); }

namespace fs = std::filesystem;

/* ------------------------------------------------------------
 * Small utilities
 * ------------------------------------------------------------ */

struct ScopedTimer
{
    std::string name;
    Clock::time_point t0;

    explicit ScopedTimer(std::string n) : name(std::move(n)), t0(Clock::now())
    {
        std::cout << "[STEP] " << name << "...\n";
    }

    ~ScopedTimer()
    {
        auto t1 = Clock::now();
        double ms = std::chrono::duration<double, std::milli>(t1 - t0).count();
        std::cout << "[OK]   " << name << " (" << ms << " ms)\n";
    }
};

static int ceil_int(double x) { return (int)std::ceil(x - 1e-12); }

/*
 * Runtime parameters controlled by CLI options.
 * Guardrails (max_racks/max_orders) are meant to prevent accidental blow-ups.
 */
struct RunParams
{
    double time_limit_s = 30.0;
    int max_racks = 250;
    int max_orders = 500;
    bool verbose = true;

    bool enable_no_interleave = false;
    int arc_window = 0;
};

static RunParams parse_extra_args(int argc, char **argv, int start_idx)
{
    RunParams P;
    for (int i = start_idx; i < argc; ++i)
    {
        std::string a = argv[i];

        auto need = [&](const char *opt)
        {
            if (i + 1 >= argc)
                die(std::string("Missing value after ") + opt);
            return std::string(argv[++i]);
        };

        if (a == "--time-limit")
            P.time_limit_s = std::stod(need("--time-limit"));
        else if (a == "--max-racks")
            P.max_racks = std::stoi(need("--max-racks"));
        else if (a == "--max-orders")
            P.max_orders = std::stoi(need("--max-orders"));
        else if (a == "--quiet")
            P.verbose = false;
        else if (a == "--enable-no-interleave")
            P.enable_no_interleave = true;
        else if (a == "--arc-window")
            P.arc_window = std::stoi(need("--arc-window"));
        else
            die("Unknown option: " + a);
    }
    return P;
}

/* ------------------------------------------------------------
 * Prepared data (normalization + precomputations)
 * ------------------------------------------------------------ */

struct PreparedData
{
    const Instance &I;

    const int num_racks;
    const int num_products;
    const int num_circuits;
    const int num_aisles;
    const int num_orders;
    const double aeration_rate;

    const std::vector<int> &rack_capacity;             /* [rack] */
    const std::vector<int> &product_circuit_raw;       /* [product] */
    const std::vector<std::vector<int>> &aisles_racks; /* [aisle] -> racks */
    const std::vector<std::vector<int>> &adjacency;    /* [i][j] distance */
    const std::vector<std::vector<int>> &orders;       /* [order] -> products */

    /* Normalized helpers */
    std::vector<int> product_circuit;                  /* [product] in 0..C-1 */
    std::vector<int> rack_to_aisle;                    /* [rack] -> aisle */
    std::vector<std::vector<int>> products_by_circuit; /* [c] -> products */
    std::vector<int> aisle_total_capacity;             /* [aisle] */
    std::vector<int> aisle_min_aeration;               /* [aisle] */
    std::vector<int> aisle_usable_capacity;            /* [aisle] */

    explicit PreparedData(const Instance &inst)
        : I(inst),
          num_racks(inst.num_racks),
          num_products(inst.num_products),
          num_circuits(inst.num_circuits),
          num_aisles(inst.num_aisles),
          num_orders(inst.num_orders),
          aeration_rate(inst.aeration_rate),
          rack_capacity(inst.rack_capacity),
          product_circuit_raw(inst.product_circuit),
          aisles_racks(inst.aisles_racks),
          adjacency(inst.adjacency),
          orders(inst.orders)
    {
        ScopedTimer t("prepare data");
        build_precomputations();
        sanity_checks();
    }

private:
    /*
     * Build generic helper structures:
     *   - Normalize product_circuit to 0-based indexing when needed.
     *   - Map each rack to its aisle (when defined by aisles_racks).
     *   - Group products by circuit.
     *   - Compute per-aisle capacities and aeration budgets.
     */
    void build_precomputations()
    {
        const std::size_t nR = (std::size_t)num_racks;
        const std::size_t nP = (std::size_t)num_products;
        const std::size_t nC = (std::size_t)num_circuits;
        const std::size_t nA = (std::size_t)num_aisles;

        /*
         * 0) Normalize product_circuit to 0-based indexing.
         * Common exports use 1..C; we convert to 0..C-1 when detected.
         */
        product_circuit = product_circuit_raw;
        if (product_circuit.size() != nP)
            die("product_circuit size mismatch");

        const int min_c = *std::min_element(product_circuit.begin(), product_circuit.end());
        const int max_c = *std::max_element(product_circuit.begin(), product_circuit.end());

        if (min_c == 1 && max_c == num_circuits)
            for (int &c : product_circuit)
                --c;

        /* 1) Build rack_to_aisle mapping. */
        rack_to_aisle.assign(nR, -1);
        for (std::size_t a = 0; a < nA; ++a)
        {
            for (int r_int : aisles_racks[a])
            {
                if (0 <= r_int && r_int < num_racks)
                    rack_to_aisle[(std::size_t)r_int] = (int)a;
            }
        }

        /* 2) Group products by circuit. */
        products_by_circuit.assign(nC, {});
        for (std::size_t p = 0; p < nP; ++p)
        {
            int c = product_circuit[p];
            if (!(0 <= c && c < num_circuits))
            {
                die("Invalid circuit id for product " + std::to_string(p) +
                    ": " + std::to_string(product_circuit_raw[p]));
            }
            products_by_circuit[(std::size_t)c].push_back((int)p);
        }

        /*
         * 3) Compute per-aisle aeration budget.
         * aeration_rate can be provided as a fraction in [0,1] or as a percentage in [0,100].
         */
        aisle_total_capacity.assign(nA, 0);
        aisle_min_aeration.assign(nA, 0);
        aisle_usable_capacity.assign(nA, 0);

        const double alpha = (aeration_rate > 1.0 ? aeration_rate / 100.0 : aeration_rate);

        for (std::size_t a = 0; a < nA; ++a)
        {
            int total_cap = 0;
            for (int r_int : aisles_racks[a])
            {
                if (!(0 <= r_int && r_int < num_racks))
                    die("Invalid rack id in aisles_racks");
                total_cap += rack_capacity[(std::size_t)r_int];
            }
            aisle_total_capacity[a] = total_cap;
            aisle_min_aeration[a] = ceil_int(total_cap * alpha);
            aisle_usable_capacity[a] = std::max(0, total_cap - aisle_min_aeration[a]);
        }
    }

    /*
     * Lightweight structural checks to catch obvious inconsistencies early.
     * This does not replace the external checker.
     */
    void sanity_checks() const
    {
        const std::size_t nR = (std::size_t)num_racks;
        const std::size_t nP = (std::size_t)num_products;
        const std::size_t nA = (std::size_t)num_aisles;
        const std::size_t nK = (std::size_t)num_orders;

        if (rack_capacity.size() != nR)
            die("rack_capacity size mismatch");
        if (product_circuit_raw.size() != nP)
            die("product_circuit size mismatch");
        if (aisles_racks.size() != nA)
            die("aisles_racks size mismatch");
        if (orders.size() != nK)
            die("orders size mismatch");
        if (adjacency.size() != nR)
            die("adjacency rows mismatch");
        for (std::size_t i = 0; i < nR; ++i)
            if (adjacency[i].size() != nR)
                die("adjacency cols mismatch");
    }
};

/* ------------------------------------------------------------
 * MILP (toy)
 * ------------------------------------------------------------ */

static std::vector<int> solve_with_gurobi(const PreparedData &P, const fs::path &solution_dir, const RunParams &params)
{
    const std::size_t nR = (std::size_t)P.num_racks;
    const std::size_t nP = (std::size_t)P.num_products;
    const std::size_t nC = (std::size_t)P.num_circuits;
    const std::size_t nK = (std::size_t)P.num_orders;

    const std::size_t START = 0;
    const std::size_t END = (nR == 0 ? 0 : nR - 1);

    if (nR < 2)
        die("Need at least 2 racks (START/END)");

    /*
     * Guardrails: this model is not scalable.
     * The size can explode with O(K * R^2) arcs and O(C^2 * R^3) non-interleaving constraints.
     */
    if ((int)nR > params.max_racks)
    {
        die("Instance too large for toy MILP: num_racks=" + std::to_string(nR) +
            " > max_racks=" + std::to_string(params.max_racks) +
            ". Generate a smaller sub-instance, or increase --max-racks knowingly.");
    }
    if ((int)nK > params.max_orders)
    {
        die("Instance too large for toy MILP: num_orders=" + std::to_string(nK) +
            " > max_orders=" + std::to_string(params.max_orders) +
            ". Reduce orders, or increase --max-orders knowingly.");
    }

    std::cout << "[META] racks=" << nR << " products=" << nP << " circuits=" << nC << " orders=" << nK
              << " time_limit_s=" << params.time_limit_s << "\n";

    /* Precompute in_order[k][p]: binary membership of product p in order k. */
    std::vector<std::vector<char>> in_order(nK, std::vector<char>(nP, 0));
    for (std::size_t k = 0; k < nK; ++k)
        for (int p_int : P.orders[k])
        {
            if (!(0 <= p_int && p_int < P.num_products))
                die("Invalid product id in orders");
            in_order[k][(std::size_t)p_int] = 1;
        }

    ScopedTimer t_build("build gurobi model");

    GRBEnv env(true);
    env.set(GRB_IntParam_OutputFlag, params.verbose ? 1 : 0);
    env.start();

    GRBModel model(env);
    model.set(GRB_StringAttr_ModelName, "slotting_toy_milp");
    model.set(GRB_DoubleParam_TimeLimit, params.time_limit_s);
    model.set(GRB_StringParam_LogFile, (solution_dir / "gurobi.log").string());

    /* -----------------------
     * Variables
     * ----------------------- */
    {
        ScopedTimer tv("add variables");

        /*
         * x[p][r] = 1 iff product p is assigned to rack r.
         */
        std::vector<std::vector<GRBVar>> x(nP, std::vector<GRBVar>(nR));
        for (std::size_t p = 0; p < nP; ++p)
            for (std::size_t r = 0; r < nR; ++r)
                x[p][r] = model.addVar(0.0, 1.0, 0.0, GRB_BINARY,
                                       "x_" + std::to_string(p) + "_" + std::to_string(r));

        /*
         * y[k][r] = 1 iff order k visits rack r (internal racks only).
         */
        std::vector<std::vector<GRBVar>> y(nK, std::vector<GRBVar>(nR));
        for (std::size_t k = 0; k < nK; ++k)
            for (std::size_t r = 1; r + 1 < nR; ++r)
                y[k][r] = model.addVar(0.0, 1.0, 0.0, GRB_BINARY,
                                       "y_" + std::to_string(k) + "_" + std::to_string(r));

        /*
         * a[k][i][j] = 1 iff arc i->j (i < j) is selected for order k.
         * Objective coefficient is the distance P.adjacency[i][j].
         *
         * Warning: O(K * R^2) variables.
         */
        std::vector<std::vector<std::vector<GRBVar>>> a(nK);
        for (std::size_t k = 0; k < nK; ++k)
        {
            a[k].resize(nR);
            for (std::size_t i = 0; i < nR; ++i)
            {
                a[k][i].resize(nR);
                for (std::size_t j = i + 1; j < nR; ++j)
                    a[k][i][j] = model.addVar(0.0, 1.0, (double)P.adjacency[i][j], GRB_BINARY,
                                              "a_" + std::to_string(k) + "_" + std::to_string(i) + "_" + std::to_string(j));
            }
        }

        /*
         * z[c][r] = 1 iff circuit c is present in rack r (internal racks only).
         */
        std::vector<std::vector<GRBVar>> z(nC, std::vector<GRBVar>(nR));
        for (std::size_t c = 0; c < nC; ++c)
            for (std::size_t r = 1; r + 1 < nR; ++r)
                z[c][r] = model.addVar(0.0, 1.0, 0.0, GRB_BINARY,
                                       "z_" + std::to_string(c) + "_" + std::to_string(r));

        model.update();

        /* -----------------------
         * Constraints
         * ----------------------- */
        {
            ScopedTimer tc("add constraints");

            /*
             * (1) Each product is assigned exactly once.
             */
            for (std::size_t p = 0; p < nP; ++p)
            {
                GRBLinExpr sum = 0;
                for (std::size_t r = 0; r < nR; ++r)
                    sum += x[p][r];
                model.addConstr(sum == 1, "assign_" + std::to_string(p));
            }

            /*
             * (2) Rack capacity constraint.
             */
            for (std::size_t r = 0; r < nR; ++r)
            {
                GRBLinExpr load = 0;
                for (std::size_t p = 0; p < nP; ++p)
                    load += x[p][r];
                model.addConstr(load <= P.rack_capacity[r], "cap_" + std::to_string(r));
            }

            /*
             * (3) Link order visits y and assignments x:
             * y[k][r] = 1 iff rack r contains at least one product from order k.
             */
            for (std::size_t k = 0; k < nK; ++k)
            {
                for (std::size_t r = 1; r + 1 < nR; ++r)
                {
                    GRBLinExpr sum_x = 0;
                    for (int p_int : P.orders[k])
                    {
                        const std::size_t p = (std::size_t)p_int;
                        model.addConstr(y[k][r] >= x[p][r],
                                        "y_ge_x_" + std::to_string(k) + "_" + std::to_string(p) + "_" + std::to_string(r));
                        sum_x += x[p][r];
                    }
                    model.addConstr(y[k][r] <= sum_x,
                                    "y_le_sumx_" + std::to_string(k) + "_" + std::to_string(r));
                }
            }

            /*
             * (4) Monotone path on the DAG i < j for each order k:
             *  - START outdegree = 1
             *  - END indegree   = 1
             *  - For internal racks r: indeg(r) = outdeg(r) = y[k][r]
             */
            for (std::size_t k = 0; k < nK; ++k)
            {
                {
                    GRBLinExpr out = 0;
                    for (std::size_t j = START + 1; j < nR; ++j)
                        out += a[k][START][j];
                    model.addConstr(out == 1, "start_out_" + std::to_string(k));
                }
                {
                    GRBLinExpr in = 0;
                    for (std::size_t i = 0; i < END; ++i)
                        in += a[k][i][END];
                    model.addConstr(in == 1, "end_in_" + std::to_string(k));
                }
                for (std::size_t r = 1; r + 1 < nR; ++r)
                {
                    GRBLinExpr in = 0, out = 0;
                    for (std::size_t i = 0; i < r; ++i)
                        in += a[k][i][r];
                    for (std::size_t j = r + 1; j < nR; ++j)
                        out += a[k][r][j];
                    model.addConstr(in == y[k][r], "flow_in_" + std::to_string(k) + "_" + std::to_string(r));
                    model.addConstr(out == y[k][r], "flow_out_" + std::to_string(k) + "_" + std::to_string(r));
                }
            }

            /*
             * (5) Aeration per aisle: total assigned in aisle <= usable_capacity[a].
             */
            for (std::size_t a_id = 0; a_id < (std::size_t)P.num_aisles; ++a_id)
            {
                GRBLinExpr load = 0;
                for (int r_int : P.aisles_racks[a_id])
                {
                    const std::size_t r = (std::size_t)r_int;
                    for (std::size_t p = 0; p < nP; ++p)
                        load += x[p][r];
                }
                model.addConstr(load <= P.aisle_usable_capacity[a_id],
                                "aeration_" + std::to_string(a_id));
            }

            /*
             * (6) Link z and x for each circuit:
             * z[c][r] = 1 iff at least one product of circuit c is placed in rack r.
             */
            for (std::size_t c = 0; c < nC; ++c)
            {
                for (std::size_t r = 1; r + 1 < nR; ++r)
                {
                    GRBLinExpr sum_x = 0;
                    for (int p_int : P.products_by_circuit[c])
                        sum_x += x[(std::size_t)p_int][r];

                    model.addConstr(z[c][r] <= sum_x,
                                    "z_le_sumx_" + std::to_string(c) + "_" + std::to_string(r));

                    for (int p_int : P.products_by_circuit[c])
                        model.addConstr(z[c][r] >= x[(std::size_t)p_int][r],
                                        "z_ge_x_" + std::to_string(c) + "_" + std::to_string(p_int) + "_" + std::to_string(r));
                }
            }

            /*
             * (7) Non-interleaving (toy / very expensive): forbid c -- c' -- c.
             * Warning: O(C^2 * R^3) constraints. This is a demonstrator only.
             */
            for (std::size_t c = 0; c < nC; ++c)
                for (std::size_t cp = 0; cp < nC; ++cp)
                {
                    if (cp == c)
                        continue;
                    for (std::size_t r1 = 1; r1 + 2 < nR; ++r1)
                        for (std::size_t r2 = r1 + 1; r2 + 1 < nR; ++r2)
                            for (std::size_t r3 = r2 + 1; r3 + 1 < nR; ++r3)
                                model.addConstr(z[c][r1] + z[cp][r2] + z[c][r3] <= 2,
                                                "no_interleave_" + std::to_string(c) + "_" + std::to_string(cp) + "_" +
                                                    std::to_string(r1) + "_" + std::to_string(r2) + "_" + std::to_string(r3));
                }
        }

        /* -----------------------
         * Optimize
         * ----------------------- */
        model.set(GRB_IntAttr_ModelSense, GRB_MINIMIZE);
        {
            ScopedTimer to("optimize");
            model.optimize();
        }

        const int status = model.get(GRB_IntAttr_Status);
        if (status != GRB_OPTIMAL && status != GRB_TIME_LIMIT && status != GRB_SUBOPTIMAL)
            die("Gurobi ended with status=" + std::to_string(status));

        /*
         * Extract assignment from x: for each product p, pick the rack r with x[p][r] = 1.
         */
        std::vector<int> assignment(nP, -1);
        for (std::size_t p = 0; p < nP; ++p)
        {
            for (std::size_t r = 0; r < nR; ++r)
                if (x[p][r].get(GRB_DoubleAttr_X) > 0.5)
                {
                    assignment[p] = (int)r;
                    break;
                }
            if (assignment[p] < 0)
                die("No rack selected for product " + std::to_string(p));
        }
        return assignment;
    }
}

/* ------------------------------------------------------------
 * main(): resolve instance path and run the solver
 * ------------------------------------------------------------ */

static fs::path locate_project_root_or_die()
{
    fs::path root = fs::current_path();
    for (int up = 0; up < 10; ++up)
    {
        if (fs::exists(root / "data"))
            return root;
        if (!root.has_parent_path())
            break;
        root = root.parent_path();
    }
    die("Cannot locate project root (missing data/ in parents). Run from project tree.");
    return {};
}

static fs::path resolve_alias_or_dir(const fs::path &data_dir, const std::string &key_raw)
{
    std::string key = key_raw;

    auto starts_with = [](const std::string &s, const std::string &p)
    { return s.rfind(p, 0) == 0; };

    /*
     * Normalize common prefixes so that keys like "warehouse_big_market" also match "market".
     */
    if (starts_with(key, "warehouse_big_"))
        key = key.substr(std::string("warehouse_big_").size());
    if (starts_with(key, "wharehouse_big_"))
        key = key.substr(std::string("wharehouse_big_").size());
    if (starts_with(key, "big_"))
        key = key.substr(4);
    if (starts_with(key, "warehouse_"))
        key = key.substr(std::string("warehouse_").size());
    if (starts_with(key, "wharehouse_"))
        key = key.substr(std::string("wharehouse_").size());

    auto pick_first_existing = [&](const std::vector<fs::path> &cands) -> fs::path
    {
        for (const auto &c : cands)
            if (fs::exists(c))
                return c;
        return {};
    };

    if (key == "toy")
    {
        fs::path inst = pick_first_existing({data_dir / "warehouse_toy", data_dir / "wharehouse_toy"});
        if (inst.empty())
            die("Alias 'toy' not found under data/");
        return inst;
    }
    if (key == "market" || key == "family" || key == "category")
    {
        const std::string base = "warehouse_big_" + key;
        const std::string base2 = "wharehouse_big_" + key;

        fs::path inst = pick_first_existing({
            data_dir / base,
            data_dir / base2,
            data_dir / "warehouse_big_" / base,
            data_dir / "wharehouse_big_" / base2,
            data_dir / "warehouse_big_" / ("warehouse_big_" + key),
            data_dir / "wharehouse_big_" / ("wharehouse_big_" + key),
        });

        if (inst.empty())
            die("Alias '" + key + "' not found under data/");
        return inst;
    }

    /* Otherwise: treat as a directory path (absolute or relative). */
    fs::path try_dir = fs::path(key_raw);
    if (!try_dir.is_absolute())
        try_dir = fs::absolute(try_dir);
    if (!fs::exists(try_dir))
        die("Unknown instance key '" + key_raw + "' and directory not found: " + try_dir.string());
    return try_dir;
}

int main(int argc, char **argv)
{
    /*
     * Accepted forms:
     *   gurobi_slotting_toy <instance_dat> <solution_dir> [opts]
     *   gurobi_slotting_toy <alias_or_instance_dir>       [opts]
     */
    if (argc < 2)
    {
        std::cerr << "Usage:\n"
                  << "  " << argv[0] << " <instance_dat> <solution_dir> [--time-limit S] [--max-racks R] [--max-orders K] [--quiet]\n"
                  << "  " << argv[0] << " <toy|market|family|category|instance_dir> [--time-limit S] [--max-racks R] [--max-orders K] [--quiet]\n";
        return 2;
    }

    fs::path instance_dat;
    fs::path solution_dir;

    try
    {
        RunParams params;

        if (argc >= 3 && fs::path(argv[1]).extension() == ".dat")
        {
            /* Long mode: <instance_dat> <solution_dir> [opts...] */
            instance_dat = fs::path(argv[1]);
            solution_dir = fs::path(argv[2]);
            params = parse_extra_args(argc, argv, 3);
        }
        else
        {
            /* Short mode: <alias_or_dir> [opts...] */
            const std::string key = argv[1];
            params = parse_extra_args(argc, argv, 2);

            fs::path root = locate_project_root_or_die();
            fs::path data_dir = root / "data";

            fs::path inst_dir = resolve_alias_or_dir(data_dir, key);
            instance_dat = inst_dir / "instance.dat";
            solution_dir = inst_dir / "solutions";
        }

        fs::create_directories(solution_dir);
        if (!fs::exists(instance_dat))
            die("Missing instance.dat: " + instance_dat.string());

        ScopedTimer t_all("solve with gurobi (toy)");

        Instance instance = load_instance_dat(instance_dat.string());
        PreparedData prepared(instance);

        std::vector<int> assignment = solve_with_gurobi(prepared, solution_dir, params);

        /*
         * IMPORTANT: write the standard filename expected by the pipeline.
         */
        const fs::path out_path = solution_dir / "rack_product_assignment_slotting_toy.txt";
        write_solution_file(out_path.string(), assignment);

        std::cout << "solver=gurobi"
                  << " output=" << out_path.string()
                  << " log=" << (solution_dir / "gurobi.log").string()
                  << " status=written\n";
        return 0;
    }
    catch (const GRBException &e)
    {
        std::cerr << "GRBException code=" << e.getErrorCode()
                  << " msg=" << e.getMessage() << "\n";
        return 1;
    }
    catch (const std::exception &e)
    {
        std::cerr << "ERROR: " << e.what() << "\n";
        return 1;
    }
}
