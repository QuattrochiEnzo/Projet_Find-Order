#include "../common/instance.hpp"
#include "../common/solution.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

/* Gurobi */
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

/* ------------------------------------------------------------
 * Warm-start reader (robust)
 *
 * Input handling:
 *   - Ignore empty lines and comments ("#" or "//").
 *   - Supported formats:
 *       (A) 1 column  : r         (one line per product p=0..P-1)
 *       (B) 2 columns : p r
 *   - Reject if any product remains unassigned after parsing.
 * ------------------------------------------------------------ */

static std::vector<int> read_warm_start_assignment_any(const fs::path &path, int num_products)
{
    std::ifstream in(path);
    if (!in)
        die("Cannot open warm-start file: " + path.string());

    std::vector<int> assignment((std::size_t)num_products, -1);
    std::string line;
    int onecol_count = 0;

    auto trim = [](std::string &s)
    {
        while (!s.empty() && (s.back() == '\r' || s.back() == '\n' || s.back() == ' ' || s.back() == '\t'))
            s.pop_back();
        std::size_t i = 0;
        while (i < s.size() && (s[i] == ' ' || s[i] == '\t'))
            ++i;
        if (i)
            s.erase(0, i);
    };

    while (std::getline(in, line))
    {
        trim(line);
        if (line.empty())
            continue;
        if (line.rfind("#", 0) == 0)
            continue;
        if (line.rfind("//", 0) == 0)
            continue;

        std::istringstream iss(line);
        int a = -1, b = -1;
        if (!(iss >> a))
            continue;

        if (iss >> b)
        {
            /* Two-column format: "p r". */
            const int p = a;
            const int r = b;
            if (0 <= p && p < num_products)
                assignment[(std::size_t)p] = r;
        }
        else
        {
            /* One-column format: line i gives rack for product i. */
            if (onecol_count < num_products)
                assignment[(std::size_t)onecol_count] = a;
            onecol_count++;
        }
    }

    for (int p = 0; p < num_products; ++p)
        if (assignment[(std::size_t)p] < 0)
            die("Unrecognized warm-start format: " + path.string() +
                " (product " + std::to_string(p) + " missing)");

    return assignment;
}

/*
 * Runtime parameters controlled by CLI options.
 * Guardrails prevent accidental blow-ups on large instances.
 */
struct RunParams
{
    double time_limit_s = 30.0;
    int max_racks = 250;
    int max_orders = 500;
    bool verbose = true;

    bool enable_no_interleave = false;
    int arc_window = 0; /* 0 = no limit */

