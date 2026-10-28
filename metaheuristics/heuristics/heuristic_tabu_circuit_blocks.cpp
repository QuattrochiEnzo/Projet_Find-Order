/*
 * heuristic_tabu_circuit_blocks.cpp
 *
 * Tabu Search on circuit blocks (contiguous rack intervals), with a robust feasible initializer.
 *
 * CLI (stable, same as heuristic_blanck):
 *   - long :  heuristic_tabu_circuit_blocks [options] <instance_dat> <solution_dir>
 *   - short:  heuristic_tabu_circuit_blocks [options] <toy|market|family|category|instance_dir>
 *
 * Options:
 *   --iters N
 *   --k N
 *   --seed N
 *   --init FILE   (initial solution file: absolute/relative / or copied inside solutions/)
 *
 * Output: solution_dir/rack_product_assignment_tabu_circuit_blocks_it{...}_k{...}_seed{...}.txt
 *
 * Notes:
 *  - No validation here; checker.py is responsible for validating solutions
 *  - We do NOT modify the Instance loader; we normalize locally via heuristic_utils.hpp
 */

#include "../common/instance.hpp"
#include "../common/solution.hpp"
#include "../common/heuristic_utils.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <deque>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <numeric>
#include <random>
#include <string>
#include <unordered_set>
#include <vector>

namespace fs = std::filesystem;
using Clock = std::chrono::steady_clock;

struct Interval
{
    int L = -1, R = -1;
    bool placed = false;
};

// ---------- tabu ----------
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
struct TabuEntry
{
    TabuKey key;
    int until_iter;
};

enum MoveType
{
    RELOCATE,
    SWAP,
    SHIFT,         // shift of interval [L,R]
    SLIDE,         // (unused here)
    SHRINK_EXPAND, // (unused here)
    SWAP_REPAIR    // (unused here)
};
struct Move
{
    MoveType type = RELOCATE;
    int c1 = -1, c2 = -1;
    int L1 = -1, R1 = -1;
    int L2 = -1, R2 = -1;
    int d = 0;
    int side = 0;
    double score = 1e100;
};

// ---------- RNG helpers ----------
static int randi(std::mt19937 &rng, int a, int b)
{
    if (a > b)
        std::swap(a, b);
    // Safety: avoid invalid uniform_int_distribution(a,b) if upstream logic accidentally breaks the range.
    if (b < a)
        die("randi: invalid range [" + std::to_string(a) + "," + std::to_string(b) + "]");
    std::uniform_int_distribution<int> d(a, b);
    return d(rng);
}
static double rand01(std::mt19937 &rng)
{
    std::uniform_real_distribution<double> d(0.0, 1.0);
    return d(rng);
}

// ============================================================
// Path helpers
// ============================================================

static fs::path locate_root_or_die()
{
    fs::path root = fs::current_path();
    for (int up = 0; up < 12; ++up)
    {
        if (fs::exists(root / "data"))
            return root;
        if (!root.has_parent_path())
            break;
        root = root.parent_path();
    }
    die("Cannot locate project root (no data/ found in parent dirs).");
    return fs::path();
}

static fs::path first_existing_or_empty(const std::vector<fs::path> &cands)
{
    for (const auto &p : cands)
        if (fs::exists(p))
            return p;
    return fs::path();
}

static fs::path resolve_init_solution_path(const fs::path &solution_dir,
                                           const std::string &init_file)
{
    if (init_file.empty())
        return fs::path();

    fs::path p(init_file);

    // (1) absolute path
    if (p.is_absolute() && fs::exists(p))
        return p;

    // (2) relative to current working directory
    if (fs::exists(p))
        return fs::absolute(p);

    // (3) relative to solutions/ directory
    fs::path q = solution_dir / p;
    if (fs::exists(q))
        return q;

    die("Init solution not found: " + init_file +
        " (checked absolute, cwd-relative, and " + q.string() + ")");
    return fs::path();
}

// ============================================================
// State
// ============================================================

struct State
{
    const Instance *I = nullptr;

    // Local normalization
    std::vector<int> pc_norm; // product_circuit normalized to [0..C-1]
    double aer_frac = 0.0;

    // Product -> rack assignment (solution)
    std::vector<int> assign; // size P, -1 if unassigned

    // Per-circuit data
    std::vector<Interval> interval;                 // size C
    std::vector<std::vector<int>> prods_by_circuit; // size C: product lists

    // Capacities / aeration
    std::vector<int> rem_rack;        // size R, remaining slots per rack
    std::vector<int> rack_aisle;      // size R, aisle id
    std::vector<int> aisle_cap_total; // size A
    std::vector<int> aisle_used;      // size A (used slots)
    std::vector<int> aisle_max_used;  // size A (max allowed used slots)

