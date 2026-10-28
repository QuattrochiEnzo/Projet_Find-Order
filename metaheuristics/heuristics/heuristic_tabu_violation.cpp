/*
 * heuristic_tabu_violation.cpp
 *
 * Tabu Search "soft constraints":
 * - Autorise temporairement des solutions infaisables (capacité / aération / contiguïté)
 * - Score étendu = proxy + λ_cap*Vcap + λ_aer*Vaer + λ_cont*Vcont
 * - Pénalités adaptatives
 * - Réparation locale (fenêtre) via votre repair_local_window() STRICT (feasible_interval)
 *
 * CLI (stable):
 *   heuristic_tabu_violation <instance_dat> <solution_dir> [--iters N] [--k K] [--seed S]
 *
 * Notes:
 * - On maintient un proxy incrémental: on ne recalcule pas O(#orders) à chaque move.
 * - On ne casse pas la structure State: on ajoute seulement des champs auxiliaires.
 * - On garde feasible_interval() strict pour repair et initialisation.
 */

#include "../common/instance.hpp"
#include "../common/solution.hpp"
#include "../common/heuristic_utils.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <filesystem>
#include <iostream>
#include <limits>
#include <numeric>
#include <random>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

namespace fs = std::filesystem;
using Clock = std::chrono::steady_clock;

// ============================================================
// Small utilities
// ============================================================

static int randi(std::mt19937 &rng, int a, int b)
{
    normalize_interval(a, b);
    std::uniform_int_distribution<int> d(a, b);
    return d(rng);
}
static double rand01(std::mt19937 &rng)
{
    std::uniform_real_distribution<double> d(0.0, 1.0);
    return d(rng);
}

template <class T>
static int pick_index_non_empty(std::mt19937 &rng, const std::vector<T> &v, const char *ctx)
{
    if (v.empty())
        die(std::string("pick_index_non_empty: empty vector in ") + ctx);
    return randi(rng, 0, (int)v.size() - 1);
}

// ============================================================
// PreparedData (same spirit as heuristic_blanck.cpp)
// ============================================================

struct PreparedData
{
    const Instance &I;
    const int num_racks;
    const int num_products;
    const int num_circuits;
    const int num_aisles;
    const int num_orders;

    const double aeration_frac; // standardized [0,1]

    const std::vector<int> &rack_capacity;             // [rack]
    const std::vector<std::vector<int>> &aisles_racks; // [aisle] -> racks
    const std::vector<std::vector<int>> &adjacency;    // distances
    const std::vector<std::vector<int>> &orders;       // [order] -> products

    std::vector<int> product_circuit_norm; // [product] in 0..C-1

    std::vector<int> rack_to_aisle;                    // [rack] -> aisle (or -1)
    std::vector<std::vector<int>> products_by_circuit; // [circuit] -> products
    std::vector<int> racks_usable;                     // racks with capacity>0 excluding START/END (0 and n-1)
    std::vector<int> aisle_total_capacity;             // [aisle]
    std::vector<int> aisle_min_aeration;               // [aisle]
    std::vector<int> aisle_usable_capacity;            // [aisle]

    // For incremental proxy updates:
    std::vector<std::vector<int>> orders_by_product; // [product] -> list of order ids

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
        product_circuit_norm = normalize_product_circuit(inst.product_circuit, num_circuits);
        build_precomputations();
        sanity_checks();
    }

private:
    void build_precomputations()
    {
        // rack_to_aisle
        rack_to_aisle.assign((std::size_t)num_racks, -1);
        for (int a = 0; a < (int)aisles_racks.size(); ++a)
        {
            for (int r : aisles_racks[(std::size_t)a])
            {
                if (!(0 <= r && r < num_racks))
                    die("aisles_racks contains invalid rack id r=" + std::to_string(r));
                if (rack_to_aisle[(std::size_t)r] != -1 && rack_to_aisle[(std::size_t)r] != a)
                    die("rack appears in multiple aisles: r=" + std::to_string(r));
                rack_to_aisle[(std::size_t)r] = a;
            }
        }

        // products_by_circuit
        products_by_circuit.assign((std::size_t)num_circuits, {});
        for (int p = 0; p < num_products; ++p)
        {
            const int c = product_circuit_norm[(std::size_t)p];
            products_by_circuit[(std::size_t)c].push_back(p);
        }

        // racks_usable (exclude START=0 and END=n-1)
        racks_usable.clear();
        racks_usable.reserve((std::size_t)std::max(0, num_racks - 2));
        for (int r = 1; r < num_racks - 1; ++r)
            if (rack_capacity[(std::size_t)r] > 0)
                racks_usable.push_back(r);

        // aeration helpers per aisle
        aisle_total_capacity.assign((std::size_t)num_aisles, 0);
        aisle_min_aeration.assign((std::size_t)num_aisles, 0);
        aisle_usable_capacity.assign((std::size_t)num_aisles, 0);

        for (int a = 0; a < num_aisles; ++a)
        {
            int total_cap = 0;
            for (int r : aisles_racks[(std::size_t)a])
                total_cap += rack_capacity[(std::size_t)r];

            aisle_total_capacity[(std::size_t)a] = total_cap;
            const int min_aer = ceil_int((double)total_cap * aeration_frac);
            aisle_min_aeration[(std::size_t)a] = min_aer;
            aisle_usable_capacity[(std::size_t)a] = std::max(0, total_cap - min_aer);
        }

        // orders_by_product
        orders_by_product.assign((std::size_t)num_products, {});
        for (int o = 0; o < num_orders; ++o)
        {
            for (int p : orders[(std::size_t)o])
            {
                if (!(0 <= p && p < num_products))
                    die("orders contains invalid product id p=" + std::to_string(p));
                orders_by_product[(std::size_t)p].push_back(o);
            }
        }
    }

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

        if (!(0.0 <= aeration_frac && aeration_frac <= 1.0))
            die("aeration_frac not in [0,1]: " + std::to_string(aeration_frac));
    }
};

