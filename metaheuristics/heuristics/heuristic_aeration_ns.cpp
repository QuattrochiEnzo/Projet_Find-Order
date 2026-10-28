// cpp/heuristics/heuristic_aeration_ns.cpp
/**
 * @file heuristic_aeration_ns.cpp
 * @brief Post-optimization neighborhood search that improves aeration by relocating products locally.
 *
 * Pipeline (black-box style):
 *   1) Load exported instance (.dat)                                            // load instance
 *   2) Load an initial feasible solution (assignment vector)                    // load init
 *   3) Run a relocation-based local search (optionally with SA / epsilon)       // improve
 *   4) Write the best assignment encountered to an output solution file         // write out
 *
 * Notes:
 * - This heuristic NEVER changes aisle-level slack if --within-aisle=1          // preserve aeration
 * - With --safe-interval=1, a product is only relocated within its circuit span // preserve contiguity span
 * - The solution format is written using common/solution.hpp utilities          // keep format contract
 */

#include "../common/instance.hpp"
#include "../common/solution.hpp"

#include <algorithm>
#include <chrono>
#include <cmath> // std::exp
#include <filesystem>
#include <fstream>
#include <iostream>
#include <random>
#include <stdexcept>
#include <string>
#include <vector>

namespace fs = std::filesystem;
using Clock = std::chrono::steady_clock;

// ============================================================
// Small utilities
// ============================================================

static void die(const std::string &msg) { throw std::runtime_error(msg); } // fail fast

static std::size_t idx(int i, const char *what)
{
    if (i < 0)
        die(std::string("Negative index for ") + what); // guard
    return static_cast<std::size_t>(i);                 // cast once
}

static double clamp01(double x)
{
    if (x < 0.0)
        return 0.0; // clamp low
    if (x > 1.0)
        return 1.0; // clamp high
    return x;       // pass-through
}

// ============================================================
// Acceptance policy (epsilon / simulated annealing)
// ============================================================

/** @brief Acceptance configuration for uphill moves. */
struct AcceptConfig
{
    double epsilon_frac = 0.0; // allow small uphill moves: delta <= eps*cur_cost
    bool sa = false;           // enable simulated annealing acceptance
    double T0_frac = 0.01;     // T0 = T0_frac * cur_cost
    double Tmin_frac = 1e-6;   // Tmin = Tmin_frac * cur_cost
    double cooling = 0.995;    // T <- T * cooling
};

static double init_temperature(double cur_cost, const AcceptConfig &cfg)
{
    if (!cfg.sa)
        return 0.0;                                 // SA disabled
    return std::max(1e-12, cfg.T0_frac * cur_cost); // avoid zero
}

static double update_temperature(double T, double cur_cost, const AcceptConfig &cfg)
{
    if (!cfg.sa)
        return 0.0;                                                // SA disabled
    const double Tmin = std::max(1e-12, cfg.Tmin_frac * cur_cost); // dynamic floor
    return std::max(Tmin, T * cfg.cooling);                        // geometric cool
}

static bool accept_move(double cur_cost, double new_cost, const AcceptConfig &cfg, double T, std::mt19937 &rng)
{
    if (new_cost <= cur_cost)
        return true; // always accept improving

    const double delta = new_cost - cur_cost; // uphill gap

    if (cfg.epsilon_frac > 0.0)
    {
        const double eps = cfg.epsilon_frac * cur_cost; // relative tolerance
        if (delta <= eps)
            return true; // accept "almost equal"
    }

    if (cfg.sa && T > 0.0)
    {
        const double p = std::exp(-delta / T);              // Metropolis prob
        std::uniform_real_distribution<double> U(0.0, 1.0); // uniform draw
        return U(rng) < clamp01(p);                         // accept w.p. p
    }

    return false; // strict reject
}

// ============================================================
// Neighborhood search configuration
// ============================================================

