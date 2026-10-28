#pragma once

#include "instance.hpp"
#include "../common/heuristic_utils.hpp"
#include <algorithm>
#include <cmath>
#include <numeric>
#include <stdexcept>
#include <string>
#include <vector>
#include <cctype>
#include <fstream>

// -----------------------------
// Safety helpers
// -----------------------------
[[noreturn]] inline void die(const std::string &msg)
{
    throw std::runtime_error(msg);
}

inline std::size_t idx(int i, const char *what)
{
    if (i < 0)
        die(std::string("negative index for ") + what + ": " + std::to_string(i));
    return static_cast<std::size_t>(i);
}

// -----------------------------
// Small math helpers
// -----------------------------
inline double clamp01(double x)
{
    if (x < 0.0)
        return 0.0;
    if (x > 1.0)
        return 1.0;
    return x;
}

inline int ceil_int(double x)
{
    return static_cast<int>(std::ceil(x - 1e-12));
}

inline std::vector<double> minmax01(const std::vector<double> &v)
{
    if (v.empty())
        return {};

    double mn = 1e100, mx = -1e100;
    for (double x : v)
    {
        mn = std::min(mn, x);
        mx = std::max(mx, x);
    }

    std::vector<double> out(v.size(), 0.0);
    if (mx <= mn + 1e-12)
        return out;

    for (std::size_t i = 0; i < v.size(); ++i)
        out[i] = (v[i] - mn) / (mx - mn);

    return out;
}

// -----------------------------
// Domain normalizations
// -----------------------------
// Convert aeration rate to fraction in [0,1], accepting either 0..1 or 0..100.
inline double standardize_aeration(double a)
{
    if (a > 1.0)
        return a / 100.0;
    if (a < 0.0)
        return 0.0;
    return a;
}

// Normalize product_circuit to 0..C-1.
// Accepts common export formats: either already 0..C-1, or 1..C.
inline std::vector<int> normalize_product_circuit(const std::vector<int> &raw, int num_circuits)
{
    if (raw.empty())
        return {};

    std::vector<int> pc = raw;
    const int mn = *std::min_element(pc.begin(), pc.end());
    const int mx = *std::max_element(pc.begin(), pc.end());

    if (mn == 1 && mx == num_circuits)
        for (int &c : pc)
            --c;

    for (int c : pc)
        if (!(0 <= c && c < num_circuits))
            die("product_circuit not valid after normalization: c=" + std::to_string(c) +
                " C=" + std::to_string(num_circuits) +
                " (raw min=" + std::to_string(mn) + " raw max=" + std::to_string(mx) + ")");

    return pc;
}

// -----------------------------
// Picking cost (reusable)
// START rack = 0, END rack = n-1 (spec).
// -----------------------------
inline double compute_picking_cost(
    const Instance &I,
    const std::vector<int> &assignment)
{
    const int START = 0;
    const int END = I.num_racks - 1;

    if ((int)assignment.size() != I.num_products)
        die("compute_picking_cost: assignment size mismatch");

    double total_cost = 0.0;

    for (const auto &order : I.orders)
    {
        std::vector<int> visited;
        visited.reserve(order.size());

        for (int p : order)
        {
            if (!(0 <= p && p < I.num_products))
                die("Invalid product id in orders for cost");
            const int r = assignment[idx(p, "product")];
            if (r <= 0 || r >= END)
                continue;
            visited.push_back(r);
        }

        if (visited.empty())
        {
            total_cost += I.adjacency[idx(START, "rack")][idx(END, "rack")];
            continue;
        }

        std::sort(visited.begin(), visited.end());
        visited.erase(std::unique(visited.begin(), visited.end()), visited.end());

        int prev = START;
        for (int r : visited)
        {
            total_cost += I.adjacency[idx(prev, "rack")][idx(r, "rack")];
            prev = r;
        }
        total_cost += I.adjacency[idx(prev, "rack")][idx(END, "rack")];
    }

    return total_cost;
}
[[maybe_unused]] inline void normalize_interval(int &L, int &R)
{
    if (L > R)
        std::swap(L, R);
}

[[maybe_unused]] inline std::vector<int> read_assignment_file(const std::string &path, int num_products)
{
    std::ifstream in(path);
    if (!in)
        die("Cannot open init solution: " + path);

    auto is_space = [](char ch) -> bool
    {
        return std::isspace(static_cast<unsigned char>(ch)) != 0;
    };

    auto trim = [&](std::string &s)
    {
        while (!s.empty() && is_space(s.front()))
            s.erase(s.begin());
        while (!s.empty() && is_space(s.back()))
            s.pop_back();
    };

    auto parse_int_or_die = [&](const std::string &line, const std::string &ctx) -> int
    {
        try
        {
            std::size_t pos = 0;
            int v = std::stoi(line, &pos);
            // refuse trailing garbage (except spaces)
            while (pos < line.size() && is_space(line[pos]))
                ++pos;
            if (pos != line.size())
                die("Bad line in init solution (" + ctx + "): " + line);
            return v;
        }
        catch (...)
        {
            die("Bad line in init solution (" + ctx + "): " + line);
        }
        return 0;
    };

    // On lit toutes les lignes "utiles" (= int pur), en ignorant vides et commentaires '#'
    std::vector<int> values;
    values.reserve((std::size_t)num_products + 1);

    std::string line;
    while (std::getline(in, line))
    {
        trim(line);
        if (line.empty())
            continue;
        if (line[0] == '#')
            continue;

        values.push_back(parse_int_or_die(line, "expected integer"));
    }

    if (values.empty())
        die("Init solution is empty (no numeric lines): " + path);

    std::vector<int> assignment((std::size_t)num_products, -1);

    // Cas A : format officiel avec header P
    if ((int)values.size() == num_products + 1 && values[0] == num_products)
    {
        for (int p = 0; p < num_products; ++p)
            assignment[(std::size_t)p] = values[(std::size_t)(p + 1)];
        return assignment;
    }

    // Cas B : format sans header (ancien)
    if ((int)values.size() == num_products)
    {
        for (int p = 0; p < num_products; ++p)
            assignment[(std::size_t)p] = values[(std::size_t)p];
        return assignment;
    }

    // Cas C : fichier plus long mais header détectable (on ignore le surplus)
    if ((int)values.size() > num_products + 1 && values[0] == num_products)
    {
        for (int p = 0; p < num_products; ++p)
            assignment[(std::size_t)p] = values[(std::size_t)(p + 1)];
        return assignment;
    }

    // Sinon : erreur explicite
    die("Bad init solution format: got " + std::to_string(values.size()) +
        " numeric lines. Expected " + std::to_string(num_products) +
        " (no header) OR " + std::to_string(num_products + 1) +
        " (with header P=num_products). File=" + path);

    return assignment; // unreachable
}
