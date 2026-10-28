// python tools/main.py --instance market --use-corrmix

/*
 * heuristic_greedy_circuit_smart.cpp
 *
 * HEURISTIC: "Smart" greedy with recovery, left-pack, and post-optimization
 * -------------------------------------------------------------------------
 * This solver places products circuit-by-circuit, aiming for a trade-off between:
 * - item popularity (static importance)
 * - correlation / co-occurrence (items frequently bought together)
 *
 * MAIN FEATURES:
 * 1) SMART PLACEMENT
 * - Uses a mixed score controlled by alpha to balance popularity and
 *   dynamic proximity bonuses (co-occurring products/circuits).
 * - Enforces strict circuit contiguity (via reserved racks) and per-aisle aeration.
 *
 * 2) RECOVERY STRATEGIES (robustness)
 * - If a circuit cannot be placed due to fragmentation, the algorithm triggers
 *   successive compaction passes:
 *     a) Local compaction (window w = 10)
 *     b) Medium compaction (window w = 25)
 *     c) Global compaction
 *     d) Final "Left-Pack" (push everything to the left) to remove holes.
 *
 * 3) LOCAL SEARCH (post-optimization)
 * - Final swap phase between circuits of identical sizes.
 * - Uses a sparse adjacency structure to speed up gain computations on large instances.
 *
 * CLI USAGE:
 *   ./heuristic_greedy_circuit_smart [options] <instance_dat> <solution_dir>
 */

#include "../common/instance.hpp"
#include "../common/solution.hpp"
#include "../common/heuristic_utils.hpp"
#include <algorithm>
#include <map>
#include <unordered_map>
#include <chrono>
#include <cmath>
#include <filesystem>
#include <iostream>
#include <numeric>
#include <stdexcept>
#include <string>
#include <vector>

using Clock = std::chrono::steady_clock;
namespace fs = std::filesystem;

enum class OrderDir
{
    Asc,
    Desc
};

enum class CircuitOrderMode
{
    ByScore,
    ById
};

enum class ProductScoreMode
{
    Auto,
    Actionable,
    Popularity,
    Turnover,
    Frequency,
    CorrMix // alpha*corr + (1-alpha)*popularity
};

static ProductScoreMode parse_product_score_mode(const std::string &s)
{
    if (s == "auto")
        return ProductScoreMode::Auto;
    if (s == "actionable")
        return ProductScoreMode::Actionable;
    if (s == "popularity")
        return ProductScoreMode::Popularity;
    if (s == "turnover")
        return ProductScoreMode::Turnover;
    if (s == "freq")
        return ProductScoreMode::Frequency;
    if (s == "corrmix")
        return ProductScoreMode::CorrMix;
    die("Invalid --product-score (expected auto|actionable|popularity|turnover|freq|corrmix), got: " + s);
    return ProductScoreMode::Auto;
}

static const char *product_score_tag(ProductScoreMode m)
{
    switch (m)
    {
    case ProductScoreMode::Auto:
        return "auto";
    case ProductScoreMode::Actionable:
        return "actionable";
    case ProductScoreMode::Popularity:
        return "popularity";
    case ProductScoreMode::Turnover:
        return "turnover";
    case ProductScoreMode::Frequency:
        return "freq";
    case ProductScoreMode::CorrMix:
        return "corrmix";
    }
    return "auto";
}

struct GreedyConfig
{
    OrderDir product_dir = OrderDir::Desc;
    CircuitOrderMode circuit_mode = CircuitOrderMode::ByScore;
    OrderDir circuit_dir = OrderDir::Desc;

    ProductScoreMode product_score = ProductScoreMode::Auto;

    // Mixed score: alpha*corr + (1-alpha)*popularity (after normalization)
    double alpha_corr = 0.7;
};

static OrderDir parse_dir(const std::string &s, const char *what)
{
    if (s == "asc")
        return OrderDir::Asc;
    if (s == "desc")
        return OrderDir::Desc;
    die(std::string("Invalid ") + what + " (expected asc|desc), got: " + s);
    return OrderDir::Desc;
}

static CircuitOrderMode parse_circuit_mode(const std::string &s)
{
    if (s == "score")
        return CircuitOrderMode::ByScore;
    if (s == "id")
        return CircuitOrderMode::ById;
    die("Invalid --circuit-order (expected score|id), got: " + s);
    return CircuitOrderMode::ByScore;
}

struct PreparedData
{
    const Instance &I;

    const int num_racks;
    const int num_products;
    const int num_circuits;
    const int num_aisles;
    const int num_orders;

    // Aeration standardized as a fraction in [0,1]
    const double aeration_frac;

    const std::vector<int> &rack_capacity;             // [rack]
    const std::vector<int> &product_circuit_raw;       // [product]
    const std::vector<std::vector<int>> &aisles_racks; // [aisle] -> racks
    const std::vector<std::vector<int>> &adjacency;    // [i][j]
    const std::vector<std::vector<int>> &orders;       // [order] -> products