    double proxy = 0.0;

    void init_empty();

    void remove_circuit(int c);
    bool feasible_interval(int c, int L, int R) const;

    bool place_circuit(int c, int L, int R, bool left_first);
    bool intervals_ok() const;

    double eval_proxy() const;
};

// ---------- State core ----------

void State::init_empty()
{
    if (!I)
        die("State::init_empty: I is null");

    const int P = I->num_products;
    const int R = I->num_racks;
    const int C = I->num_circuits;
    const int A = I->num_aisles;

    if ((int)I->rack_capacity.size() != R)
        die("State::init_empty: rack_capacity size mismatch");
    if ((int)I->product_circuit.size() != P)
        die("State::init_empty: product_circuit size mismatch");
    if ((int)I->aisles_racks.size() != A)
        die("State::init_empty: aisles_racks size mismatch");

    /*
     * Local normalization:
     * - product_circuit can be 0-based (0..C-1) or 1-based (1..C)
     * - aeration can be given as percent (e.g. 15) or fraction (0.15)
     */
    pc_norm = normalize_product_circuit(I->product_circuit, C);
    aer_frac = standardize_aeration(I->aeration_rate);
    if (!(aer_frac >= 0.0 && aer_frac <= 1.0))
        die("State::init_empty: aeration invalid after standardize: raw=" + std::to_string(I->aeration_rate) +
            " norm=" + std::to_string(aer_frac));

    assign.assign((std::size_t)P, -1);
    interval.assign((std::size_t)C, Interval{-1, -1, false});
    prods_by_circuit.assign((std::size_t)C, {});

    /* Build product lists per circuit. */
    for (int p = 0; p < P; ++p)
    {
        int c = pc_norm[idx(p, "product")];
        prods_by_circuit[idx(c, "c")].push_back(p);
    }

    /* Remaining rack capacities (in "slots"). */
    rem_rack = I->rack_capacity;

    rack_aisle.assign((std::size_t)R, -1);
    aisle_cap_total.assign((std::size_t)A, 0);
    aisle_used.assign((std::size_t)A, 0);
    aisle_max_used.assign((std::size_t)A, 0);

    /* Build rack->aisle mapping and compute aisle total capacity. */
    for (int a = 0; a < A; ++a)
    {
        for (int r : I->aisles_racks[idx(a, "aisle")])
        {
            if (!(0 <= r && r < R))
                die("State::init_empty: invalid rack id in aisles_racks: a=" + std::to_string(a) +
                    " r=" + std::to_string(r));
            rack_aisle[idx(r, "rack")] = a;
            aisle_cap_total[idx(a, "aisle")] += I->rack_capacity[idx(r, "rack")];
        }
    }

    /*
     * Aeration budgets:
     * min_empty = ceil(capacity * aer_frac)
     * max_used  = capacity - min_empty
     */
    for (int a = 0; a < A; ++a)
    {
        const int cap = aisle_cap_total[idx(a, "aisle")];
        const int min_empty = ceil_int((double)cap * aer_frac);
        const int max_used = std::max(0, cap - min_empty);
        aisle_max_used[idx(a, "aisle")] = max_used;
    }

    proxy = 0.0;
}

void State::remove_circuit(int c)
{
    if (!I)
        die("State::remove_circuit: I is null");
    if (!(0 <= c && c < I->num_circuits))
        return;

    Interval &iv = interval[idx(c, "c")];
    if (iv.L < 0)
    {
        iv = Interval{-1, -1, false};
        return;
    }

    /*
     * Remove all products of circuit c from the assignment,
     * restoring rack capacities and aisle used slots.
     */
    for (int p : prods_by_circuit[idx(c, "c")])
    {
        int r = assign[idx(p, "p")];
        if (r < 0)
            continue;

        rem_rack[idx(r, "r")] += 1;

        int a = rack_aisle[idx(r, "r")];
        if (a >= 0)
            aisle_used[idx(a, "a")] -= 1;

        assign[idx(p, "p")] = -1;
    }

    iv = Interval{-1, -1, false};
}