// ============================================================
// Tabu structures
// ============================================================

struct Interval
{
    int L = -1, R = -1;
};

struct TabuKey
{
    int c, L, R;
    bool operator==(const TabuKey &o) const { return c == o.c && L == o.L && R == o.R; }
};
struct TabuKeyHash
{
    std::size_t operator()(const TabuKey &k) const
    {
        return (std::size_t)k.c * 1315423911u ^ (std::size_t)k.L * 2654435761u ^ (std::size_t)k.R;
    }
};

enum MoveType
{
    RELOCATE,
    SWAP,
    SHIFT,         // change R with L fixed
    SLIDE,         // translate [L,R] by d
    SHRINK_EXPAND, // adjust one side by +/- t
    SWAP_REPAIR
};

struct Move
{
    MoveType type = RELOCATE;
    int c1 = -1, c2 = -1;
    int L1 = -1, R1 = -1;
    int L2 = -1, R2 = -1;
    int d = 0;
    int side = 0; // 0=left, 1=right
    double score = 1e100;
};

// ============================================================
// Penalties / violations
// ============================================================

struct PenaltyWeights
{
    double cap = 1.0, aer = 1.0, cont = 1.0;
    double mult_up = 1.10, mult_down = 0.97;
    double min_w = 1e-6, max_w = 1e9;
};

struct Violations
{
    long long cap = 0;
    long long aer = 0;
    long long cont = 0;
    long long total() const { return cap + aer + cont; }
};

static inline double extended_score(double proxy, const Violations &V, const PenaltyWeights &W)
{
    return proxy + W.cap * (double)V.cap + W.aer * (double)V.aer + W.cont * (double)V.cont;
}

// ============================================================
// State (keeps your structure, adds aux for proxy increment)
// ============================================================

struct State
{
    const Instance *I = nullptr;

    // normalized local data
    std::vector<int> pc_norm; // product -> circuit in 0..C-1
    double aer_frac = 0.0;

    std::vector<int> assign;        // product -> rack, -1 if unassigned
    std::vector<Interval> interval; // circuit -> [L,R], -1 if unplaced
    std::vector<std::vector<int>> prods_by_circuit;

    std::vector<int> rem_rack;        // remaining capacity per rack (can go negative in soft mode)
    std::vector<int> rack_aisle;      // rack -> aisle id, -1 if none
    std::vector<int> aisle_cap_total; // total capacity per aisle
    std::vector<int> aisle_used;      // used slots per aisle
    std::vector<int> aisle_max_used;  // max allowed used (aeration)

    // incremental proxy helpers
    const PreparedData *P = nullptr;
    std::vector<int> order_min_r; // [order] min assigned rack (or +INF if none)
    std::vector<int> order_max_r; // [order] max assigned rack (or -1 if none)
    double proxy = 0.0;

    void init_empty(const PreparedData &prepared);

    void remove_circuit(int c);

    bool feasible_interval(int c, int L, int R) const;

    // modified: strict switch
    bool place_circuit(int c, int L, int R, bool left_first, bool strict);

    bool intervals_ok() const;

    // incremental proxy
    void rebuild_orders_bounds_and_proxy();
    void recompute_order_bounds_and_proxy_delta(const std::vector<int> &orders_touched);

    // helper
    std::vector<int> collect_orders_touched_by_circuits(const std::vector<int> &circuits) const;
};

void State::init_empty(const PreparedData &prepared)
{
    I = &prepared.I;
    P = &prepared;

    const int Pn = I->num_products;
    [[maybe_unused]] const int Rn = I->num_racks;
    const int Cn = I->num_circuits;
    const int An = I->num_aisles;

    pc_norm = prepared.product_circuit_norm;
    aer_frac = prepared.aeration_frac;

    assign.assign((std::size_t)Pn, -1);
    interval.assign((std::size_t)Cn, Interval{-1, -1});
    prods_by_circuit = prepared.products_by_circuit;

    rem_rack = I->rack_capacity;

    rack_aisle = prepared.rack_to_aisle;
    aisle_cap_total = prepared.aisle_total_capacity;

    aisle_used.assign((std::size_t)An, 0);
    aisle_max_used.assign((std::size_t)An, 0);
    for (int a = 0; a < An; ++a)
        aisle_max_used[(std::size_t)a] = prepared.aisle_usable_capacity[(std::size_t)a];

    // proxy helpers
    order_min_r.assign((std::size_t)I->num_orders, (int)1e9);
    order_max_r.assign((std::size_t)I->num_orders, -1);
    proxy = 0.0;
}

std::vector<int> State::collect_orders_touched_by_circuits(const std::vector<int> &circuits) const
{
    if (!P)
        die("collect_orders_touched_by_circuits: P is null");
    std::unordered_set<int> S;
    S.reserve(2048);

    for (int c : circuits)
    {
        if (!(0 <= c && c < I->num_circuits))
            continue;
        for (int p : prods_by_circuit[(std::size_t)c])
        {
            for (int o : P->orders_by_product[(std::size_t)p])
                S.insert(o);
        }
    }

    std::vector<int> out;
    out.reserve(S.size());
    for (int o : S)
        out.push_back(o);
    return out;
}