/** @brief Neighborhood search parameters. */
struct NSConfig
{
    int iters = 50'000;        // number of outer iterations
    int k_candidates = 2'000;  // attempts per iteration to find a feasible move
    unsigned seed = 0;         // RNG seed
    bool within_aisle = true;  // keep moves within the same aisle
    bool safe_interval = true; // keep circuit span unchanged: r_to in [L_c, R_c]
};

// ============================================================
// State representation (solution + incremental helpers)
// ============================================================

struct State
{
    const Instance &I; // instance handle
    int P, R, C, A;    // sizes

    std::vector<int> assignment;                    // [p] -> r
    std::vector<int> load;                          // [r] -> nb products
    std::vector<std::vector<int>> products_in_rack; // [r] -> list(p)
    std::vector<int> rack_to_aisle;                 // [r] -> aisle or -1
    std::vector<int> product_to_circuit;            // [p] -> circuit 0..C-1
    std::vector<int> circuit_L, circuit_R;          // [c] -> interval bounds

    static std::vector<int> normalize_product_circuit(const std::vector<int> &raw, int num_circuits)
    {
        if (raw.empty())
            return {};                                    // allow empty
        std::vector<int> pc = raw;                        // copy
        int mn = *std::min_element(pc.begin(), pc.end()); // detect base
        int mx = *std::max_element(pc.begin(), pc.end()); // detect base

        if (mn == 1 && mx == num_circuits) // 1-indexed -> 0-indexed
            for (int &c : pc)
                --c;

        for (int c : pc)
            if (!(0 <= c && c < num_circuits))
                die("product_circuit invalid after normalization");

        return pc; // normalized mapping
    }

    explicit State(const Instance &inst, std::vector<int> a)
        : I(inst),
          P(inst.num_products), R(inst.num_racks), C(inst.num_circuits), A(inst.num_aisles),
          assignment(std::move(a)),
          load(static_cast<std::size_t>(R), 0),
          products_in_rack(static_cast<std::size_t>(R)),
          rack_to_aisle(static_cast<std::size_t>(R), -1),
          product_to_circuit(normalize_product_circuit(inst.product_circuit, inst.num_circuits)),
          circuit_L(static_cast<std::size_t>(C), R),
          circuit_R(static_cast<std::size_t>(C), -1)
    {
        // Build rack->aisle lookup                                              // map racks to aisles
        for (int aidx = 0; aidx < A; ++aidx)
            for (int r : I.aisles_racks[idx(aidx, "aisle")])
                if (0 <= r && r < R)
                    rack_to_aisle[idx(r, "rack")] = aidx;

        // Build rack loads + inverted lists                                     // compute occupancy
        for (int p = 0; p < P; ++p)
        {
            const int r = assignment[idx(p, "product")];
            if (!(0 <= r && r < R))
                die("Invalid rack id in solution");
            products_in_rack[idx(r, "rack")].push_back(p);
            load[idx(r, "rack")] += 1;
        }

        // Build circuit rack span [L,R]                                         // compute spans
        for (int p = 0; p < P; ++p)
        {
            const int c = product_to_circuit[idx(p, "product")];
            const int r = assignment[idx(p, "product")];
            circuit_L[idx(c, "circuit")] = std::min(circuit_L[idx(c, "circuit")], r);
            circuit_R[idx(c, "circuit")] = std::max(circuit_R[idx(c, "circuit")], r);
        }

        // Mark empty circuits consistently                                      // normalize empties
        for (int c = 0; c < C; ++c)
        {
            if (circuit_R[idx(c, "circuit")] < 0)
            {
                circuit_L[idx(c, "circuit")] = 0;
                circuit_R[idx(c, "circuit")] = -1;
            }
        }
    }
};

// ============================================================
// Solution IO
// ============================================================

/**
 * @brief Read an assignment vector from a solution file (expects first int = n_products).
 * @param path Path to input file.
 * @param n_products Expected number of products.
 * @return assignment[p] = rack_id
 */