static inline bool intersects(int L1, int R1, int L2, int R2)
{
    return !(R1 < L2 || R2 < L1);
}
static inline bool forbidden_overlap_allow_boundary(int L1, int R1, int L2, int R2)
{
    const int interL = std::max(L1, L2);
    const int interR = std::min(R1, R2);

    if (interL > interR)
        return false; // disjoint

    if (interL == interR)
    {
        const int x = interL;
        // allow only if it's exactly an end-start boundary
        if ((x == R1 && x == L2) || (x == R2 && x == L1))
            return false;
    }

    return true; // real overlap -> forbidden
}
bool State::feasible_interval(int c, int L, int R) const
{
    if (!I)
        die("State::feasible_interval: I is null");
    if (!(0 <= c && c < I->num_circuits))
        return false;

    if (L > R)
        std::swap(L, R);
    if (L < 0 || R >= I->num_racks)
        return false;

    // Local helper: forbid true overlaps, but allow sharing exactly one boundary rack
    // when intervals meet end-to-start (e.g., [..x] and [x..]).
    auto forbidden_overlap_allow_boundary = [](int L1, int R1, int L2, int R2) -> bool
    {
        const int interL = std::max(L1, L2);
        const int interR = std::min(R1, R2);

        if (interL > interR)
            return false; // disjoint

        if (interL == interR)
        {
            const int x = interL;
            // allowed only if it's exactly an end-start boundary
            if ((x == R1 && x == L2) || (x == R2 && x == L1))
                return false;
        }

        return true; // real overlap -> forbidden
    };

    /*
     * Block constraint: the interval [L,R] must not truly overlap any other placed circuit interval.
     * Boundary sharing on exactly one rack is allowed (frontier rack).
     */
    for (int cc = 0; cc < I->num_circuits; ++cc)
    {
        if (cc == c)
            continue;
        const Interval &iv = interval[idx(cc, "c")];
        if (iv.L < 0)
            continue;

        if (forbidden_overlap_allow_boundary(L, R, iv.L, iv.R))
            return false;
    }

    /*
     * Capacity + aeration feasibility:
     * Does this interval provide enough "usable slots" given:
     * - remaining rack slots
     * - remaining aisle aeration budgets?
     */
    const int demand = (int)prods_by_circuit[idx(c, "c")].size();
    if (demand == 0)
        return true;

    // Remaining aisle budgets
    std::vector<int> remA = aisle_max_used;
    for (int a = 0; a < I->num_aisles; ++a)
        remA[idx(a, "a")] -= aisle_used[idx(a, "a")];

    long long cap_sum = 0;
    for (int r = L; r <= R; ++r)
    {
        int a = rack_aisle[idx(r, "r")];
        if (a < 0)
            continue;

        const int rr = rem_rack[idx(r, "r")];
        const int aa = remA[idx(a, "a")];
        const int contrib = std::min(rr, aa);
        if (contrib > 0)
        {
            cap_sum += contrib;
            remA[idx(a, "a")] -= contrib;
            if (cap_sum >= demand)
                return true;
        }
    }
    return false;
}

bool State::place_circuit(int c, int L, int R, bool /*left_first*/)
{
    if (!I)
        die("State::place_circuit: I is null");
    if (!(0 <= c && c < I->num_circuits))
        return false;

    if (L > R)
        std::swap(L, R);
    if (L < 0 || R < 0 || L > R || R >= I->num_racks)
        return false;
    if (interval[idx(c, "c")].L >= 0)
        return false;

    const auto &prods = prods_by_circuit[idx(c, "c")];
    if (prods.empty())
    {
        interval[idx(c, "c")] = Interval{L, R, true};
        return true;
    }

    std::vector<int> used_racks;
    used_racks.reserve(prods.size());

    int r = L;
    for (int p : prods)
    {
        while (r <= R && rem_rack[idx(r, "r")] <= 0)
            ++r;

        if (r > R)
        {
            /* Rollback: restore capacities and clear product assignments. */
            for (int rr : used_racks)
            {
                rem_rack[idx(rr, "r")] += 1;
                int a = rack_aisle[idx(rr, "r")];
                if (a >= 0)
                    aisle_used[idx(a, "a")] -= 1;
            }
            for (int p2 : prods)
                assign[idx(p2, "p")] = -1;
            return false;
        }

        // Aeration constraint: cannot exceed aisle_max_used in that aisle.
        int a = rack_aisle[idx(r, "r")];
        if (a >= 0 && aisle_used[idx(a, "a")] + 1 > aisle_max_used[idx(a, "a")])
        {
            /*
             * Note: this code originally tried "++r; --p;" but that's impossible in range-based for.
             * Current behavior is conservative: rollback immediately on aeration violation.
             */
            ++r;
            // -> simpler conservative rollback
            for (int rr : used_racks)
            {
                rem_rack[idx(rr, "r")] += 1;
                int aa = rack_aisle[idx(rr, "r")];
                if (aa >= 0)
                    aisle_used[idx(aa, "a")] -= 1;
            }
            for (int p2 : prods)
                assign[idx(p2, "p")] = -1;
            return false;
        }

        assign[idx(p, "p")] = r;
        rem_rack[idx(r, "r")] -= 1;
        used_racks.push_back(r);

        if (a >= 0)
            aisle_used[idx(a, "a")] += 1;
    }

    interval[idx(c, "c")] = Interval{L, R, true};
    return true;
}