void State::rebuild_orders_bounds_and_proxy()
{
    if (!P)
        die("rebuild_orders_bounds_and_proxy: P is null");

    const int START = 0;
    const int END = I->num_racks - 1;

    std::fill(order_min_r.begin(), order_min_r.end(), (int)1e9);
    std::fill(order_max_r.begin(), order_max_r.end(), -1);

    double total = 0.0;

    for (int o = 0; o < I->num_orders; ++o)
    {
        int mn = (int)1e9;
        int mx = -1;
        for (int p : P->orders[(std::size_t)o])
        {
            int r = assign[(std::size_t)p];
            if (r < 0)
                continue;
            mn = std::min(mn, r);
            mx = std::max(mx, r);
        }
        order_min_r[(std::size_t)o] = mn;
        order_max_r[(std::size_t)o] = mx;

        if (mx < 0)
        {
            total += P->adjacency[(std::size_t)START][(std::size_t)END];
        }
        else
        {
            total += P->adjacency[(std::size_t)START][(std::size_t)mn];
            total += P->adjacency[(std::size_t)mx][(std::size_t)END];
            total += (mx - mn);
        }
    }

    proxy = total;
}

void State::recompute_order_bounds_and_proxy_delta(const std::vector<int> &orders_touched)
{
    if (!P)
        die("recompute_order_bounds_and_proxy_delta: P is null");

    const int START = 0;
    const int END = I->num_racks - 1;

    auto order_cost = [&](int mn, int mx) -> double
    {
        if (mx < 0)
            return (double)P->adjacency[(std::size_t)START][(std::size_t)END];
        return (double)P->adjacency[(std::size_t)START][(std::size_t)mn] +
               (double)P->adjacency[(std::size_t)mx][(std::size_t)END] +
               (double)(mx - mn);
    };

    for (int o : orders_touched)
    {
        if (!(0 <= o && o < I->num_orders))
            continue;

        const int old_mn = order_min_r[(std::size_t)o];
        const int old_mx = order_max_r[(std::size_t)o];
        const double old_cost = order_cost(old_mn, old_mx);

        int mn = (int)1e9;
        int mx = -1;
        for (int p : P->orders[(std::size_t)o])
        {
            int r = assign[(std::size_t)p];
            if (r < 0)
                continue;
            mn = std::min(mn, r);
            mx = std::max(mx, r);
        }

        order_min_r[(std::size_t)o] = mn;
        order_max_r[(std::size_t)o] = mx;
        const double new_cost = order_cost(mn, mx);

        proxy += (new_cost - old_cost);
    }
}

void State::remove_circuit(int c)
{
    if (c < 0 || c >= (int)interval.size())
        return;
    if (interval[(std::size_t)c].L < 0)
        return;

    const auto &prods = prods_by_circuit[(std::size_t)c];
    for (int p : prods)
    {
        int r = assign[(std::size_t)p];
        if (r < 0)
            continue;

        rem_rack[(std::size_t)r] += 1;

        int a = rack_aisle[(std::size_t)r];
        if (a >= 0)
            aisle_used[(std::size_t)a] -= 1;

        assign[(std::size_t)p] = -1;
    }

    interval[(std::size_t)c] = Interval{-1, -1};
}

bool State::feasible_interval(int c, int L, int R) const
{
    if (c < 0 || c >= (int)interval.size())
        return false;
    if (L < 0 || R < 0 || L >= I->num_racks || R >= I->num_racks)
        return false;
    if (L > R)
        return false;

    // Strict overlap forbidden; touching allowed
    for (int c2 = 0; c2 < (int)interval.size(); ++c2)
    {
        if (c2 == c)
            continue;
        const auto &iv = interval[(std::size_t)c2];
        if (iv.L < 0)
            continue;
        if (std::max(L, iv.L) < std::min(R, iv.R))
            return false;
    }

    const int need = (int)prods_by_circuit[(std::size_t)c].size();
    if (need == 0)
        return true;

    std::vector<int> rem = rem_rack; // copy
    std::vector<int> added(aisle_used.size(), 0);

    int r = L;
    for (int k = 0; k < need; ++k)
    {
        while (r <= R && rem[(std::size_t)r] <= 0)
            ++r;
        if (r > R)
            return false;

        rem[(std::size_t)r]--;

        int a = rack_aisle[(std::size_t)r];
        if (a >= 0)
            added[(std::size_t)a]++;
    }

    for (std::size_t a = 0; a < aisle_used.size(); ++a)
        if (aisle_used[a] + added[a] > aisle_max_used[a])
            return false;

    return true;
}

// Modified: strict switch
bool State::place_circuit(int c, int L, int R, bool left_first, bool strict)
{
    if (!I)
        die("State::place_circuit: I is null");

    if (c < 0 || c >= (int)interval.size())
        return false;

    normalize_interval(L, R);
    if (L < 0 || R < 0 || L >= I->num_racks || R >= I->num_racks)
        return false;
    if (L > R)
        return false;

    if (strict && !feasible_interval(c, L, R))
        return false;

    // remove current placement if any
    remove_circuit(c);

    const auto &prods = prods_by_circuit[(std::size_t)c];
    interval[(std::size_t)c] = Interval{L, R};

    if (prods.empty())
        return true;

    int r = left_first ? L : R;
    const int step = left_first ? +1 : -1;

    auto advance = [&](int &rr)
    {
        rr += step;
        if (rr < L)
            rr = R;
        if (rr > R)
            rr = L;
    };

    for (int p : prods)
    {
        if (strict)
        {
            int tries = 0;
            while (tries <= (R - L) && rem_rack[(std::size_t)r] <= 0)
            {
                r += step;
                tries++;
                if (r < L || r > R)
                    r = left_first ? L : R;
            }
            if (rem_rack[(std::size_t)r] <= 0)
            {
                remove_circuit(c);
                return false;
            }
        }
        // soft: accept even if rem<=0

        assign[(std::size_t)p] = r;
        rem_rack[(std::size_t)r] -= 1;

        const int a = rack_aisle[(std::size_t)r];
        if (a >= 0)
            aisle_used[(std::size_t)a] += 1;

        advance(r);
    }

    return true;
}