    // Normalized helpers
    std::vector<int> product_circuit;                  // [product] 0..C-1
    std::vector<std::vector<int>> products_by_circuit; // [circuit] -> products
    std::vector<int> aisle_total_capacity;             // [aisle]
    std::vector<int> aisle_min_aeration;               // [aisle]
    std::vector<int> aisle_usable_capacity;            // [aisle]
    std::vector<int> rack_to_aisle;                    // [rack] -> aisle or -1

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
    void build_precomputations()
    {
        const std::size_t nP = static_cast<std::size_t>(num_products);
        const std::size_t nC = static_cast<std::size_t>(num_circuits);
        const std::size_t nA = static_cast<std::size_t>(num_aisles);

        /*
         * Normalize product_circuit:
         * input may be 0-based (0..C-1) or 1-based (1..C).
         * After normalization: 0..C-1.
         */
        if (product_circuit_raw.size() != nP)
            die("product_circuit size mismatch (precompute)");
        product_circuit = normalize_product_circuit(product_circuit_raw, num_circuits);

        /*
         * Build products_by_circuit[c] = list of products belonging to circuit c.
         */
        products_by_circuit.assign(nC, {});
        for (std::size_t p = 0; p < nP; ++p)
        {
            const int c = product_circuit[p];
            products_by_circuit[idx(c, "circuit")].push_back(static_cast<int>(p));
        }

        /*
         * Aeration per aisle:
         * usable_capacity[a] = total_capacity[a] - ceil(total_capacity[a] * aeration_frac).
         */
        aisle_total_capacity.assign(nA, 0);
        aisle_min_aeration.assign(nA, 0);
        aisle_usable_capacity.assign(nA, 0);

        for (std::size_t a = 0; a < nA; ++a)
        {
            int total_cap = 0;
            for (int r : aisles_racks[a])
            {
                if (!(0 <= r && r < num_racks))
                    die("Invalid rack id in aisles_racks");
                total_cap += rack_capacity[idx(r, "rack")];
            }
            aisle_total_capacity[a] = total_cap;
            const int min_aer = ceil_int(total_cap * aeration_frac);
            aisle_min_aeration[a] = min_aer;
            aisle_usable_capacity[a] = std::max(0, total_cap - min_aer);
        }

        /*
         * Build rack_to_aisle mapping (first aisle occurrence wins).
         */
        rack_to_aisle.assign((std::size_t)num_racks, -1);
        for (int a = 0; a < num_aisles; ++a)
            for (int r : aisles_racks[idx(a, "aisle")])
                if (0 <= r && r < num_racks && rack_to_aisle[idx(r, "rack")] < 0)
                    rack_to_aisle[idx(r, "rack")] = a;
    }