static std::vector<int> read_solution_vector(const fs::path &path, int n_products)
{
    std::ifstream in(path);
    if (!in)
        die("Cannot open solution file: " + path.string()); // must exist

    int n = -1;
    in >> n;
    if (n != n_products)
        die("Solution n_products mismatch: expected " + std::to_string(n_products) +
            " got " + std::to_string(n));

    std::vector<int> a(static_cast<std::size_t>(n_products), -1);
    for (int p = 0; p < n_products; ++p)
        if (!(in >> a[idx(p, "product")]))
            die("Bad solution file (missing lines)");

    return a; // raw assignment
}

// ============================================================
// Diagnostics helpers (aeration-related proxies)
// ============================================================

static long long total_slack_capacity(const State &S)
{
    long long slack = 0;
    for (int r = 0; r < S.R; ++r)
    {
        const int cap = S.I.rack_capacity[idx(r, "rack")];
        if (cap <= 0)
            continue;                                                  // ignore blocked
        slack += static_cast<long long>(cap - S.load[idx(r, "rack")]); // add free slots
    }
    return slack;
}

static int count_slack_racks(const State &S)
{
    int cnt = 0;
    for (int r = 0; r < S.R; ++r)
    {
        const int cap = S.I.rack_capacity[idx(r, "rack")];
        if (cap <= 0)
            continue; // ignore blocked
        if (S.load[idx(r, "rack")] < cap)
            ++cnt; // rack has slack
    }
    return cnt;
}

// ============================================================
// Move feasibility + application
// ============================================================

static bool can_relocate(const State &S, const NSConfig &cfg, int p, int r_to)
{
    const int r_from = S.assignment[idx(p, "product")];
    if (r_to == r_from)
        return false; // no-op
    if (!(0 <= r_to && r_to < S.R))
        return false; // bounds

    // Capacity constraint                                                     // keep feasible
    if (S.I.rack_capacity[idx(r_to, "rack")] <= 0)
        return false; // avoid blocked
    if (S.load[idx(r_to, "rack")] >= S.I.rack_capacity[idx(r_to, "rack")])
        return false;

    // Stay within same aisle (keeps aisle slack unchanged)                    // preserve aeration
    if (cfg.within_aisle)
    {
        const int a1 = S.rack_to_aisle[idx(r_from, "rack")];
        const int a2 = S.rack_to_aisle[idx(r_to, "rack")];
        if (a1 < 0 || a2 < 0)
            return false;
        if (a1 != a2)
            return false;
    }

    // Keep circuit span unchanged: r_to must stay within [L,R]                // preserve span
    if (cfg.safe_interval)
    {
        const int c = S.product_to_circuit[idx(p, "product")];
        const int L = S.circuit_L[idx(c, "circuit")];
        const int R = S.circuit_R[idx(c, "circuit")];
        if (R < 0)
            return false; // empty circuit
        if (!(L <= r_to && r_to <= R))
            return false;
    }

    return true;
}

static void apply_relocate(State &S, int p, int r_to)
{
    const int r_from = S.assignment[idx(p, "product")];
    if (r_from == r_to)
        return; // no-op

    // Remove p from its current rack list                                     // update inverse map
    auto &from = S.products_in_rack[idx(r_from, "rack")];
    auto it = std::find(from.begin(), from.end(), p);
    if (it == from.end())
        die("Internal error: product not in its rack list");
    *it = from.back();
    from.pop_back();

    // Add p to destination rack list                                          // update inverse map
    S.products_in_rack[idx(r_to, "rack")].push_back(p);

    // Update assignment + loads                                               // maintain consistency
    S.assignment[idx(p, "product")] = r_to;
    S.load[idx(r_from, "rack")] -= 1;
    S.load[idx(r_to, "rack")] += 1;

    // Circuit intervals deliberately unchanged under safe_interval mode       // keep invariant
}

// ============================================================
// Local search core
// ============================================================