bool State::intervals_ok() const
{
    // Basic consistency: if interval placed then products must be assigned.
    // (Does not enforce strict feasibility here.)
    for (int c = 0; c < (int)interval.size(); ++c)
    {
        const Interval iv = interval[(std::size_t)c];
        if (iv.L < 0)
            continue;
        if (iv.L > iv.R)
            return false;
    }
    return true;
}

// ============================================================
// Violations computation
// ============================================================

static inline long long viol_capacity_total(const State &st)
{
    long long v = 0;
    for (int r = 0; r < st.I->num_racks; ++r)
        if (st.rem_rack[(std::size_t)r] < 0)
            v += (long long)(-st.rem_rack[(std::size_t)r]);
    return v;
}

static inline long long viol_aeration_total(const State &st)
{
    long long v = 0;
    for (int a = 0; a < st.I->num_aisles; ++a)
    {
        const int used = st.aisle_used[(std::size_t)a];
        const int maxu = st.aisle_max_used[(std::size_t)a];
        if (used > maxu)
            v += (long long)(used - maxu);
    }
    return v;
}

// contiguity violation (local): holes/discontinuities inside [L,R] + overlaps with other circuits
static long long contiguity_violation_for_circuit(const State &st, int c)
{
    if (!(0 <= c && c < st.I->num_circuits))
        return 0;

    const Interval iv = st.interval[idx(c, "c")];
    if (iv.L < 0 || iv.R < 0 || iv.L > iv.R)
        return 0;

    const int L = iv.L, R = iv.R;
    const int NR = st.I->num_racks;

    // Mark racks occupied by this circuit (only within [L,R])
    static thread_local std::vector<unsigned char> used_flag;
    static thread_local std::vector<int> touched;
    if ((int)used_flag.size() != NR)
        used_flag.assign((std::size_t)NR, 0);
    touched.clear();

    for (int p : st.prods_by_circuit[(std::size_t)c])
    {
        int r = st.assign[(std::size_t)p];
        if (r < L || r > R)
            continue;
        if (r >= 0 && r < NR && !used_flag[(std::size_t)r])
        {
            used_flag[(std::size_t)r] = 1;
            touched.push_back(r);
        }
    }

    long long holes = 0;
    int segments = 0;
    bool in_seg = false;
    for (int r = L; r <= R; ++r)
    {
        const bool occ = (r >= 0 && r < NR) ? (used_flag[(std::size_t)r] != 0) : false;
        if (!occ)
        {
            holes += 1;
            if (in_seg)
                in_seg = false;
        }
        else
        {
            if (!in_seg)
            {
                segments++;
                in_seg = true;
            }
        }
    }

    for (int r : touched)
        used_flag[(std::size_t)r] = 0;

    const long long discont = std::max(0, segments - 1);

    // Overlap with other circuits (soft allowed, penalized):
    long long overlap = 0;
    for (int c2 = 0; c2 < st.I->num_circuits; ++c2)
    {
        if (c2 == c)
            continue;
        const Interval jv = st.interval[idx(c2, "c2")];
        if (jv.L < 0)
            continue;
        const int lo = std::max(L, jv.L);
        const int hi = std::min(R, jv.R);
        if (lo <= hi)
            overlap += (hi - lo + 1);
    }

    return holes + discont + overlap;
}

static inline long long viol_contiguity_local(const State &st, const std::vector<int> &circuits)
{
    long long v = 0;
    for (int c : circuits)
        v += contiguity_violation_for_circuit(st, c);
    return v;
}

// ============================================================
// Move generation (based on your snippets)
// ============================================================

static Move gen_relocate(const State &cur, std::mt19937 &rng)
{
    Move m;
    m.type = RELOCATE;
    m.c1 = randi(rng, 0, cur.I->num_circuits - 1);

    int need = (int)cur.prods_by_circuit[(std::size_t)m.c1].size();
    int len_base = std::max(0, need);
    int wiggle = randi(rng, 0, 60);
    int len = len_base + wiggle;

    int L = randi(rng, 1, std::max(1, cur.I->num_racks - 2));
    int R = std::min(cur.I->num_racks - 2, L + len);

    m.L1 = L;
    m.R1 = R;
    return m;
}

static Move gen_swap(const State &cur, std::mt19937 &rng)
{
    Move m;
    m.type = SWAP;
    m.c1 = randi(rng, 0, cur.I->num_circuits - 1);
    m.c2 = randi(rng, 0, cur.I->num_circuits - 1);
    while (m.c2 == m.c1)
        m.c2 = randi(rng, 0, cur.I->num_circuits - 1);
    return m;
}

static Move gen_shift(const State &cur, std::mt19937 &rng)
{
    Move m;
    m.type = SHIFT;
    m.c1 = randi(rng, 0, cur.I->num_circuits - 1);
    const auto &iv = cur.interval[(std::size_t)m.c1];
    if (iv.L < 0)
        return m;

    int d = (rand01(rng) < 0.5) ? -randi(rng, 1, 20) : randi(rng, 1, 20);
    int newR = std::min(cur.I->num_racks - 2, std::max(iv.L, iv.R + d));
    m.L1 = iv.L;
    m.R1 = newR;
    return m;
}

static Move gen_slide(const State &cur, std::mt19937 &rng)
{
    Move m;
    m.type = SLIDE;

    m.c1 = randi(rng, 0, cur.I->num_circuits - 1);
    const auto &iv = cur.interval[(std::size_t)m.c1];
    if (iv.L < 0)
        return m;

    int len = iv.R - iv.L;
    int shift = (rand01(rng) < 0.5) ? -randi(rng, 1, 20) : randi(rng, 1, 20);

    int newL = iv.L + shift;
    int newR = newL + len;

    newL = std::max(1, newL);
    newR = std::min(cur.I->num_racks - 2, newR);

    if (newL > newR)
        std::swap(newL, newR);

    m.L1 = newL;
    m.R1 = newR;
    return m;
}