bool State::intervals_ok() const
{
    struct Seg
    {
        int L, R;
    };
    std::vector<Seg> segs;
    segs.reserve(interval.size());

    for (int c = 0; c < (int)interval.size(); ++c)
    {
        const auto &iv = interval[idx(c, "c")];
        if (iv.L < 0)
            continue;
        if (iv.L > iv.R)
            return false;
        segs.push_back(Seg{iv.L, iv.R});
    }

    std::sort(segs.begin(), segs.end(), [](const Seg &a, const Seg &b)
              {
        if (a.L != b.L) return a.L < b.L;
        return a.R < b.R; });

    // Overlap strict interdit, mais égalité autorisée (frontière partagée)
    for (std::size_t i = 1; i < segs.size(); ++i)
        if (segs[i].L < segs[i - 1].R) // <- CHANGÉ (au lieu de <=)
            return false;

    return true;
}

double State::eval_proxy() const
{
    if (!I)
        die("State::eval_proxy2: I is null");

    const int START = 0;
    const int END = I->num_racks - 1;

    double total = 0.0;

    // Reuse buffers to avoid allocations per order
    std::vector<int> racks;
    racks.reserve(64);

    for (const auto &ord : I->orders)
    {
        racks.clear();

        // Collect racks visited by this order (skip unassigned)
        for (int p : ord)
        {
            const int r = assign[idx(p, "p")];
            if (r >= 0)
                racks.push_back(r);
        }

        if (racks.empty())
        {
            total += I->adjacency[idx(START, "r")][idx(END, "r")];
            continue;
        }

        // sort + unique
        std::sort(racks.begin(), racks.end());
        racks.erase(std::unique(racks.begin(), racks.end()), racks.end());

        total += I->adjacency[idx(START, "r")][idx(racks.front(), "r")];

        for (std::size_t i = 0; i + 1 < racks.size(); ++i)
        {
            const int r1 = racks[i];
            const int r2 = racks[i + 1];
            total += I->adjacency[idx(r1, "r")][idx(r2, "r")];
        }

        total += I->adjacency[idx(racks.back(), "r")][idx(END, "r")];
    }

    return total;
}

// ============================================================
// Move generation
// ============================================================

static Move gen_slide(const State &cur, std::mt19937 &rng)
{
    Move m;
    m.type = SHIFT; // keep SHIFT (even though the function name says "slide")

    m.c1 = randi(rng, 0, cur.I->num_circuits - 1);
    const auto &iv = cur.interval[idx(m.c1, "c")];
    if (iv.L < 0)
        return m;

    int len = iv.R - iv.L;
    int shift = (rand01(rng) < 0.5) ? -randi(rng, 1, 20) : randi(rng, 1, 20);

    int newL = iv.L + shift;
    int newR = newL + len;

    newL = std::max(0, newL);
    newR = std::min(cur.I->num_racks - 1, newR);

    // Preserve length if clamping broke it.
    if (newR - newL != len)
    {
        newR = std::min(cur.I->num_racks - 1, newL + len);
        newL = std::max(0, newR - len);
    }

    m.L1 = newL;
    m.R1 = newR;
    return m;
}

static Move gen_block_swap(const State &cur, std::mt19937 &rng)
{
    Move m;
    m.type = SWAP;

    m.c1 = randi(rng, 0, cur.I->num_circuits - 1);
    m.c2 = randi(rng, 0, cur.I->num_circuits - 1);
    while (m.c2 == m.c1)
        m.c2 = randi(rng, 0, cur.I->num_circuits - 1);
    return m;
}

static Move gen_shrink_expand(const State &cur, std::mt19937 &rng)
{
    Move m;
    m.type = RELOCATE;

    m.c1 = randi(rng, 0, cur.I->num_circuits - 1);
    const auto &iv = cur.interval[idx(m.c1, "c")];
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

    newL = std::max(0, newL);
    newR = std::min(cur.I->num_racks - 1, newR);

    if (newL > newR)
        std::swap(newL, newR);

    m.L1 = newL;
    m.R1 = newR;
    return m;
}

// ============================================================
// Apply move (IMPORTANT: rollback on failure)
// ============================================================