static void local_search_aeration(State &S, const NSConfig &cfg, const AcceptConfig &acc)
{
    std::mt19937 rng(cfg.seed); // deterministic RNG

    // Cost function: START=0, END=R-1                                         // consistent with pipeline
    auto compute_cost = [&](const std::vector<int> &assign) -> double
    {
        const int START = 0;
        const int END = S.R - 1;

        double total = 0.0;
        for (const auto &ord : S.I.orders)
        {
            std::vector<int> visited;
            visited.reserve(ord.size()); // collect racks

            for (int p : ord)
            {
                if (!(0 <= p && p < S.P))
                    continue; // guard
                const int r = assign[idx(p, "product")];
                if (r <= 0 || r >= END)
                    continue; // ignore START/END/blocked
                visited.push_back(r);
            }

            if (visited.empty())
            {
                total += static_cast<double>(S.I.adjacency[idx(START, "rack")][idx(END, "rack")]);
                continue;
            }

            std::sort(visited.begin(), visited.end()); // order path
            visited.erase(std::unique(visited.begin(), visited.end()), visited.end());

            int prev = START;
            for (int r : visited)
            {
                total += static_cast<double>(S.I.adjacency[idx(prev, "rack")][idx(r, "rack")]);
                prev = r;
            }
            total += static_cast<double>(S.I.adjacency[idx(prev, "rack")][idx(END, "rack")]);
        }

        return total;
    };

    // Candidate racks: usable + belonging to an aisle                          // restrict domain
    std::vector<int> candidate_racks;
    candidate_racks.reserve(static_cast<std::size_t>(S.R));
    for (int r = 0; r < S.R; ++r)
        if (S.I.rack_capacity[idx(r, "rack")] > 0 && S.rack_to_aisle[idx(r, "rack")] >= 0)
            candidate_racks.push_back(r);

    if (candidate_racks.empty())
        return; // nothing to do
    std::uniform_int_distribution<int> dr(0, static_cast<int>(candidate_racks.size()) - 1);

    // Initialize current/best + SA schedule                                    // set baseline
    double cur_cost = compute_cost(S.assignment);
    double best_cost = cur_cost;
    std::vector<int> best_assignment = S.assignment;
    double T = init_temperature(cur_cost, acc);

    // Main loop: sample feasible moves, accept, track best                      // iterate improvements
    for (int it = 0; it < cfg.iters; ++it)
    {
        bool proposed = false;

        // Try to find one feasible relocation this iteration                   // bounded effort
        for (int t = 0; t < cfg.k_candidates; ++t)
        {
            const int r_from = candidate_racks[idx(dr(rng), "dr")];
            if (S.products_in_rack[idx(r_from, "rack")].empty())
                continue; // nothing to move

            const auto &from_list = S.products_in_rack[idx(r_from, "rack")];
            const int p = from_list[static_cast<std::size_t>(rng() % from_list.size())];

            const int r_to = candidate_racks[idx(dr(rng), "dr")];
            if (!can_relocate(S, cfg, p, r_to))
                continue; // reject infeasible

            proposed = true;

            // Apply candidate move                                              // modify state
            apply_relocate(S, p, r_to);

            // Evaluate move                                                     // compute exact cost
            const double new_cost = compute_cost(S.assignment);

            // Accept or rollback                                                // apply acceptance rule
            if (accept_move(cur_cost, new_cost, acc, T, rng))
            {
                cur_cost = new_cost; // commit
                if (cur_cost < best_cost)
                {
                    best_cost = cur_cost; // update best
                    best_assignment = S.assignment;
                }
            }
            else
            {
                apply_relocate(S, p, r_from); // rollback
            }

            break; // one move per iteration
        }

        T = update_temperature(T, cur_cost, acc); // cool schedule
        if (!proposed)
            break; // stop if stuck
    }

    S.assignment = std::move(best_assignment); // return best found
}

// ============================================================
// CLI
// ============================================================