static Move gen_shrink_expand(const State &cur, std::mt19937 &rng)
{
    Move m;
    m.type = SHRINK_EXPAND;

    m.c1 = randi(rng, 0, cur.I->num_circuits - 1);
    const auto &iv = cur.interval[(std::size_t)m.c1];
    if (iv.L < 0)
        return m;

    int side = (rand01(rng) < 0.5) ? 0 : 1;
    int delta = randi(rng, 1, 15);

    int newL = iv.L;
    int newR = iv.R;

    if (side == 0)
        newL -= delta;
    else
        newR += delta;

    newL = std::max(1, newL);
    newR = std::min(cur.I->num_racks - 2, newR);

    if (newL > newR)
        std::swap(newL, newR);

    m.L1 = newL;
    m.R1 = newR;
    return m;
}

// ============================================================
// Hard move validity (reject only invalid indices / inverted interval; blocked racks not available => ignored)
// ============================================================

static inline bool hard_valid_interval(const State &st, int L, int R)
{
    ::normalize_interval(L, R);

    const int NR = st.I->num_racks;
    if (L < 0 || R < 0 || L >= NR || R >= NR)
        return false;
    if (L > R)
        return false;

    // Exclure START/END
    if (L <= 0)
        return false;
    if (R >= NR - 1)
        return false;

    // HARD: interdire tout rack à capacité 0 dans l'intervalle
    // => évite exactement l'erreur "special/zero-capacity rack"
    for (int r = L; r <= R; ++r)
    {
        if (st.I->rack_capacity[(std::size_t)r] <= 0)
            return false;
    }

    return true;
}

// ============================================================
// repair_local_window (STRICT) -- reusing your provided code (slightly adapted to strict place_circuit signature)
// ============================================================

static bool repair_local_window(State &st,
                                const std::vector<int> &circuits,
                                int winL, int winR,
                                int max_tries,
                                std::mt19937 &rng)
{
    (void)rng;

    if (!st.I)
        die("repair_local_window: st.I is null");

    normalize_interval(winL, winR);

    if (winR < winL)
        return false;

    State backup = st;

    // remove circuits
    for (int c : circuits)
    {
        if (!(0 <= c && c < st.I->num_circuits))
            continue;
        st.remove_circuit(c);
    }

    std::vector<int> order = circuits;
    std::sort(order.begin(), order.end(), [&](int a, int b)
              {
                  if (!(0 <= a && a < st.I->num_circuits)) return false;
                  if (!(0 <= b && b < st.I->num_circuits)) return true;
                  return st.prods_by_circuit[(std::size_t)a].size() >
                         st.prods_by_circuit[(std::size_t)b].size(); });

    for (int attempt = 0; attempt < std::max(1, max_tries); ++attempt)
    {
        bool ok_all = true;

        st = backup;
        for (int c : circuits)
            st.remove_circuit(c);

        // touched orders for incremental proxy update
        std::vector<int> orders_touched = st.collect_orders_touched_by_circuits(circuits);

        for (int c : order)
        {
            if (!(0 <= c && c < st.I->num_circuits))
                continue;

            bool placed = false;

            for (int L = winL; L <= winR && !placed; ++L)
            {
                for (int R = L; R <= winR; ++R)
                {
                    if (!st.feasible_interval(c, L, R))
                        continue;
                    if (st.place_circuit(c, L, R, true, /*strict=*/true))
                    {
                        placed = true;
                        break;
                    }
                }
            }

            if (!placed)
            {
                ok_all = false;
                break;
            }
        }

        if (ok_all && st.intervals_ok())
        {
            // rebuild proxy incrementally only on touched orders
            st.recompute_order_bounds_and_proxy_delta(orders_touched);

            return true;
        }
    }

    st = backup;
    return false;
}

// ============================================================
// Apply move (soft), incremental proxy update on touched orders
// ============================================================

static bool apply_move_soft(State &st, const Move &m, std::vector<int> &circuits_touched, std::vector<int> &orders_touched)
{
    if (!st.I)
        die("apply_move_soft: st.I null");

    circuits_touched.clear();

    if (m.type == RELOCATE || m.type == SHIFT || m.type == SLIDE || m.type == SHRINK_EXPAND)
    {
        const int c = m.c1;
        if (!(0 <= c && c < st.I->num_circuits))
            return false;

        int L = m.L1, R = m.R1;
        normalize_interval(L, R);
        if (!hard_valid_interval(st, L, R))
            return false;

        circuits_touched.push_back(c);
        orders_touched = st.collect_orders_touched_by_circuits(circuits_touched);

        // apply soft
        if (!st.place_circuit(c, L, R, /*left_first=*/true, /*strict=*/false))
            return false;

        // proxy delta only for touched orders
        st.recompute_order_bounds_and_proxy_delta(orders_touched);
        return true;
    }

    if (m.type == SWAP || m.type == SWAP_REPAIR)
    {
        const int c1 = m.c1, c2 = m.c2;
        if (!(0 <= c1 && c1 < st.I->num_circuits))
            return false;
        if (!(0 <= c2 && c2 < st.I->num_circuits))
            return false;
        if (c1 == c2)
            return false;

        Interval iv1 = st.interval[idx(c1, "c")];
        Interval iv2 = st.interval[idx(c2, "c")];
        if (iv1.L < 0 || iv2.L < 0)
            return false;

        if (!hard_valid_interval(st, iv2.L, iv2.R))
            return false;
        if (!hard_valid_interval(st, iv1.L, iv1.R))
            return false;

        circuits_touched.push_back(c1);
        circuits_touched.push_back(c2);
        orders_touched = st.collect_orders_touched_by_circuits(circuits_touched);

        st.remove_circuit(c1);
        st.remove_circuit(c2);

        bool ok1 = st.place_circuit(c1, iv2.L, iv2.R, true, false);
        bool ok2 = st.place_circuit(c2, iv1.L, iv1.R, true, false);
        if (!ok1 || !ok2)
            return false;

        st.recompute_order_bounds_and_proxy_delta(orders_touched);
        return true;
    }

    return false;
}