static bool apply_move(State &st, const Move &m, std::mt19937 &rng)
{
    (void)rng;

    State backup = st;

    if (m.type == RELOCATE || m.type == SHIFT)
    {
        int c = m.c1;
        if (!(0 <= c && c < st.I->num_circuits))
            return false;
        if (st.interval[idx(c, "c")].L < 0)
            return false;

        st.remove_circuit(c);

        if (!st.feasible_interval(c, m.L1, m.R1))
        {
            st = backup;
            return false;
        }
        if (!st.place_circuit(c, m.L1, m.R1, true))
        {
            st = backup;
            return false;
        }

        return true;
    }
    else if (m.type == SWAP)
    {
        int c1 = m.c1, c2 = m.c2;
        if (!(0 <= c1 && c1 < st.I->num_circuits))
            return false;
        if (!(0 <= c2 && c2 < st.I->num_circuits))
            return false;
        if (c1 == c2)
            return false;

        const auto i1 = st.interval[idx(c1, "c")];
        const auto i2 = st.interval[idx(c2, "c")];
        if (i1.L < 0 || i2.L < 0)
            return false;

        st.remove_circuit(c1);
        st.remove_circuit(c2);

        if (!st.feasible_interval(c1, i2.L, i2.R))
        {
            st = backup;
            return false;
        }
        if (!st.place_circuit(c1, i2.L, i2.R, true))
        {
            st = backup;
            return false;
        }

        if (!st.feasible_interval(c2, i1.L, i1.R))
        {
            st = backup;
            return false;
        }
        if (!st.place_circuit(c2, i1.L, i1.R, true))
        {
            st = backup;
            return false;
        }

        return true;
    }

    st = backup;
    return false;
}

// ============================================================
// Robust feasible init (greedy-smart-like)
// ============================================================

struct IntervalChoice
{
    int L = -1;
    int R = -1;
    double score = 1e100;
};