int main(int argc, char **argv)
{
    try
    {
        if (argc < 4)
        {
            std::cerr
                << "Usage:\n"
                << "  " << argv[0]
                << " [--iters N] [--k K] [--seed S] [--within-aisle 0|1] [--safe-interval 0|1]\n"
                << "  " << argv[0]
                << " [--epsilon-frac x] [--sa 0|1] [--t0-frac x] [--tmin-frac x] [--cooling x]\n"
                << "  " << argv[0]
                << " [--out FILE]\n"
                << "  <instance_dat> <solution_in> <solution_dir>\n";
            return 2;
        }

        NSConfig cfg;                 // NS params
        AcceptConfig acc;             // accept params
        std::vector<std::string> pos; // positional args
        fs::path out_file_override;   // optional output file

        for (int i = 1; i < argc; ++i)
        {
            const std::string arg = argv[i];

            auto need = [&](const char *opt) -> std::string
            {
                if (i + 1 >= argc)
                    die(std::string("Missing value after ") + opt);
                return std::string(argv[++i]);
            };

            // Neighborhood search parameters                                   // parse NS
            if (arg == "--iters")
                cfg.iters = std::stoi(need("--iters"));
            else if (arg == "--k")
                cfg.k_candidates = std::stoi(need("--k"));
            else if (arg == "--seed")
                cfg.seed = static_cast<unsigned>(std::stoul(need("--seed")));
            else if (arg == "--within-aisle")
                cfg.within_aisle = (std::stoi(need("--within-aisle")) != 0);
            else if (arg == "--safe-interval")
                cfg.safe_interval = (std::stoi(need("--safe-interval")) != 0);

            // Acceptance parameters                                             // parse acceptance
            else if (arg == "--epsilon-frac")
                acc.epsilon_frac = std::stod(need("--epsilon-frac"));
            else if (arg == "--sa")
                acc.sa = (std::stoi(need("--sa")) != 0);
            else if (arg == "--t0-frac")
                acc.T0_frac = std::stod(need("--t0-frac"));
            else if (arg == "--tmin-frac")
                acc.Tmin_frac = std::stod(need("--tmin-frac"));
            else if (arg == "--cooling")
                acc.cooling = std::stod(need("--cooling"));

            // Output control                                                    // avoid hard-coded names
            else if (arg == "--out")
                out_file_override = fs::path(need("--out"));

            // Positional args                                                   // collect paths
            else
                pos.push_back(arg);
        }

        // Validate configuration                                                // enforce invariants
        if (cfg.iters <= 0)
            die("iters must be > 0");
        if (cfg.k_candidates <= 0)
            die("k must be > 0");
        if (acc.epsilon_frac < 0.0)
            die("epsilon-frac must be >= 0");

        if (acc.sa)
        {
            if (acc.T0_frac <= 0.0)
                die("t0-frac must be > 0 when sa=1");
            if (acc.Tmin_frac <= 0.0)
                die("tmin-frac must be > 0 when sa=1");
            if (!(0.0 < acc.cooling && acc.cooling < 1.0))
                die("cooling must be in (0,1)");
        }

        if (pos.size() != 3)
            die("Expected 3 positional args: <instance_dat> <solution_in> <solution_dir>");

        // Resolve inputs/outputs                                                // prepare FS
        const fs::path instance_dat = pos[0];
        const fs::path solution_in = pos[1];
        const fs::path solution_dir = pos[2];
        fs::create_directories(solution_dir);

        // Load instance + init solution                                         // bootstrap state
        const Instance I = load_instance_dat(instance_dat.string());
        auto init_assignment = read_solution_vector(solution_in, I.num_products);
        State S(I, std::move(init_assignment));

        // Quick diagnostics (aeration proxy)                                    // print info
        std::cout << "slack_racks=" << count_slack_racks(S)
                  << " total_slack=" << total_slack_capacity(S) << "\n";

        // Run local search                                                      // optimize
        const auto t0 = Clock::now();
        local_search_aeration(S, cfg, acc);
        const auto t1 = Clock::now();
        const double ms = std::chrono::duration<double, std::milli>(t1 - t0).count();

        // Write solution                                                        // persist
        fs::path out_path;
        if (!out_file_override.empty())
            out_path = out_file_override; // user-specified
        else
            out_path = solution_dir / "rack_product_assignment_aeration_ns.txt";

        write_solution_file(out_path.string(), S.assignment);

        // Summary logging                                                       // standard log line
        std::cout << "heuristic=aeration_ns"
                  << " iters=" << cfg.iters
                  << " k=" << cfg.k_candidates
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