// ============================================================
// Evaluate move (soft): apply on copy, compute V and score
// ============================================================

static double eval_move_soft(const State &cur,
                             const Move &m,
                             const PenaltyWeights &W,
                             Violations &outV,
                             State &outNext)
{
    outNext = cur;

    std::vector<int> circs, orders;
    if (!apply_move_soft(outNext, m, circs, orders))
        return 1e100;

    outV.cap = viol_capacity_total(outNext);
    outV.aer = viol_aeration_total(outNext);

    // contiguity only on touched circuits (plus overlap with others is already accounted per circuit)
    outV.cont = viol_contiguity_local(outNext, circs);

    return extended_score(outNext.proxy, outV, W);
}

// ============================================================
// Adaptive penalties
// ============================================================

static void adapt_penalties(PenaltyWeights &W,
                            const Violations &V,
                            bool feasible,
                            int iter,
                            int &last_infeasible_iter,
                            long long high_V)
{
    if (!feasible)
        last_infeasible_iter = iter;

    if (V.total() > high_V)
    {
        if (V.cap > 0)
            W.cap = std::min(W.max_w, W.cap * W.mult_up);
        if (V.aer > 0)
            W.aer = std::min(W.max_w, W.aer * W.mult_up);
        if (V.cont > 0)
            W.cont = std::min(W.max_w, W.cont * W.mult_up);
        return;
    }

    const int stable = iter - last_infeasible_iter;
    if (feasible && stable > 200)
    {
        W.cap = std::max(W.min_w, W.cap * W.mult_down);
        W.aer = std::max(W.min_w, W.aer * W.mult_down);
        W.cont = std::max(W.min_w, W.cont * W.mult_down);
    }
}

// ============================================================
// Decide whether to repair after accepting a move
// ============================================================

static bool maybe_repair_after_accept(State &st,
                                      const Move &m,
                                      const Violations &V,
                                      long long seuil_V,
                                      int max_tries,
                                      std::mt19937 &rng)
{
    if (V.total() <= seuil_V)
        return true;

    std::vector<int> circs;
    if (m.c1 >= 0)
        circs.push_back(m.c1);
    if (m.c2 >= 0 && m.c2 != m.c1)
        circs.push_back(m.c2);

    if (circs.empty())
        return true;

    int winL = st.I->num_racks - 2;
    int winR = 1;
    for (int c : circs)
    {
        if (!(0 <= c && c < st.I->num_circuits))
            continue;
        const Interval iv = st.interval[idx(c, "c")];
        if (iv.L < 0)
            continue;
        winL = std::min(winL, iv.L);
        winR = std::max(winR, iv.R);
    }

    // expand window
    winL = std::max(1, winL - 25);
    winR = std::min(st.I->num_racks - 2, winR + 25);

    // Repair is STRICT: it will enforce feasible_interval.
    // After repair, rebuild full proxy bounds for safety (or delta on touched)
    const bool ok = repair_local_window(st, circs, winL, winR, max_tries, rng);
    if (ok)
    {
        // safest: rebuild everything
        st.rebuild_orders_bounds_and_proxy();
    }
    return ok;
}

// ============================================================
// Initialization: greedy strict placement; fallback soft if needed.
// ============================================================

static void build_initial_solution(State &st, std::mt19937 &rng)
{
    // Place circuits in descending size
    std::vector<int> circs((std::size_t)st.I->num_circuits);
    std::iota(circs.begin(), circs.end(), 0);
    std::sort(circs.begin(), circs.end(), [&](int a, int b)
              { return st.prods_by_circuit[(std::size_t)a].size() > st.prods_by_circuit[(std::size_t)b].size(); });

    const int R = st.I->num_racks;

    for (int c : circs)
    {
        const int need = (int)st.prods_by_circuit[(std::size_t)c].size();
        if (need == 0)
            continue;

        bool placed = false;

        // try strict windows with modest wiggle
        const int maxWiggle = 50;
        for (int L = 1; L < R - 1 && !placed; ++L)
        {
            int R0 = std::min(R - 2, L + need + maxWiggle);
            for (int RR = L; RR <= R0; ++RR)
            {
                if (st.feasible_interval(c, L, RR))
                {
                    if (st.place_circuit(c, L, RR, /*left_first=*/true, /*strict=*/true))
                    {
                        placed = true;
                        break;
                    }
                }
            }
        }

        if (!placed)
        {
            int L = 1;
            for (int tries = 0; tries < 100; ++tries)
            {
                int cand = randi(rng, 1, std::max(1, R - 2));
                if (st.I->rack_capacity[(std::size_t)cand] > 0)
                {
                    L = cand;
                    break;
                }
            }
            int RR = std::min(R - 2, L + std::max(0, need));
            while (RR > L && st.I->rack_capacity[(std::size_t)RR] <= 0)
                --RR;

            if (hard_valid_interval(st, L, RR))
                st.place_circuit(c, L, RR, true, false);
            else
                st.place_circuit(c, 1, std::min(R - 2, 1 + std::max(0, need)), true, false);
        }
    }

    // Build full proxy data from current assignment
    st.rebuild_orders_bounds_and_proxy();
}

// ============================================================
// Tabu search core
// ============================================================

struct TabuList
{
    std::unordered_map<TabuKey, int, TabuKeyHash> until;
    int tenure = 25;

    bool is_tabu(const TabuKey &k, int iter) const
    {
        auto it = until.find(k);
        if (it == until.end())
            return false;
        return it->second > iter;
    }

    void add(const TabuKey &k, int iter)
    {
        until[k] = iter + tenure;
    }
};