    void sanity_checks() const
    {
        if ((int)rack_capacity.size() != num_racks)
            die("rack_capacity size mismatch");
        if ((int)product_circuit_raw.size() != num_products)
            die("product_circuit size mismatch");
        if ((int)aisles_racks.size() != num_aisles)
            die("aisles_racks size mismatch");
        if ((int)orders.size() != num_orders)
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

static std::vector<double> build_product_score(const PreparedData &P, ProductScoreMode mode, double alpha_corr)
{
    auto get_metric = [&](const std::string &name) -> const std::vector<double> *
    {
        auto it = P.I.product_metrics.find(name);
        if (it == P.I.product_metrics.end())
            return nullptr;
        if ((int)it->second.size() != P.num_products)
            die("PMET " + name + " bad size");
        return &it->second;
    };

    /*
     * Forced modes: actionable / popularity / turnover / frequency.
     */
    if (mode == ProductScoreMode::Actionable)
    {
        if (auto v = get_metric("actionable_score"))
            return *v;
        die("Requested --product-score actionable but PMET actionable_score is missing in instance.dat");
    }
    if (mode == ProductScoreMode::Popularity)
    {
        if (auto v = get_metric("popularity_score"))
            return *v;
        die("Requested --product-score popularity but PMET popularity_score is missing in instance.dat");
    }
    if (mode == ProductScoreMode::Turnover)
    {
        if (auto v = get_metric("turnover_score"))
            return *v;
        die("Requested --product-score turnover but PMET turnover_score is missing in instance.dat");
    }
    if (mode == ProductScoreMode::Frequency)
    {
        const auto freq = compute_frequency(P);
        std::vector<double> score((std::size_t)P.num_products, 0.0);
        for (int p = 0; p < P.num_products; ++p)
            score[idx(p, "product")] = (double)freq[idx(p, "product")];
        return score;
    }

    /*
     * NEW: CorrMix = alpha*corr + (1-alpha)*popularity (after min-max normalization).
     */
    if (mode == ProductScoreMode::CorrMix)
    {
        const auto *pop = get_metric("popularity_score");
        const auto *corr = get_metric("product_correlation_score");
        if (!pop)
            die("Requested --product-score corrmix but PMET popularity_score is missing in instance.dat");
        if (!corr)
            die("Requested --product-score corrmix but PMET product_correlation_score is missing in instance.dat");

        const double a = clamp01(alpha_corr);

        const auto pop01 = minmax01(*pop);
        const auto corr01 = minmax01(*corr);

        std::vector<double> score((std::size_t)P.num_products, 0.0);
        for (int p = 0; p < P.num_products; ++p)
        {
            score[idx(p, "product")] =
                a * corr01[idx(p, "product")] + (1.0 - a) * pop01[idx(p, "product")];
        }
        return score;
    }

    /*
     * Auto mode (historical behavior):
     * - prefer actionable_score if present
     * - else popularity_score
     * - else fallback to frequency
     */
    if (auto v = get_metric("actionable_score"))
        return *v;
    if (auto v = get_metric("popularity_score"))
        return *v;

    const auto freq = compute_frequency(P);
    std::vector<double> score((std::size_t)P.num_products, 0.0);
    for (int p = 0; p < P.num_products; ++p)
        score[idx(p, "product")] = (double)freq[idx(p, "product")];
    return score;
}

static std::vector<double> build_aisle_score(const PreparedData &P)
{
    /*
     * Prefer AMET aisle_entry_exit_score if present.
     */
    if (auto it = P.I.aisle_metrics.find("aisle_entry_exit_score"); it != P.I.aisle_metrics.end())
    {
        if ((int)it->second.size() != P.num_aisles)
            die("AMET aisle_entry_exit_score bad size");
        return it->second;
    }

    /*
     * Fallback: estimate aisle quality by START->anchor + anchor->END,
     * where anchor is the smallest internal rack id in the aisle.
     */
    const int START = 0;
    const int END = P.num_racks - 1;

    std::vector<double> score(static_cast<std::size_t>(P.num_aisles), 0.0);
    for (int a = 0; a < P.num_aisles; ++a)
    {
        const auto &racks = P.aisles_racks[idx(a, "aisle")];
        if (racks.empty())
        {
            score[idx(a, "aisle")] = 1e18;
            continue;
        }

        int anchor = racks[0];
        for (int r : racks)
        {
            if (r == START || r == END)
                continue;
            anchor = std::min(anchor, r);
        }

        score[idx(a, "aisle")] =
            (double)P.adjacency[idx(START, "rack")][idx(anchor, "rack")] +
            (double)P.adjacency[idx(anchor, "rack")][idx(END, "rack")];
    }
    return score;
}

[[maybe_unused]] static std::vector<int> build_circuit_order(const PreparedData &P, const std::vector<double> &prod_score, const GreedyConfig &cfg)
{
    std::vector<int> order(static_cast<std::size_t>(P.num_circuits), 0);
    std::iota(order.begin(), order.end(), 0);

    /*
     * If ordering is ById, we simply iterate in increasing or decreasing circuit id.
     */
    if (cfg.circuit_mode == CircuitOrderMode::ById)
    {
        if (cfg.circuit_dir == OrderDir::Desc)
            std::reverse(order.begin(), order.end());
        return order;
    }

    /*
     * ByScore: circuit importance = sum of its product scores.
     */
    std::vector<double> circuit_score(static_cast<std::size_t>(P.num_circuits), 0.0);
    for (int c = 0; c < P.num_circuits; ++c)
    {
        double s = 0.0;
        for (int p : P.products_by_circuit[idx(c, "circuit")])
            s += prod_score[idx(p, "product")];
        circuit_score[idx(c, "circuit")] = s;
    }

    std::sort(order.begin(), order.end(), [&](int a, int b)
              {
                  const double sa = circuit_score[idx(a, "c")], sb = circuit_score[idx(b, "c")];
                  if (sa != sb)
                      return (cfg.circuit_dir == OrderDir::Desc) ? (sa > sb) : (sa < sb);
                  return a < b; });

    return order;
}

// ============================================================
// Interval placement (contiguity-safe)
// ============================================================

struct IntervalChoice
{
    int L = -1;
    int R = -1;
    double score = 1e100;
};

struct PlacedCircuit
{
    int c = -1;
    int L = -1;
    int R = -1;
};

enum class PlaceMode
{
    ScoreFirst, // prioritize placement quality
    BestFit,    // minimize interval length
    LeftFirst   // minimize L (pack to the left)
};

static IntervalChoice choose_interval_scorefirst(
    const PreparedData &P,
    const std::vector<int> &rem_rack,
    const std::vector<int> &rem_aisle,
    const std::vector<int> &reserved_by,
    const std::vector<double> &aisle_score,
    int demand)
{
    const int R = P.num_racks;
    IntervalChoice best;

    for (int L = 0; L < R; ++L)
    {
        if (reserved_by[idx(L, "rack")] != -1)
            continue;

        std::vector<int> tmp_rem_aisle = rem_aisle;

        int cap_sum = 0;
        double score_sum = 0.0;
        int score_w = 0;

        for (int r = L; r < R; ++r)
        {
            if (reserved_by[idx(r, "rack")] != -1)
                break;

            int a = P.rack_to_aisle[idx(r, "rack")];
            if (a >= 0)
            {
                const int rr = rem_rack[idx(r, "rack")];
                const int aa = tmp_rem_aisle[idx(a, "aisle")];
                const int contrib = std::min(rr, aa);
                if (contrib > 0)
                {
                    cap_sum += contrib;
                    score_sum += aisle_score[idx(a, "aisle")] * (double)contrib;
                    score_w += contrib;
                    tmp_rem_aisle[idx(a, "aisle")] -= contrib;
                }
            }

            if (cap_sum >= demand)
            {
                const int len = r - L + 1;
                const double mean = (score_w > 0) ? (score_sum / (double)score_w) : 1e18;
                const double candidate = mean + 1e-4 * (double)len;

                if (candidate < best.score)
                {
                    best.L = L;
                    best.R = r;
                    best.score = candidate;
                }
                break;
            }
        }
    }
    return best;
}

static IntervalChoice choose_interval_bestfit(
    const PreparedData &P,
    const std::vector<int> &rem_rack,
    const std::vector<int> &rem_aisle,
    const std::vector<int> &reserved_by,
    const std::vector<double> &aisle_score,
    int demand)
{
    const int R = P.num_racks;
    IntervalChoice best;

    for (int L = 0; L < R; ++L)
    {
        if (reserved_by[idx(L, "rack")] != -1)
            continue;

        std::vector<int> tmp_rem_aisle = rem_aisle;
        int cap_sum = 0;

        for (int r = L; r < R; ++r)
        {
            if (reserved_by[idx(r, "rack")] != -1)
                break;

            int a = P.rack_to_aisle[idx(r, "rack")];
            if (a >= 0)
            {
                const int rr = rem_rack[idx(r, "rack")];
                const int aa = tmp_rem_aisle[idx(a, "aisle")];
                const int contrib = std::min(rr, aa);
                if (contrib > 0)
                {
                    cap_sum += contrib;
                    tmp_rem_aisle[idx(a, "aisle")] -= contrib;
                }
            }

            if (cap_sum >= demand)
            {
                const int len = r - L + 1;

                double qual = 0.0;
                int qw = 0;
                for (int x = L; x <= r; ++x)
                {
                    int ax = P.rack_to_aisle[idx(x, "rack")];
                    if (ax >= 0)
                    {
                        qual += aisle_score[idx(ax, "aisle")];
                        qw += 1;
                    }
                }
                const double mean = (qw > 0) ? (qual / (double)qw) : 1e18;

                const double candidate = (double)len + 1e-4 * (double)L + 1e-6 * mean;

                if (candidate < best.score)
                {
                    best.L = L;
                    best.R = r;
                    best.score = candidate;
                }
                break;
            }
        }
    }

    return best;
}

static IntervalChoice choose_interval_leftfirst(
    const PreparedData &P,
    const std::vector<int> &rem_rack,
    const std::vector<int> &rem_aisle,
    const std::vector<int> &reserved_by,
    const std::vector<double> &aisle_score,
    int demand)
{
    const int R = P.num_racks;
    IntervalChoice best;

    for (int L = 0; L < R; ++L)
    {
        if (reserved_by[idx(L, "rack")] != -1)
            continue;

        std::vector<int> tmp_rem_aisle = rem_aisle;
        int cap_sum = 0;

        for (int r = L; r < R; ++r)
        {
            if (reserved_by[idx(r, "rack")] != -1)
                break;

            int a = P.rack_to_aisle[idx(r, "rack")];
            if (a >= 0)
            {
                const int rr = rem_rack[idx(r, "rack")];
                const int aa = tmp_rem_aisle[idx(a, "aisle")];
                const int contrib = std::min(rr, aa);
                if (contrib > 0)
                {
                    cap_sum += contrib;
                    tmp_rem_aisle[idx(a, "aisle")] -= contrib;
                }
            }

            if (cap_sum >= demand)
            {
                const int len = r - L + 1;

                double qual = 0.0;
                int qw = 0;
                for (int x = L; x <= r; ++x)
                {
                    int ax = P.rack_to_aisle[idx(x, "rack")];
                    if (ax >= 0)
                    {
                        qual += aisle_score[idx(ax, "aisle")];
                        qw += 1;
                    }
                }
                const double mean = (qw > 0) ? (qual / (double)qw) : 1e18;

                /*
                 * Left-first: minimize L first, then interval length, then add a tiny quality term.
                 */
                const double candidate = (double)L + 1e-4 * (double)len + 1e-6 * mean;

                if (candidate < best.score)
                {
                    best.L = L;
                    best.R = r;
                    best.score = candidate;
                }
                break;
            }
        }
    }

    return best;
}

static void unplace_circuit(
    const PreparedData &P,
    int c,
    const std::vector<int> &products,
    std::vector<int> &assignment,
    std::vector<int> &rem_rack,
    std::vector<int> &rem_aisle,
    std::vector<int> &reserved_by,
    int L,
    int R)
{
    /*
     * Remove all products of circuit c from the current assignment,
     * restoring rack and aisle remaining capacities.
     * Also clears the reserved interval [L,R] if provided.
     */
    for (int p : products)
    {
        int r = assignment[idx(p, "product")];
        if (r >= 0)
        {
            int a = P.rack_to_aisle[idx(r, "rack")];
            rem_rack[idx(r, "rack")] += 1;
            if (a >= 0)
                rem_aisle[idx(a, "aisle")] += 1;
            assignment[idx(p, "product")] = -1;
        }
    }

    if (L >= 0 && R >= 0)
    {
        for (int r = L; r <= R; ++r)
            if (reserved_by[idx(r, "rack")] == c)
                reserved_by[idx(r, "rack")] = -1;
    }
}

static bool place_circuit(
    const PreparedData &P,
    int c,
    const std::vector<int> &products_sorted,
    const std::vector<double> &aisle_score,
    std::vector<int> &assignment,
    std::vector<int> &rem_rack,
    std::vector<int> &rem_aisle,
    std::vector<int> &reserved_by,
    PlaceMode mode,
    PlacedCircuit &out_place)
{
    /*
     * Try to place all products of circuit c inside a single contiguous rack interval,
     * respecting both rack capacities and per-aisle aeration budgets.
     */
    const int demand = (int)products_sorted.size();

    IntervalChoice choice;
    if (mode == PlaceMode::BestFit)
        choice = choose_interval_bestfit(P, rem_rack, rem_aisle, reserved_by, aisle_score, demand);
    else if (mode == PlaceMode::LeftFirst)
        choice = choose_interval_leftfirst(P, rem_rack, rem_aisle, reserved_by, aisle_score, demand);
    else
        choice = choose_interval_scorefirst(P, rem_rack, rem_aisle, reserved_by, aisle_score, demand);

    if (choice.L < 0)
        return false;

    int remaining = demand;
    std::size_t ip = 0;

    for (int r = choice.L; r <= choice.R; ++r)
    {
        if (remaining <= 0)
            break;

        int a = P.rack_to_aisle[idx(r, "rack")];
        if (a < 0)
            continue;

        const std::size_t rr = idx(r, "rack");
        const std::size_t aa = idx(a, "aisle");

        const int can = std::min(rem_rack[rr], rem_aisle[aa]);
        const int take = std::min(can, remaining);
        if (take <= 0)
            continue;

        for (int t = 0; t < take; ++t)
        {
            int p = products_sorted[ip++];
            assignment[idx(p, "product")] = r;
        }

        rem_rack[rr] -= take;
        rem_aisle[aa] -= take;
        remaining -= take;
    }

    if (remaining > 0)
    {
        /*
         * Placement failed: rollback assignments (without touching reservations yet).
         */
        unplace_circuit(P, c, products_sorted, assignment, rem_rack, rem_aisle, reserved_by, -1, -1);
        return false;
    }

    /*
     * Reserve the full interval to preserve contiguity for future placements.
     */
    for (int r = choice.L; r <= choice.R; ++r)
        reserved_by[idx(r, "rack")] = c;

    out_place = PlacedCircuit{c, choice.L, choice.R};
    return true;
}

static bool compactify_last_w(
    const PreparedData &P,
    int w,
    const std::vector<double> &aisle_score,
    const std::vector<std::vector<int>> &products_by_circuit_sorted,
    std::vector<PlacedCircuit> &placed,
    std::vector<int> &assignment,
    std::vector<int> &rem_rack,
    std::vector<int> &rem_aisle,
    std::vector<int> &reserved_by)
{
    /*
     * Repack only the last w placed circuits (local/medium/global recovery).
     * Steps:
     * 1) Unplace last w circuits (restore capacities and free reservations).
     * 2) Re-place them using BestFit to reduce fragmentation.
     */
    if (placed.empty())
        return true;

    int n = (int)placed.size();
    int start = std::max(0, n - w);

    for (int i = n - 1; i >= start; --i)
    {
        const PlacedCircuit pc = placed[idx(i, "i")];
        unplace_circuit(P, pc.c, products_by_circuit_sorted[idx(pc.c, "c")],
                        assignment, rem_rack, rem_aisle, reserved_by, pc.L, pc.R);
    }

    for (int i = start; i < n; ++i)
    {
        const int c = placed[static_cast<std::size_t>(i)].c;
        PlacedCircuit np;
        if (!place_circuit(P, c, products_by_circuit_sorted[idx(c, "c")], aisle_score,
                           assignment, rem_rack, rem_aisle, reserved_by,
                           PlaceMode::BestFit, np))
        {
            return false;
        }
        placed[static_cast<std::size_t>(i)] = np;
    }

    return true;
}

static bool left_pack_all(
    const PreparedData &P,
    const std::vector<double> &aisle_score,
    const std::vector<std::vector<int>> &products_by_circuit_sorted,
    std::vector<PlacedCircuit> &placed,
    std::vector<int> &assignment,
    std::vector<int> &rem_rack,
    std::vector<int> &rem_aisle,
    std::vector<int> &reserved_by)
{
    /*
     * Ultimate recovery: unplace everything, then re-place all circuits using LeftFirst
     * to push intervals as far left as possible and eliminate holes.
     */
    if (placed.empty())
        return true;

    for (int i = (int)placed.size() - 1; i >= 0; --i)
    {
        const PlacedCircuit pc = placed[idx(static_cast<std::size_t>(i), "i")];
        unplace_circuit(P, pc.c, products_by_circuit_sorted[idx(pc.c, "c")],
                        assignment, rem_rack, rem_aisle, reserved_by, pc.L, pc.R);
    }

    for (std::size_t i = 0; i < placed.size(); ++i)
    {
        const int c = placed[i].c;
        PlacedCircuit np;
        if (!place_circuit(P, c, products_by_circuit_sorted[idx(c, "c")], aisle_score,
                           assignment, rem_rack, rem_aisle, reserved_by,
                           PlaceMode::LeftFirst, np))
        {
            return false;
        }
        placed[i] = np;
    }
    return true;
}

/*
 * Neighborhood score (sparse version):
 * Evaluates how well a circuit is positioned w.r.t. correlated neighboring circuits
 * within a small radius in the current placed-order list.
 */
double calculate_neighborhood_score_sparse(int circuit_id, int current_pos,
                                           const std::vector<PlacedCircuit> &placed,
                                           const std::vector<std::unordered_map<int, double>> &adj_list)
{
    double score = 0.0;
    int radius = 5;
    int n = static_cast<int>(placed.size());

    const auto &my_correlations = adj_list[static_cast<std::size_t>(circuit_id)];
    if (my_correlations.empty())
        return 0.0;

    for (int i = std::max(0, current_pos - radius); i <= std::min(n - 1, current_pos + radius); ++i)
    {
        if (i == current_pos)
            continue;
        int neighbor_id = placed[static_cast<std::size_t>(i)].c;

        auto it = my_correlations.find(neighbor_id);
        if (it != my_correlations.end())
        {
            double dist = std::abs(i - current_pos);
            score += it->second / dist;
        }
    }
    return score;
}

// ============================================================
// Main greedy + recovery
// ============================================================

/*
 * GREEDY CIRCUIT SMART HEURISTIC
 * Goal: place product circuits to reduce picking cost by combining:
 * - popularity,
 * - co-occurrence (correlation),
 * - and warehouse geometry constraints.
 */
static std::vector<int> heuristic_greedy_circuit_smart(const PreparedData &P, const GreedyConfig &cfg)
{
    /*
     * STEP 1: build circuit-to-orders index for dynamic correlation updates.
     * circuit_to_orders[c] = list of orders that contain circuit c.
     */
    std::vector<std::vector<int>> circuit_to_orders(static_cast<size_t>(P.num_circuits));
    for (int o = 0; o < P.num_orders; ++o)
    {
        /* Avoid counting the same circuit twice within the same order. */
        std::vector<int> seen;
        for (int p : P.orders[static_cast<size_t>(o)])
        {
            int c = P.product_circuit[static_cast<size_t>(p)];
            if (std::find(seen.begin(), seen.end(), c) == seen.end())
            {
                circuit_to_orders[static_cast<size_t>(c)].push_back(o);
                seen.push_back(c);
            }
        }
    }

    /*
     * STEP 2: initialize state structures.
     */
    std::vector<int> assignment(static_cast<size_t>(P.num_products), -1);

    /* Remaining capacity per rack. */
    std::vector<int> rem_rack(static_cast<size_t>(P.num_racks));
    for (int r = 0; r < P.num_racks; ++r)
        rem_rack[static_cast<size_t>(r)] = P.rack_capacity[static_cast<size_t>(r)];

    /* Remaining aeration budget per aisle (max number of items allowed in that aisle). */
    std::vector<int> rem_aisle(static_cast<size_t>(P.num_aisles));
    for (int a = 0; a < P.num_aisles; ++a)
        rem_aisle[static_cast<size_t>(a)] = P.aisle_usable_capacity[static_cast<size_t>(a)];

    /* reserved_by[r] tells which circuit reserved rack r (to preserve contiguity). */
    std::vector<int> reserved_by(static_cast<size_t>(P.num_racks), -1);

    /* Product scores and aisle quality scores. */
    const auto prod_score = build_product_score(P, cfg.product_score, cfg.alpha_corr);
    const auto aisle_score = build_aisle_score(P);

    /*
     * STEP 3: compute static circuit base scores (sum of product scores).
     * Then normalize to [0,1] to avoid purely favoring large circuits.
     */
    std::vector<double> base_circuit_scores(static_cast<size_t>(P.num_circuits), 0.0);
    double max_base = 0.0;
    for (int c = 0; c < P.num_circuits; ++c)
    {
        for (int p : P.products_by_circuit[static_cast<size_t>(c)])
            base_circuit_scores[static_cast<size_t>(c)] += prod_score[static_cast<size_t>(p)];
        max_base = std::max(max_base, base_circuit_scores[static_cast<size_t>(c)]);
    }
    if (max_base > 0)
    {
        for (double &s : base_circuit_scores)
            s /= max_base;
    }

    /*
     * Sort products inside each circuit according to cfg.product_dir.
     */
    std::vector<std::vector<int>> products_by_circuit_sorted = P.products_by_circuit;
    for (int c = 0; c < P.num_circuits; ++c)
    {
        std::sort(products_by_circuit_sorted[static_cast<size_t>(c)].begin(),
                  products_by_circuit_sorted[static_cast<size_t>(c)].end(), [&](int a, int b)
                  {
            double sa = prod_score[static_cast<size_t>(a)];
            double sb = prod_score[static_cast<size_t>(b)];
            return (cfg.product_dir == OrderDir::Desc) ? (sa > sb) : (sa < sb); });
    }

    /*
     * STEP 4: main greedy placement loop.
     */
    std::vector<PlacedCircuit> placed;
    std::vector<bool> placed_flag(static_cast<size_t>(P.num_circuits), false);
    std::vector<double> dynamic_bonus(static_cast<size_t>(P.num_circuits), 0.0);

    for (int step = 0; step < P.num_circuits; ++step)
    {
        int best_c = -1;
        double best_val = -1e18;

        /*
         * (4.1) Normalize correlation bonus at this step.
         */
        double current_step_max_bonus = 0.000001;
        for (int c = 0; c < P.num_circuits; ++c)
        {
            if (!placed_flag[static_cast<size_t>(c)])
                current_step_max_bonus = std::max(current_step_max_bonus, dynamic_bonus[static_cast<size_t>(c)]);
        }

        /*
         * (4.2) Estimate the largest contiguous free block capacity.
         * Used to penalize circuits that cannot fit geometrically.
         */
        int max_contiguous_block = 0;
        int current_block = 0;
        for (int r = 0; r < P.num_racks; ++r)
        {
            if (reserved_by[static_cast<size_t>(r)] == -1)
            {
                current_block += rem_rack[static_cast<size_t>(r)];
            }
            else
            {
                max_contiguous_block = std::max(max_contiguous_block, current_block);
                current_block = 0;
            }
        }
        max_contiguous_block = std::max(max_contiguous_block, current_block);

        /*
         * (4.3) Select the best next circuit using a hybrid score:
         * - s_init: static popularity term (normalized)
         * - s_corr: dynamic co-occurrence bonus (normalized)
         * - s_fit : fit term w.r.t. current max contiguous space
         */
        for (int c = 0; c < P.num_circuits; ++c)
        {
            if (placed_flag[static_cast<size_t>(c)])
                continue;

            int c_size = static_cast<int>(products_by_circuit_sorted[static_cast<size_t>(c)].size());
            double s_init = base_circuit_scores[static_cast<size_t>(c)];
            double s_corr = dynamic_bonus[static_cast<size_t>(c)] / current_step_max_bonus;

            double s_fit = 0.0;
            if (c_size > max_contiguous_block)
            {
                s_fit = -10.0;
            }
            else
            {
                s_fit = static_cast<double>(c_size) / static_cast<double>(max_contiguous_block);
            }

            /*
             * Mixing formula:
             * - (1 - alpha) weights static popularity
             * - alpha * 0.7 weights correlation (locality / bundling)
             * - alpha * 0.3 weights fit (avoid wasting holes)
             */
            double current_val = (1.0 - cfg.alpha_corr) * s_init + (cfg.alpha_corr * 0.7) * s_corr + (cfg.alpha_corr * 0.3) * s_fit;

            if (best_c == -1 || current_val > best_val)
            {
                best_val = current_val;
                best_c = c;
            }
        }

        if (best_c == -1)
            break;

        /*
         * STEP 5: try placement; if it fails, run recovery (compaction / left-pack), then retry.
         */
        const auto &products = products_by_circuit_sorted[static_cast<size_t>(best_c)];
        PlacedCircuit pc;
        bool ok = place_circuit(P, best_c, products, aisle_score, assignment,
                                rem_rack, rem_aisle, reserved_by, PlaceMode::ScoreFirst, pc);

        if (!ok)
        {
            if (!compactify_last_w(P, 10, aisle_score, products_by_circuit_sorted, placed, assignment, rem_rack, rem_aisle, reserved_by) &&
                !compactify_last_w(P, 25, aisle_score, products_by_circuit_sorted, placed, assignment, rem_rack, rem_aisle, reserved_by) &&
                !compactify_last_w(P, P.num_circuits, aisle_score, products_by_circuit_sorted, placed, assignment, rem_rack, rem_aisle, reserved_by))
            {
                if (!left_pack_all(P, aisle_score, products_by_circuit_sorted, placed, assignment, rem_rack, rem_aisle, reserved_by))
                {
                    die("Critical failure: cannot place circuit " + std::to_string(best_c) + " even after recovery.");
                }
            }
            if (!place_circuit(P, best_c, products, aisle_score, assignment, rem_rack, rem_aisle, reserved_by, PlaceMode::ScoreFirst, pc))
                die("Fatal error: circuit " + std::to_string(best_c) + " still does not fit after recovery.");
        }

        placed.push_back(pc);
        placed_flag[static_cast<size_t>(best_c)] = true;

        /*
         * STEP 6: update dynamic bonuses using co-occurrence information.
         * When placing best_c, boost not-yet-placed circuits appearing in the same orders.
         */
        std::map<int, double> co_occurrences;
        for (int order_idx : circuit_to_orders[static_cast<size_t>(best_c)])
        {
            const auto &current_order = P.orders[static_cast<size_t>(order_idx)];
            double weight = 1.0 / static_cast<double>(current_order.size());
            for (int p : current_order)
            {
                int c_other = P.product_circuit[static_cast<size_t>(p)];
                if (!placed_flag[static_cast<size_t>(c_other)])
                {
                    co_occurrences[c_other] += weight;
                }
            }
        }

        if (!co_occurrences.empty())
        {
            std::vector<std::pair<int, double>> sorted_co(co_occurrences.begin(), co_occurrences.end());
            std::sort(sorted_co.begin(), sorted_co.end(), [](auto &a, auto &b)
                      { return a.second > b.second; });

            /* Boost only top-20 to enforce locality and avoid diluting scores. */
            size_t top_limit = std::min((size_t)20, sorted_co.size());
            for (size_t i = 0; i < top_limit; ++i)
            {
                int target_c = sorted_co[i].first;
                dynamic_bonus[static_cast<size_t>(target_c)] += sorted_co[i].second;
            }
        }
    }

    /*
     * STEP 7: local search (post-optimization).
     * Greedy may be myopic; attempt random improving swaps between equal-size circuits.
     */
    std::cout << "[Local Search] Building sparse adjacency list..." << std::endl;

    /*
     * Build stable circuit-to-circuit correlations as a sparse adjacency list.
     */
    std::vector<std::unordered_map<int, double>> adj_list(static_cast<std::size_t>(P.num_circuits));
    for (const auto &order : P.orders)
    {
        if (order.size() < 2)
            continue;
        double w = 1.0 / static_cast<double>(order.size());
        for (size_t i = 0; i < order.size(); ++i)
        {
            int circ_i = P.product_circuit[static_cast<size_t>(order[i])];
            for (size_t j = i + 1; j < order.size(); ++j)
            {
                int circ_j = P.product_circuit[static_cast<size_t>(order[j])];
                if (circ_i != circ_j)
                {
                    adj_list[static_cast<std::size_t>(circ_i)][circ_j] += w;
                    adj_list[static_cast<std::size_t>(circ_j)][circ_i] += w;
                }
            }
        }
    }

    int successful_swaps = 0;
    int iterations = 100000;

    for (int iter = 0; iter < iterations; ++iter)
    {
        /* Randomly pick two placed circuits. */
        int idx1 = rand() % static_cast<int>(placed.size());
        int idx2 = rand() % static_cast<int>(placed.size());
        if (idx1 == idx2)
            continue;

        const std::size_t i1 = static_cast<std::size_t>(idx1);
        const std::size_t i2 = static_cast<std::size_t>(idx2);

        int c1 = placed[i1].c;
        int c2 = placed[i2].c;

        /*
         * Safety: only swap circuits of identical size (keeps per-interval capacities consistent).
         */
        if (products_by_circuit_sorted[static_cast<size_t>(c1)].size() ==
            products_by_circuit_sorted[static_cast<size_t>(c2)].size())
        {
            double score_before = calculate_neighborhood_score_sparse(c1, idx1, placed, adj_list) +
                                  calculate_neighborhood_score_sparse(c2, idx2, placed, adj_list);

            double score_after = calculate_neighborhood_score_sparse(c1, idx2, placed, adj_list) +
                                 calculate_neighborhood_score_sparse(c2, idx1, placed, adj_list);

            /* Accept improving swap. */
            if (score_after > score_before)
            {
                std::swap(placed[i1].c, placed[i2].c);
                const auto &prods1 = products_by_circuit_sorted[static_cast<size_t>(c1)];
                const auto &prods2 = products_by_circuit_sorted[static_cast<size_t>(c2)];

                /* Update product-to-rack assignments consistently with the swap. */
                for (size_t k = 0; k < prods1.size(); ++k)
                {
                    std::swap(assignment[static_cast<size_t>(prods1[k])],
                              assignment[static_cast<size_t>(prods2[k])]);
                }
                successful_swaps++;
            }
        }
    }
    std::cout << "[Local Search] Done. Improvements applied: " << successful_swaps << std::endl;

    /*
     * Final check: all products must be assigned.
     */
    for (int p = 0; p < P.num_products; ++p)
        if (assignment[static_cast<size_t>(p)] < 0)
            die("Unassigned product: " + std::to_string(p));

    return assignment;
}

// ============================================================
// CLI main
// ============================================================

int main(int argc, char **argv)
{
    GreedyConfig cfg;

    std::vector<std::string> positional;
    positional.reserve((std::size_t)argc);

    for (int i = 1; i < argc; ++i)
    {
        std::string a = argv[i];

        auto need_value = [&](const char *opt) -> std::string
        {
            if (i + 1 >= argc)
                die(std::string("Missing value after ") + opt);
            return std::string(argv[++i]);
        };

        if (a == "--product-dir")
            cfg.product_dir = parse_dir(need_value("--product-dir"), "product-dir");
        else if (a == "--circuit-order")
            cfg.circuit_mode = parse_circuit_mode(need_value("--circuit-order"));
        else if (a == "--circuit-dir")
            cfg.circuit_dir = parse_dir(need_value("--circuit-dir"), "circuit-dir");
        else if (a == "--product-score")
            cfg.product_score = parse_product_score_mode(need_value("--product-score"));
        else if (a == "--alpha")
            cfg.alpha_corr = std::stod(need_value("--alpha"));
        else
            positional.push_back(a);
    }

    if (positional.size() != 1 && positional.size() != 2)
    {
        std::cerr << "Usage:\n"
                  << "  " << argv[0] << " [--product-dir asc|desc] [--circuit-order score|id] [--circuit-dir asc|desc] <instance_dat> <solution_dir>\n"
                  << "  " << argv[0] << " [--product-dir asc|desc] [--circuit-order score|id] [--circuit-dir asc|desc] <toy|market|family|category|instance_dir>\n";
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

    auto locate_root = []() -> fs::path
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
        die("Cannot locate project root (no data/ found in parent dirs).");
        return fs::path();
    };

    if (positional.size() == 2)
    {
        instance_dat = fs::path(positional[0]);
        solution_dir = fs::path(positional[1]);
    }
    else
    {
        const std::string key(positional[0]);
        const fs::path root = locate_root();
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
     * Build an output filename suffix that encodes the main config choices,
     * to make experiments traceable.
     */
    auto cfg_suffix = [&]() -> std::string
    {
        const std::string pd = (cfg.product_dir == OrderDir::Desc) ? "pdesc" : "pasc";
        const std::string cd = (cfg.circuit_dir == OrderDir::Desc) ? "cdesc" : "casc";
        const std::string cm = (cfg.circuit_mode == CircuitOrderMode::ByScore) ? "score" : "id";

        const auto ps = [&]() -> std::string
        {
            switch (cfg.product_score)
            {
            case ProductScoreMode::Auto:
                return "psauto";
            case ProductScoreMode::Actionable:
                return "psact";
            case ProductScoreMode::Popularity:
                return "pspop";
            case ProductScoreMode::Turnover:
                return "psturn";
            case ProductScoreMode::Frequency:
                return "psfreq";
            case ProductScoreMode::CorrMix:
                return "pscorrmix";
            }
            return "psauto";
        };

        return pd + "_" + ps() + "_" + cm + "_" + cd;
    };

    const fs::path out_path =
        solution_dir / ("rack_product_assignment_greedy_circuit_smart_" + cfg_suffix() + ".txt");

    try
    {
        auto t0 = Clock::now();

        fs::create_directories(solution_dir);
        if (!fs::exists(instance_dat))
            die("Missing instance.dat: " + instance_dat.string());

        Instance instance = load_instance_dat(instance_dat.string());
        PreparedData prepared(instance);

        auto assignment = heuristic_greedy_circuit_smart(prepared, cfg);
        const double cost = compute_picking_cost(instance, assignment);
        write_solution_file(out_path.string(), assignment);

        auto t1 = Clock::now();
        const double ms = std::chrono::duration<double, std::milli>(t1 - t0).count();
        std::cout << "heuristic=greedy_circuit_smart"
                  << " product_dir=" << (cfg.product_dir == OrderDir::Desc ? "desc" : "asc")
                  << " circuit_order=" << (cfg.circuit_mode == CircuitOrderMode::ByScore ? "score" : "id")
                  << " circuit_dir=" << (cfg.circuit_dir == OrderDir::Desc ? "desc" : "asc")
                  << " cost=" << cost
                  << " time_ms=" << ms
                  << " output=" << out_path.filename().string()
                  << " product_score=" << product_score_tag(cfg.product_score)
                  << " status=written\n";
        return 0;
    }
    catch (const std::exception &e)
    {
        std::cerr << "ERROR: " << e.what() << "\n";
        return 1;
    }
}