static IntervalChoice choose_interval_bestfit_init(
    const State &st,
    const std::vector<int> &rem_aisle,
    const std::vector<int> &reserved_by,
    int demand)
{
    /*
     * Best-fit interval selection for initialization:
     * find the shortest interval [L,R] (tie-broken by smaller L) that can host 'demand' items
     * given remaining rack capacities and remaining per-aisle budgets.
     */
    const int R = st.I->num_racks;
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

            int a = st.rack_aisle[idx(r, "rack")];
            if (a >= 0)
            {
                const int rr = st.rem_rack[idx(r, "rack")];
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
                const double candidate = (double)len + 1e-4 * (double)L;
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

static void unplace_circuit_init(State &st,
                                 int c,
                                 const std::vector<int> &products,
                                 std::vector<int> &rem_aisle,
                                 std::vector<int> &reserved_by,
                                 int L,
                                 int R)
{
    /*
     * Unplace a circuit during initialization:
     * - clear product assignments
     * - restore rack and aisle capacities
     * - remove reservations over [L,R] if applicable
     */
    for (int p : products)
    {
        int r = st.assign[idx(p, "p")];
        if (r >= 0)
        {
            int a = st.rack_aisle[idx(r, "r")];
            st.rem_rack[idx(r, "r")] += 1;
            if (a >= 0)
            {
                st.aisle_used[idx(a, "a")] -= 1;
                rem_aisle[idx(a, "a")] += 1;
            }
            st.assign[idx(p, "p")] = -1;
        }
    }

    if (L >= 0 && R >= 0)
    {
        for (int r = L; r <= R; ++r)
            if (reserved_by[idx(r, "rack")] == c)
                reserved_by[idx(r, "rack")] = -1;
    }

    st.interval[idx(c, "c")] = Interval{-1, -1, false};
}

static bool place_circuit_init_bestfit(State &st,
                                       int c,
                                       const std::vector<int> &products,
                                       std::vector<int> &rem_aisle,
                                       std::vector<int> &reserved_by,
                                       int &outL,
                                       int &outR)
{
    /*
     * Place circuit c during initialization using best-fit interval selection.
     * This assigns the products into racks inside the chosen interval [L,R],
     * consuming both rack capacity and aisle budget.
     */
    const int demand = (int)products.size();

    IntervalChoice choice = choose_interval_bestfit_init(st, rem_aisle, reserved_by, demand);
    if (choice.L < 0)
        return false;

    int remaining = demand;
    std::size_t ip = 0;

    for (int r = choice.L; r <= choice.R && remaining > 0; ++r)
    {
        int a = st.rack_aisle[idx(r, "r")];
        if (a < 0)
            continue;

        const int can = std::min(st.rem_rack[idx(r, "r")], rem_aisle[idx(a, "a")]);
        const int take = std::min(can, remaining);
        if (take <= 0)
            continue;

        for (int t = 0; t < take; ++t)
        {
            int p = products[idx((int)ip, "ip")];
            ++ip;
            st.assign[idx(p, "p")] = r;
        }

        st.rem_rack[idx(r, "r")] -= take;
        st.aisle_used[idx(a, "a")] += take;
        rem_aisle[idx(a, "a")] -= take;
        remaining -= take;
    }

    if (remaining > 0)
    {
        /* Rollback on failure (no reservations were applied yet). */
        unplace_circuit_init(st, c, products, rem_aisle, reserved_by, -1, -1);
        return false;
    }

    /* Reserve the entire interval for circuit c. */
    for (int r = choice.L; r <= choice.R; ++r)
        reserved_by[idx(r, "r")] = c;

    st.interval[idx(c, "c")] = Interval{choice.L, choice.R, true};

    outL = choice.L;
    outR = choice.R;
    return true;
}

static void build_initial_feasible(State &st)
{
    /*
     * Build a feasible starting solution:
     * - reset everything
     * - place circuits largest-first using best-fit intervals
     * - if a circuit fails to place, unplace last K circuits and retry (recovery)
     */
    const int R = st.I->num_racks;
    const int C = st.I->num_circuits;
    const int A = st.I->num_aisles;

    std::fill(st.assign.begin(), st.assign.end(), -1);
    for (int c = 0; c < C; ++c)
        st.interval[idx(c, "c")] = Interval{-1, -1, false};

    st.rem_rack = st.I->rack_capacity;
    std::fill(st.aisle_used.begin(), st.aisle_used.end(), 0);

    std::vector<int> rem_aisle = st.aisle_max_used;
    std::vector<int> reserved_by((std::size_t)R, -1);

    // Order: place larger circuits first (harder to fit)
    std::vector<int> order((std::size_t)C, 0);
    std::iota(order.begin(), order.end(), 0);
    std::sort(order.begin(), order.end(), [&](int a, int b)
              { return st.prods_by_circuit[idx(a, "c")].size() > st.prods_by_circuit[idx(b, "c")].size(); });

    struct Placed
    {
        int c = -1, L = -1, R = -1;
    };
    std::vector<Placed> placed;
    placed.reserve(order.size());

    const int K_RECOVER = 25;

    int i = 0;
    while (i < (int)order.size())
    {
        int c = order[idx(i, "i")];
        const auto &prods = st.prods_by_circuit[idx(c, "c")];

        int L = -1, RR = -1;
        if (place_circuit_init_bestfit(st, c, prods, rem_aisle, reserved_by, L, RR))
        {
            placed.push_back(Placed{c, L, RR});
            ++i;
            continue;
        }

        // Recovery: unplace last K circuits and retry
        int un = std::min(K_RECOVER, (int)placed.size());
        if (un == 0)
            break;

        for (int t = 0; t < un; ++t)
        {
            Placed pc = placed.back();
            placed.pop_back();
            const auto &pp = st.prods_by_circuit[idx(pc.c, "c")];
            unplace_circuit_init(st, pc.c, pp, rem_aisle, reserved_by, pc.L, pc.R);
        }

        i = (int)placed.size();
    }

    if ((int)placed.size() != C)
        throw std::runtime_error("cannot build initial feasible solution (even with smart init)");

    // Sanity checks
    for (int r = 0; r < R; ++r)
        if (st.rem_rack[idx(r, "r")] < 0)
            throw std::runtime_error("init produced negative rem_rack");

    for (int a = 0; a < A; ++a)
        if (st.aisle_used[idx(a, "a")] > st.aisle_max_used[idx(a, "a")])
            throw std::runtime_error("init violates aeration budget");
}

// ============================================================
// Init from assignment file
// ============================================================

static void init_state_from_assignment(
    State &st,
    const Instance &instance,
    const std::vector<int> &prod_to_rack)
{
    st.I = &instance;
    st.init_empty();

    const int P = instance.num_products;
    const int R = instance.num_racks;

    if ((int)prod_to_rack.size() != P)
    {
        die("init_state_from_assignment: expected product->rack vector of length num_products=" +
            std::to_string(P) + ", got " + std::to_string(prod_to_rack.size()));
    }

    std::vector<int> assign_local = prod_to_rack;

    /*
     * Validate the input assignment:
     * - all products assigned
     * - rack ids are within bounds
     * - no blocked rack used
     */
    for (int p = 0; p < P; ++p)
    {
        const int r = assign_local[idx(p, "p")];
        if (r < 0)
            die("init file leaves product unassigned: p=" + std::to_string(p));
        if (!(0 <= r && r < R))
            die("init assignment has invalid rack id: p=" + std::to_string(p) + " r=" + std::to_string(r));
        if (instance.rack_capacity[idx(r, "r")] <= 0)
            die("init assignment puts product in blocked rack: p=" + std::to_string(p) + " r=" + std::to_string(r));
    }

    st.assign = std::move(assign_local);

    st.rem_rack = instance.rack_capacity;
    std::fill(st.aisle_used.begin(), st.aisle_used.end(), 0);

    /*
     * Recompute remaining capacities and aeration usage from the assignment.
     */
    for (int p = 0; p < P; ++p)
    {
        const int r = st.assign[idx(p, "p")];

        st.rem_rack[idx(r, "r")] -= 1;
        if (st.rem_rack[idx(r, "r")] < 0)
            die("init assignment violates rack capacity: r=" + std::to_string(r));

        const int a = st.rack_aisle[idx(r, "r")];
        if (a >= 0)
        {
            st.aisle_used[idx(a, "a")] += 1;
            if (st.aisle_used[idx(a, "a")] > st.aisle_max_used[idx(a, "a")])
                die("init assignment violates aeration budget on aisle " + std::to_string(a));
        }
    }

    /*
     * Build circuit intervals as min/max envelope over their products' racks.
     * Note: this may create overlaps if the input assignment does not respect block contiguity.
     */
    for (int c = 0; c < instance.num_circuits; ++c)
    {
        int L = 1e9, Rmax = -1;
        for (int p : st.prods_by_circuit[idx(c, "c")])
        {
            const int r = st.assign[idx(p, "p")];
            L = std::min(L, r);
            Rmax = std::max(Rmax, r);
        }
        if (Rmax < 0)
            st.interval[idx(c, "c")] = Interval{-1, -1, false};
        else
            st.interval[idx(c, "c")] = Interval{L, Rmax, true};
    }

    if (!st.intervals_ok())
        die("init assignment produces overlapping intervals (contiguity/overlap issue).");
}

// ============================================================
// Tabu search
// ============================================================

static void tabu_search(State &cur, int iters, int k, unsigned seed)
{
    std::mt19937 rng(seed);

    std::unordered_set<TabuKey, TabuKeyHash> tabu_set;
    std::deque<TabuEntry> tabu_q;

    auto add_tabu = [&](int c, int L, int R, int iter, int tenure)
    {
        TabuKey key{c, L, R};
        tabu_set.insert(key);
        tabu_q.push_back({key, iter + tenure});
    };

    auto expire = [&](int iter)
    {
        while (!tabu_q.empty() && tabu_q.front().until_iter <= iter)
        {
            tabu_set.erase(tabu_q.front().key);
            tabu_q.pop_front();
        }
    };

    cur.proxy = cur.eval_proxy();
    State best = cur;
    double best_score = cur.proxy;

    for (int it = 0; it < iters; ++it)
    {
        expire(it);

        Move best_move;
        State best_candidate;
        bool found = false;

        /*
         * Sample k candidate moves, keep the best (aspiration if improves global best).
         */
        for (int t = 0; t < k; ++t)
        {
            Move m;
            double pick = rand01(rng);

            if (pick < 0.5)
                m = gen_slide(cur, rng);
            else if (pick < 0.8)
                m = gen_shrink_expand(cur, rng);
            else
                m = gen_block_swap(cur, rng);

            State cand = cur;

            if (!apply_move(cand, m, rng))
                continue;

            cand.proxy = cand.eval_proxy();

            // Aspiration: if global best is improved, accept immediately.
            if (cand.proxy < best_score)
            {
                best_move = m;
                best_candidate = cand;
                found = true;
                break;
            }

            // Otherwise keep best candidate this iteration.
            if (!found || cand.proxy < best_move.score)
            {
                best_move = m;
                best_move.score = cand.proxy;
                best_candidate = cand;
                found = true;
            }
        }

        if (!found)
            continue;

        /*
         * Update tabu list (simple tenure).
         * IMPORTANT: we tabuize the (c,L,R) corresponding to the chosen move when applicable.
         */
        if (best_move.type == RELOCATE || best_move.type == SHIFT)
        {
            int tenure = 10 + (it % 7);
            add_tabu(best_move.c1, best_move.L1, best_move.R1, it, tenure);
        }
        else if (best_move.type == SWAP)
        {
            int tenure = 8 + (it % 5);
            // Tabuize both circuits on their new intervals (approx).
            // (If you want more accurate: read intervals directly from best_candidate.)
            const Interval &iv1 = best_candidate.interval[idx(best_move.c1, "c")];
            const Interval &iv2 = best_candidate.interval[idx(best_move.c2, "c")];
            if (iv1.L >= 0)
                add_tabu(best_move.c1, iv1.L, iv1.R, it, tenure);
            if (iv2.L >= 0)
                add_tabu(best_move.c2, iv2.L, iv2.R, it, tenure);
        }

        cur = best_candidate;

        if (cur.proxy < best_score)
        {
            best_score = cur.proxy;
            best = cur;
        }
    }

    cur = best; // always return the best found state
}

// ============================================================
// MAIN
// ============================================================

int main(int argc, char **argv)
{
    int iters = 20000;
    int k = 2000;
    unsigned seed = 1;
    std::string init_file;

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

        if (a == "--iters")
            iters = std::stoi(need_value("--iters"));
        else if (a == "--k")
            k = std::stoi(need_value("--k"));
        else if (a == "--seed")
            seed = (unsigned)std::stoul(need_value("--seed"));
        else if (a == "--init")
            init_file = need_value("--init");
        else
            positional.push_back(a);
    }

    if (positional.size() != 1 && positional.size() != 2)
    {
        std::cerr << "Usage:\n"
                  << "  " << argv[0] << " [--iters N] [--k N] [--seed N] [--init FILE] <instance_dat> <solution_dir>\n"
                  << "  " << argv[0] << " [--iters N] [--k N] [--seed N] [--init FILE] <toy|market|family|category|instance_dir>\n";
        return 2;
    }

    fs::path instance_dat;
    fs::path solution_dir;

    if (positional.size() == 2)
    {
        instance_dat = fs::path(positional[0]);
        solution_dir = fs::path(positional[1]);
    }
    else
    {
        const std::string key(positional[0]);
        const fs::path root = locate_root_or_die();
        const fs::path data_dir = root / "data";
        fs::path inst_dir;

        if (key == "toy")
            inst_dir = first_existing_or_empty({data_dir / "warehouse_toy", data_dir / "wharehouse_toy"});
        else if (key == "market")
            inst_dir = first_existing_or_empty({
                data_dir / "wharehouse_big_market",
                data_dir / "warehouse_big_market",
                data_dir / "wharehouse_big_" / "wharehouse_big_market",
                data_dir / "warehouse_big_" / "warehouse_big_market",
            });
        else if (key == "family")
            inst_dir = first_existing_or_empty({
                data_dir / "wharehouse_big_family",
                data_dir / "warehouse_big_family",
                data_dir / "wharehouse_big_" / "wharehouse_big_family",
                data_dir / "warehouse_big_" / "warehouse_big_family",
            });
        else if (key == "category")
            inst_dir = first_existing_or_empty({
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
        }

        if (inst_dir.empty() || !fs::exists(inst_dir))
            die("Instance directory not found: " + key);

        instance_dat = inst_dir / "instance.dat";
        solution_dir = inst_dir / "solutions";
    }

    const fs::path out_path =
        solution_dir / ("rack_product_assignment_tabu_circuit_blocks_it" + std::to_string(iters) +
                        "_k" + std::to_string(k) +
                        "_seed" + std::to_string(seed) + ".txt");

    try
    {
        auto t0 = Clock::now();

        fs::create_directories(solution_dir);
        if (!fs::exists(instance_dat))
            die("Missing instance.dat: " + instance_dat.string());

        Instance instance = load_instance_dat(instance_dat.string());

        State st;
        st.I = &instance;
        st.init_empty();

        /*
         * Initialization:
         * - If --init is provided: load a product->rack assignment from file and rebuild state.
         * - Else: build a feasible solution using the robust greedy initializer.
         */
        if (!init_file.empty())
        {
            const fs::path init_path = resolve_init_solution_path(solution_dir, init_file);

            // IMPORTANT: read in PRODUCT->RACK format (length P)
            const auto prod_to_rack = read_assignment_file(init_path.string(), instance.num_products);

            init_state_from_assignment(st, instance, prod_to_rack);

            std::cout << "[INFO] init=loaded file=" << init_path.filename().string() << "\n";
        }
        else
        {
            build_initial_feasible(st);
            std::cout << "[INFO] init=built feasible\n";
        }

        tabu_search(st, iters, k, seed);

        const auto &final_assignment = st.assign;
        const double cost = compute_picking_cost(instance, final_assignment);
        write_solution_file(out_path.string(), final_assignment);

        auto t1 = Clock::now();
        const double ms = std::chrono::duration<double, std::milli>(t1 - t0).count();

        std::cout << "heuristic=tabu_circuit_blocks"
                  << " iters=" << iters
                  << " k=" << k
                  << " seed=" << seed
                  << " init=" << (init_file.empty() ? "none" : init_file)
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