static Move sample_move(const State &cur, std::mt19937 &rng)
{
    double x = rand01(rng);

    if (x < 0.35)
        return gen_relocate(cur, rng);
    if (x < 0.55)
        return gen_shift(cur, rng);
    if (x < 0.75)
        return gen_slide(cur, rng);
    if (x < 0.90)
        return gen_shrink_expand(cur, rng);
    return gen_swap(cur, rng);
}

struct BestRecord
{
    double best_score = std::numeric_limits<double>::infinity();
    double best_proxy = std::numeric_limits<double>::infinity();
    State best_state;
};
struct BestFeasibleRecord
{
    bool has = false;
    double best_proxy = std::numeric_limits<double>::infinity();
    State best_state;
};

static bool is_strict_feasible(const State &st)
{
    // 1) pas d’assign sur rack cap=0
    for (int p = 0; p < st.I->num_products; ++p)
    {
        int r = st.assign[(std::size_t)p];
        if (r < 0)
            return false; // chez vous: tout produit doit être assigné
        if (st.I->rack_capacity[(std::size_t)r] <= 0)
            return false;
    }

    // 2) cap & aer
    if (viol_capacity_total(st) != 0)
        return false;
    if (viol_aeration_total(st) != 0)
        return false;

    // 3) overlap strict
    for (int c1 = 0; c1 < st.I->num_circuits; ++c1)
    {
        const Interval a = st.interval[(std::size_t)c1];
        if (a.L < 0)
            return false;
        for (int c2 = c1 + 1; c2 < st.I->num_circuits; ++c2)
        {
            const Interval b = st.interval[(std::size_t)c2];
            if (b.L < 0)
                return false;
            if (std::max(a.L, b.L) < std::min(a.R, b.R))
                return false; // overlap interdit, toucher OK
        }
    }

    return true;
}

static std::vector<int> heuristic_tabu_violation(const PreparedData &prepared,
                                                 int iters,
                                                 int K,
                                                 int seed,
                                                 const std::string &init_file)
{
    std::mt19937 rng((unsigned)seed);
    BestFeasibleRecord bestF;
    State cur;
    cur.init_empty(prepared);
    if (!init_file.empty())
    {
        std::vector<int> init_assign =
            read_assignment_file(init_file, prepared.num_products);

        cur.assign = init_assign;

        // reconstruire structures internes cohérentes
        // reset capacities
        cur.rem_rack = prepared.rack_capacity;
        std::fill(cur.aisle_used.begin(), cur.aisle_used.end(), 0);
        std::fill(cur.interval.begin(), cur.interval.end(), Interval{-1, -1});

        // reconstruire rem_rack + aisle_used
        for (int p = 0; p < prepared.num_products; ++p)
        {
            int r = cur.assign[(std::size_t)p];
            if (r < 0)
                continue;
            cur.rem_rack[(std::size_t)r]--;
            int a = prepared.rack_to_aisle[(std::size_t)r];
            if (a >= 0)
                cur.aisle_used[(std::size_t)a]++;
        }

        // reconstruire intervalles circuits
        for (int c = 0; c < prepared.num_circuits; ++c)
        {
            int L = 1e9, R = -1;
            for (int p : cur.prods_by_circuit[(std::size_t)c])
            {
                int r = cur.assign[(std::size_t)p];
                if (r < 0)
                    continue;
                L = std::min(L, r);
                R = std::max(R, r);
            }
            if (R >= 0)
                cur.interval[(std::size_t)c] = Interval{L, R};
        }

        cur.rebuild_orders_bounds_and_proxy();
    }
    else
    {
        build_initial_solution(cur, rng);
    }

    PenaltyWeights W;
    W.cap = 50;
    W.aer = 100;
    W.cont = 50;

    TabuList tabu;
    tabu.tenure = 30;

    BestRecord best;
    best.best_state = cur;

    int last_infeasible_iter = 0;

    for (int iter = 1; iter <= iters; ++iter)
    {
        Move bestMove;
        State bestNext;
        Violations bestV;
        double bestMoveScore = std::numeric_limits<double>::infinity();

        // current feasibility snapshot
        Violations curV;
        curV.cap = viol_capacity_total(cur);
        curV.aer = viol_aeration_total(cur);
        curV.cont = viol_contiguity_local(cur, std::vector<int>{}); // 0 (empty local) by design
        // we only use curV.total()==0 as feasible indicator; compute local cont for current is not needed
        [[maybe_unused]] const bool cur_feasible = (curV.cap == 0 && curV.aer == 0); // cont may be nonzero if overlaps exist

        for (int k = 0; k < K; ++k)
        {
            Move m = sample_move(cur, rng);

            // candidate circuits touched for tabu key
            std::vector<int> touched;
            if (m.c1 >= 0)
                touched.push_back(m.c1);
            if (m.c2 >= 0 && m.c2 != m.c1)
                touched.push_back(m.c2);

            // Build tabu key: store (c, newL, newR) for the primary circuit
            TabuKey tk{m.c1, m.L1, m.R1};

            // Evaluate (soft)
            State next;
            Violations V;
            double sc = eval_move_soft(cur, m, W, V, next);
            if (sc >= 1e99)
                continue;

            // Aspiration: accept tabu if improves best known
            const bool tabu_flag = tabu.is_tabu(tk, iter);
            const bool aspiration = (sc < best.best_score);

            if (tabu_flag && !aspiration)
                continue;

            m.score = sc;
            if (sc < bestMoveScore)
            {
                bestMoveScore = sc;
                bestMove = m;
                bestNext = std::move(next);
                bestV = V;
            }
        }

        // If no move found, random restart soft
        if (bestMoveScore >= 1e99)
        {
            build_initial_solution(cur, rng);
            continue;
        }

        // accept best move
        cur = std::move(bestNext);
        if (is_strict_feasible(cur))
        {
            if (!bestF.has || cur.proxy < bestF.best_proxy)
            {
                bestF.has = true;
                bestF.best_proxy = cur.proxy;
                bestF.best_state = cur;
            }
        }

        // tabu update for involved circuits (simple)
        if (bestMove.c1 >= 0)
            tabu.add(TabuKey{bestMove.c1, bestMove.L1, bestMove.R1}, iter);

        // repair trigger (controlled)
        const long long seuil_V = 200; // tune
        const long long high_V = 800;  // tune
        const int repair_tries = 2;    // tune
        (void)maybe_repair_after_accept(cur, bestMove, bestV, seuil_V, repair_tries, rng);

        // recompute violations after potential repair (cheap for cap/aer, local for cont)
        Violations Vnow;
        Vnow.cap = viol_capacity_total(cur);
        Vnow.aer = viol_aeration_total(cur);
        Vnow.cont = contiguity_violation_for_circuit(cur, bestMove.c1) +
                    (bestMove.c2 >= 0 ? contiguity_violation_for_circuit(cur, bestMove.c2) : 0);

        const bool feasible_now = (Vnow.total() == 0);

        // best update
        const double score_now = extended_score(cur.proxy, Vnow, W);
        if (score_now < best.best_score)
        {
            best.best_score = score_now;
            best.best_proxy = cur.proxy;
            best.best_state = cur;
        }

        // adaptive penalties
        adapt_penalties(W, Vnow, feasible_now, iter, last_infeasible_iter, high_V);

        // optional mild diversification
        if (iter % 500 == 0 && rand01(rng) < 0.15)
        {
            build_initial_solution(cur, rng);
        }
    }

    // Output best_state assignment
    // 1) si on a une meilleure faisable, retourne-la
    if (bestF.has)
        return bestF.best_state.assign;

    // 2) sinon: réparer best_any et retourner
    State out = best.best_state;

    // réparation globale (fenêtre = tout l'entrepôt), strict
    {
        std::vector<int> allC((std::size_t)out.I->num_circuits);
        std::iota(allC.begin(), allC.end(), 0);

        // on tente une réparation large
        bool ok = repair_local_window(out, allC,
                                      /*winL=*/1,
                                      /*winR=*/out.I->num_racks - 2,
                                      /*max_tries=*/5,
                                      rng);
        if (!ok)
        {
            // dernier recours: reconstruire un init strict
            out.init_empty(*out.P);
            build_initial_solution(out, rng);
            // build_initial_solution est déjà majoritairement strict, et a fallback soft:
            // on force une réparation globale juste après
            std::vector<int> allC2((std::size_t)out.I->num_circuits);
            std::iota(allC2.begin(), allC2.end(), 0);
            (void)repair_local_window(out, allC2, 1, out.I->num_racks - 2, 5, rng);
        }
    }

    if (!is_strict_feasible(out))
        die("Final solution still infeasible after global repair.");

    return out.assign;
}