    /* Warm start */
    std::string warm_start_path = "";
    bool warm_start_set_a = true;
    int warm_start_cap_arcs = 0; /* if >0, cap visited racks per order when building a-start */
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
        else if (a == "--warm-start")
            P.warm_start_path = need("--warm-start");
        else if (a == "--warm-start-no-a")
            P.warm_start_set_a = false;
        else if (a == "--warm-start-cap-arcs")
            P.warm_start_cap_arcs = std::stoi(need("--warm-start-cap-arcs"));
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
    const std::vector<std::vector<int>> &adjacency;    /* [i][j] */
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
     * Precomputations:
     *   - Normalize product_circuit to 0-based indexing when needed.
     *   - Build rack_to_aisle mapping.
     *   - Group products by circuit.
     *   - Compute per-aisle capacities and aeration budgets.
     */
    void build_precomputations()
    {
        const std::size_t nR = (std::size_t)num_racks;
        const std::size_t nP = (std::size_t)num_products;
        const std::size_t nC = (std::size_t)num_circuits;
        const std::size_t nA = (std::size_t)num_aisles;

        /* Normalize product_circuit if exported as 1..C. */
        product_circuit = product_circuit_raw;
        if (product_circuit.size() != nP)
            die("product_circuit size mismatch");

        const int min_c = *std::min_element(product_circuit.begin(), product_circuit.end());
        const int max_c = *std::max_element(product_circuit.begin(), product_circuit.end());

        if (min_c == 1 && max_c == num_circuits)
            for (int &c : product_circuit)
                --c;

        /* Map racks to aisles when defined by aisles_racks. */
        rack_to_aisle.assign(nR, -1);
        for (std::size_t a = 0; a < nA; ++a)
        {
            for (int r_int : aisles_racks[a])
            {
                if (0 <= r_int && r_int < num_racks)
                    rack_to_aisle[(std::size_t)r_int] = (int)a;
            }
        }

        /* Group products by circuit. */
        products_by_circuit.assign(nC, {});
        for (std::size_t p = 0; p < nP; ++p)
        {
            int c = product_circuit[p];
            if (!(0 <= c && c < num_circuits))
                die("Invalid circuit id for product " + std::to_string(p) + ": " + std::to_string(product_circuit_raw[p]));
            products_by_circuit[(std::size_t)c].push_back((int)p);
        }

        /* Per-aisle aeration: alpha is interpreted as fraction or percent. */
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
     * Lightweight consistency checks.
     * Full validation remains the responsibility of the external checker.
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
 * Warm-start helpers
 * ------------------------------------------------------------ */

static void apply_warm_start(
    const PreparedData &P,
    const RunParams &params,
    const std::vector<int> &warm_assign,
    std::vector<std::vector<GRBVar>> &x,
    std::vector<std::vector<GRBVar>> &y,
    std::vector<std::vector<std::vector<GRBVar>>> &a,
    const std::vector<std::vector<std::vector<char>>> &has_a,
    std::vector<GRBVar> &L,
    std::vector<GRBVar> &U,
    std::vector<std::vector<GRBVar>> &delta)
{
    const std::size_t nR = (std::size_t)P.num_racks;
    const std::size_t nC = (std::size_t)P.num_circuits;
    const std::size_t nK = (std::size_t)P.num_orders;

    const std::size_t START = 0;
    const std::size_t END = nR - 1;

    if (warm_assign.empty())
        return;

    ScopedTimer tw("apply warm start");

    /*
     * (WS1) x start values: force x[p][r0] = 1 for the warm rack r0, 0 otherwise.
     */
    for (std::size_t p = 0; p < (std::size_t)P.num_products; ++p)
    {
        int r0 = warm_assign[p];
        for (std::size_t r = 0; r < nR; ++r)
            x[p][r].set(GRB_DoubleAttr_Start, (r == (std::size_t)r0) ? 1.0 : 0.0);
    }

    /*
     * (WS2) y start values: mark racks that contain at least one product from the order.
     */
    for (std::size_t k = 0; k < nK; ++k)
    {
        std::vector<char> visit(nR, 0);
        for (int p_int : P.orders[k])
        {
            if (!(0 <= p_int && p_int < P.num_products))
                continue;
            int r0 = warm_assign[(std::size_t)p_int];
            if (1 <= r0 && r0 <= (int)nR - 2)
                visit[(std::size_t)r0] = 1;
        }
        for (std::size_t r = 1; r + 1 < nR; ++r)
            y[k][r].set(GRB_DoubleAttr_Start, visit[r] ? 1.0 : 0.0);
    }

    /*
     * (WS3) L/U start values: compute circuit span [L, U] from the warm assignment.
     */
    std::vector<int> Ls((std::size_t)nC, 1), Us((std::size_t)nC, 1);
    for (std::size_t c = 0; c < nC; ++c)
    {
        int Lc = (int)nR, Uc = -1;
        for (int p_int : P.products_by_circuit[c])
        {
            int r0 = warm_assign[(std::size_t)p_int];
            if (1 <= r0 && r0 <= (int)nR - 2)
            {
                Lc = std::min(Lc, r0);
                Uc = std::max(Uc, r0);
            }
        }
        if (Uc < 0)
        {
            Lc = 1;
            Uc = 1;
        }
        Ls[c] = Lc;
        Us[c] = Uc;
        L[c].set(GRB_DoubleAttr_Start, (double)Lc);
        U[c].set(GRB_DoubleAttr_Start, (double)Uc);
    }

    /*
     * (WS4) delta start values: set delta[c][cp]=1 when circuit c ends before circuit cp starts.
     */
    for (std::size_t c = 0; c < nC; ++c)
        for (std::size_t cp = c + 1; cp < nC; ++cp)
        {
            double v = (Us[c] <= Ls[cp]) ? 1.0 : 0.0;
            delta[c][cp].set(GRB_DoubleAttr_Start, v);
        }

    /*
     * (WS5) a start values: build a monotone path START -> visited racks -> END.
     * This is optional and can be disabled with --warm-start-no-a.
     */
    if (params.warm_start_set_a)
    {
        for (std::size_t k = 0; k < nK; ++k)
        {
            std::vector<int> racks;
            racks.reserve(P.orders[k].size());
            for (int p_int : P.orders[k])
            {
                int r0 = warm_assign[(std::size_t)p_int];
                if (1 <= r0 && r0 <= (int)nR - 2)
                    racks.push_back(r0);
            }
            std::sort(racks.begin(), racks.end());
            racks.erase(std::unique(racks.begin(), racks.end()), racks.end());

            if (params.warm_start_cap_arcs > 0 && (int)racks.size() > params.warm_start_cap_arcs)
                racks.resize((std::size_t)params.warm_start_cap_arcs);

            std::size_t prev = START;
            for (int rr : racks)
            {
                std::size_t r = (std::size_t)rr;
                if (prev < r && has_a[k][prev][r])
                    a[k][prev][r].set(GRB_DoubleAttr_Start, 1.0);
                prev = r;
            }
            if (prev < END && has_a[k][prev][END])
                a[k][prev][END].set(GRB_DoubleAttr_Start, 1.0);
        }
    }
}

/* ------------------------------------------------------------
 * MILP
 * ------------------------------------------------------------ */

static std::vector<int> solve_with_gurobi(
    const PreparedData &P,
    const fs::path &solution_dir,
    const RunParams &params,
    const std::vector<int> &warm_assign)
{
    const std::size_t nR = (std::size_t)P.num_racks;
    const std::size_t nP = (std::size_t)P.num_products;
    const std::size_t nC = (std::size_t)P.num_circuits;
    const std::size_t nK = (std::size_t)P.num_orders;

    const std::size_t START = 0;
    const std::size_t END = nR - 1;

    if (nR < 2)
        die("Need at least 2 racks (START/END)");

    /* Guardrails: prevent accidental explosion on large instances. */
    if ((int)nR > params.max_racks)
        die("Instance too large: num_racks=" + std::to_string(nR) + " > max_racks=" + std::to_string(params.max_racks));
    if ((int)nK > params.max_orders)
        die("Instance too large: num_orders=" + std::to_string(nK) + " > max_orders=" + std::to_string(params.max_orders));

    std::cout << "[META] racks=" << nR << " products=" << nP << " circuits=" << nC << " orders=" << nK
              << " time_limit_s=" << params.time_limit_s
              << " warm_start=" << (!warm_assign.empty() ? "yes" : "no")
              << " arc_window=" << params.arc_window
              << "\n";

    ScopedTimer t_build("build gurobi model");

    GRBEnv env(true);
    env.set(GRB_IntParam_OutputFlag, params.verbose ? 1 : 0);
    env.start();

    GRBModel model(env);
    model.set(GRB_StringAttr_ModelName, "slotting2_milp");
    model.set(GRB_DoubleParam_TimeLimit, params.time_limit_s);
    model.set(GRB_StringParam_LogFile, (solution_dir / "gurobi.log").string());

    /* With warm start, focus on finding an incumbent quickly. */
    model.set(GRB_IntParam_MIPFocus, warm_assign.empty() ? 0 : 1);
    model.set(GRB_DoubleParam_Heuristics, 0.5);

    /* Variables */
    std::vector<std::vector<GRBVar>> x(nP, std::vector<GRBVar>(nR));
    std::vector<std::vector<GRBVar>> y(nK, std::vector<GRBVar>(nR));
    std::vector<std::vector<std::vector<GRBVar>>> a(nK);
    std::vector<std::vector<std::vector<char>>> has_a(nK);
    std::vector<GRBVar> L(nC), U(nC);
    std::vector<std::vector<GRBVar>> delta(nC);

    /* -----------------------
     * Variables
     * ----------------------- */
    {
        ScopedTimer tv("add variables");

        /* x[p][r] = 1 iff product p is assigned to rack r. */
        for (std::size_t p = 0; p < nP; ++p)
            for (std::size_t r = 0; r < nR; ++r)
                x[p][r] = model.addVar(0.0, 1.0, 0.0, GRB_BINARY, "x_" + std::to_string(p) + "_" + std::to_string(r));

        /* y[k][r] = 1 iff order k visits internal rack r. */
        for (std::size_t k = 0; k < nK; ++k)
            for (std::size_t r = 1; r + 1 < nR; ++r)
                y[k][r] = model.addVar(0.0, 1.0, 0.0, GRB_BINARY, "y_" + std::to_string(k) + "_" + std::to_string(r));

        /*
         * a[k][i][j] = 1 iff arc i->j is chosen for order k (i < j).
         * Arcs are restricted by arc_window, but i->END is always created.
         */
        for (std::size_t k = 0; k < nK; ++k)
        {
            a[k].resize(nR);
            has_a[k].assign(nR, std::vector<char>(nR, 0));

            for (std::size_t i = 0; i < nR; ++i)
            {
                a[k][i].resize(nR);

                std::size_t j_min = i + 1;
                std::size_t j_max = nR - 1;

                if (params.arc_window > 0)
                    j_max = std::min<std::size_t>(j_max, i + (std::size_t)params.arc_window);

                for (std::size_t j = j_min; j <= j_max && j < nR; ++j)
                {
                    a[k][i][j] = model.addVar(0.0, 1.0, (double)P.adjacency[i][j], GRB_BINARY,
                                              "a_" + std::to_string(k) + "_" + std::to_string(i) + "_" + std::to_string(j));
                    has_a[k][i][j] = 1;
                }

                /* Ensure arc i->END exists even if arc_window is small. */
                if (i < END && !has_a[k][i][END])
                {
                    a[k][i][END] = model.addVar(0.0, 1.0, (double)P.adjacency[i][END], GRB_BINARY,
                                                "a_" + std::to_string(k) + "_" + std::to_string(i) + "_" + std::to_string(END));
                    has_a[k][i][END] = 1;
                }
            }
        }

        /* L[c], U[c]: circuit span bounds in rack index (internal racks only). */
        for (std::size_t c = 0; c < nC; ++c)
        {
            L[c] = model.addVar(1.0, (double)(nR - 2), 0.0, GRB_INTEGER, "L_" + std::to_string(c));
            U[c] = model.addVar(1.0, (double)(nR - 2), 0.0, GRB_INTEGER, "U_" + std::to_string(c));
        }

        /* delta[c][cp]: ordering indicator between circuit intervals (c < cp). */
        for (std::size_t c = 0; c < nC; ++c)
        {
            delta[c].resize(nC);
            for (std::size_t cp = c + 1; cp < nC; ++cp)
                delta[c][cp] = model.addVar(0.0, 1.0, 0.0, GRB_BINARY,
                                            "delta_" + std::to_string(c) + "_" + std::to_string(cp));
        }

        model.update();
    }

    /* -----------------------
     * Constraints
     * ----------------------- */
    {
        ScopedTimer tc("add constraints");

        /* (1) Each product is assigned exactly once. */
        for (std::size_t p = 0; p < nP; ++p)
        {
            GRBLinExpr sum = 0;
            for (std::size_t r = 0; r < nR; ++r)
                sum += x[p][r];
            model.addConstr(sum == 1, "assign_" + std::to_string(p));
        }

        /* (2) Rack capacity (cap[r] may be 0, which forbids assignments). */
        for (std::size_t r = 0; r < nR; ++r)
        {
            GRBLinExpr load = 0;
            for (std::size_t p = 0; p < nP; ++p)
                load += x[p][r];
            model.addConstr(load <= P.rack_capacity[r], "cap_" + std::to_string(r));
        }

        /* (3) Link y and x (visit iff at least one ordered product is in the rack). */
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

        /* (4) Monotone path constraints on the DAG i < j, for each order k. */
        for (std::size_t k = 0; k < nK; ++k)
        {
            /* START outdegree = 1. */
            {
                GRBLinExpr out = 0;
                for (std::size_t j = START + 1; j < nR; ++j)
                    if (has_a[k][START][j])
                        out += a[k][START][j];
                model.addConstr(out == 1, "start_out_" + std::to_string(k));
            }

            /* END indegree = 1. */
            {
                GRBLinExpr in = 0;
                for (std::size_t i = 0; i < END; ++i)
                    if (has_a[k][i][END])
                        in += a[k][i][END];
                model.addConstr(in == 1, "end_in_" + std::to_string(k));
            }

            /* For each internal rack r: indeg(r) = outdeg(r) = y[k][r]. */
            for (std::size_t r = 1; r + 1 < nR; ++r)
            {
                GRBLinExpr in = 0, out = 0;

                for (std::size_t i = 0; i < r; ++i)
                    if (has_a[k][i][r])
                        in += a[k][i][r];

                for (std::size_t j = r + 1; j < nR; ++j)
                    if (has_a[k][r][j])
                        out += a[k][r][j];

                model.addConstr(in == y[k][r], "flow_in_" + std::to_string(k) + "_" + std::to_string(r));
                model.addConstr(out == y[k][r], "flow_out_" + std::to_string(k) + "_" + std::to_string(r));
            }
        }

        /* (5) Aeration per aisle: total assigned in the aisle <= usable capacity. */
        for (std::size_t a_id = 0; a_id < (std::size_t)P.num_aisles; ++a_id)
        {
            GRBLinExpr load = 0;
            for (int r_int : P.aisles_racks[a_id])
            {
                const std::size_t r = (std::size_t)r_int;
                for (std::size_t p = 0; p < nP; ++p)
                    load += x[p][r];
            }
            model.addConstr(load <= P.aisle_usable_capacity[a_id], "aeration_" + std::to_string(a_id));
        }

        /* (6) Circuit interval validity. */
        for (std::size_t c = 0; c < nC; ++c)
            model.addConstr(L[c] <= U[c], "L_le_U_" + std::to_string(c));

        /* (7) Link product placements to circuit interval [L, U]. */
        for (std::size_t c = 0; c < nC; ++c)
        {
            for (std::size_t p = 0; p < nP; ++p)
            {
                if (P.product_circuit[p] != (int)c)
                    continue;

                for (std::size_t r = 1; r < nR - 1; ++r)
                {
                    model.addConstr(L[c] <= (int)r + (int)(nR - 2) * (1 - x[p][r]),
                                    "Lc_le_r_" + std::to_string(c) + "_" + std::to_string(p) + "_" + std::to_string(r));
                    model.addConstr(U[c] >= (int)r - (int)(nR - 2) * (1 - x[p][r]),
                                    "Uc_ge_r_" + std::to_string(c) + "_" + std::to_string(p) + "_" + std::to_string(r));
                }
            }
        }

        /* (8) Enforce a total order of circuit intervals via delta. */
        for (std::size_t c = 0; c < nC; ++c)
        {
            for (std::size_t cp = c + 1; cp < nC; ++cp)
            {
                model.addConstr(U[c] <= L[cp] + (int)(nR - 2) * (1 - delta[c][cp]),
                                "ord1_" + std::to_string(c) + "_" + std::to_string(cp));
                model.addConstr(U[cp] <= L[c] + (int)(nR - 2) * delta[c][cp],
                                "ord2_" + std::to_string(c) + "_" + std::to_string(cp));
            }
        }
    }

    /* -----------------------
     * Warm start
     * ----------------------- */
    if (!warm_assign.empty())
        apply_warm_start(P, params, warm_assign, x, y, a, has_a, L, U, delta);

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

    /* Extract assignment from x. */
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

/* ------------------------------------------------------------
 * main(): resolve instance path and run
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

    /* Normalize common prefixes so that multiple naming conventions map to the same key. */
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
     *   <instance_dat> <solution_dir> [opts]
     *   <toy|market|family|category|instance_dir> [opts]
     */
    if (argc < 2)
    {
        std::cerr << "Usage:\n"
                  << "  " << argv[0] << " <instance_dat> <solution_dir> [--time-limit S] [--max-racks R] [--max-orders K] [--quiet] [--arc-window W]\n"
                  << "        [--warm-start PATH] [--warm-start-no-a] [--warm-start-cap-arcs T]\n"
                  << "  " << argv[0] << " <toy|market|family|category|instance_dir> [opts]\n";
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

        ScopedTimer t_all("solve with gurobi");

        Instance instance = load_instance_dat(instance_dat.string());
        PreparedData prepared(instance);

        /* Load warm start if provided. */
        std::vector<int> warm_assign;
        if (!params.warm_start_path.empty())
        {
            fs::path ws = fs::path(params.warm_start_path);
            if (!ws.is_absolute())
            {
                fs::path root = locate_project_root_or_die();
                ws = root / ws;
            }
            if (!fs::exists(ws))
                die("Warm-start path does not exist: " + ws.string());

            warm_assign = read_warm_start_assignment_any(ws, prepared.num_products);
            std::cout << "[INFO] warm-start loaded: " << ws.string() << "\n";
        }

        std::vector<int> assignment = solve_with_gurobi(prepared, solution_dir, params, warm_assign);

        const fs::path out_path = solution_dir / "rack_product_assignment_gurobi_model2.txt";
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