// ============================================================
// Main (similar to heuristic_blanck)
// ============================================================

int main(int argc, char **argv)
{
    // Modes:
    //  - long:  heuristic_tabu_violation <instance_dat> <solution_dir> [--iters N] [--k K] [--seed S]
    //  - short: heuristic_tabu_violation <toy|market|family|category|instance_dir> [--iters N] [--k K] [--seed S]
    if (argc < 2)
    {
        std::cerr << "Usage:\n"
                  << "  " << argv[0] << " <instance_dat> <solution_dir> [--iters N] [--k K] [--seed S]\n"
                  << "  " << argv[0] << " <toy|market|family|category|instance_dir> [--iters N] [--k K] [--seed S]\n";
        return 2;
    }

    // parse optional flags
    int iters = 2000;
    int K = 2000;
    int seed = 1;
    std::string init_file;

    auto parse_flags = [&](int &i)
    {
        const std::string a(argv[i]);

        if (a == "--iters" && i + 1 < argc)
        {
            iters = std::stoi(argv[++i]);
            return true;
        }
        if (a == "--k" && i + 1 < argc)
        {
            K = std::stoi(argv[++i]);
            return true;
        }
        if (a == "--seed" && i + 1 < argc)
        {
            seed = std::stoi(argv[++i]);
            return true;
        }
        if (a == "--init" && i + 1 < argc)
        {
            init_file = argv[++i];
            return true;
        }
        return false;
    };

    std::vector<std::string> positional;
    positional.reserve((std::size_t)argc);
    for (int i = 1; i < argc; ++i)
    {
        if (parse_flags(i))
            continue;
        positional.push_back(argv[i]);
    }

    fs::path instance_dat;
    fs::path solution_dir;

    if (positional.size() == 2)
    {
        instance_dat = fs::path(positional[0]);
        solution_dir = fs::path(positional[1]);
    }
    else if (positional.size() == 1)
    {
        const std::string key(positional[0]);

        // Find project root (parent that contains data/)
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
    else
    {
        die("Invalid arguments. Expect 1 or 2 positional args plus optional flags.");
    }

    const fs::path out_path = solution_dir / "rack_product_assignment_tabu_violation.txt";

    try
    {
        auto t0 = Clock::now();

        fs::create_directories(solution_dir);

        if (!fs::exists(instance_dat))
            die("Missing instance.dat: " + instance_dat.string());

        Instance instance = load_instance_dat(instance_dat.string());
        PreparedData prepared(instance);

        std::vector<int> assignment = heuristic_tabu_violation(prepared, iters, K, seed, init_file);
        // Quick logging cost (same formula as checker helper)
        double cost = compute_picking_cost(instance, assignment);

        write_solution_file(out_path.string(), assignment);

        auto t1 = Clock::now();
        const double ms = std::chrono::duration<double, std::milli>(t1 - t0).count();

        std::cout << "heuristic=tabu_violation time_ms=" << ms
                  << " iters=" << iters << " k=" << K << " seed=" << seed
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
