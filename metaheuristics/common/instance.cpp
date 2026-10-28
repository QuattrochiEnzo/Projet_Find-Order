#include "instance.hpp"
#include <fstream>
#include <sstream>
#include <stdexcept>
#include <cctype>

static void die(const std::string &m) { throw std::runtime_error(m); }

static std::string ltrim(std::string s)
{
    size_t i = 0;
    while (i < s.size() && std::isspace((unsigned char)s[i]))
        ++i;
    s.erase(0, i);
    return s;
}

static std::vector<int> read_int_list(std::istringstream &iss)
{
    std::vector<int> v;
    int x;
    while (iss >> x)
        v.push_back(x);
    return v;
}

static std::vector<double> read_double_list(std::istringstream &iss)
{
    std::vector<double> v;
    double x;
    while (iss >> x)
        v.push_back(x);
    return v;
}

Instance load_instance_dat(const std::string &path)
{
    std::ifstream f(path);
    if (!f)
        die("Cannot open: " + path);

    Instance I;
    std::string line;

    auto expect = [&](bool ok, const std::string &msg)
    {
        if (!ok)
            die("instance.dat: " + msg);
    };

    int ais_expected = -1, adj_expected = -1, ord_expected = -1;

    while (std::getline(f, line))
    {
        line = ltrim(line);
        if (line.empty() || line[0] == '#')
            continue;

        std::istringstream iss(line);
        std::string tag;
        iss >> tag;

        if (tag == "META")
        {
            expect(static_cast<bool>(
                       iss >> I.num_racks >> I.total_slots >> I.aeration_rate >>
                       I.num_products >> I.num_circuits >> I.num_aisles >> I.num_orders),
                   "bad META");
        }
        else if (tag == "CAP")
            I.rack_capacity = read_int_list(iss);
        else if (tag == "CIR")
            I.product_circuit = read_int_list(iss);
        else if (tag == "AIS")
        {
            expect(static_cast<bool>(iss >> ais_expected), "bad AIS");
            I.aisles_racks.clear();
        }
        else if (tag == "A")
        {
            int k;
            expect(static_cast<bool>(iss >> k), "bad A");

            std::vector<int> racks;
            expect(k >= 0, "bad A: negative k");
            racks.reserve(static_cast<std::size_t>(k));

            for (int i = 0; i < k; i++)
            {
                int r;
                expect(static_cast<bool>(iss >> r), "bad A list");
                racks.push_back(r);
            }
            I.aisles_racks.push_back(std::move(racks));
        }
        else if (tag == "ADJ")
        {
            expect(static_cast<bool>(iss >> adj_expected), "bad ADJ");
            I.adjacency.clear();
        }
        else if (tag == "D")
            I.adjacency.push_back(read_int_list(iss));
        else if (tag == "ORD")
        {
            expect(static_cast<bool>(iss >> ord_expected), "bad ORD");
            I.orders.clear();
        }
        else if (tag == "O")
        {
            int k;
            expect(static_cast<bool>(iss >> k), "bad O");

            std::vector<int> ps;
            expect(k >= 0, "bad O: negative k");
            ps.reserve(static_cast<std::size_t>(k));
            for (int i = 0; i < k; i++)
            {
                int p;
                expect(static_cast<bool>(iss >> p), "bad O list");
                ps.push_back(p);
            }
            I.orders.push_back(std::move(ps));
        }
        // ------------------------------------------------------------
        // Optional metrics (backward compatible)
        // PMET <name> <n> v0 v1 ... v(n-1)
        // RMET <name> <n> v0 v1 ... v(n-1)
        // SMET <name> <value>
        // ------------------------------------------------------------
        else if (tag == "PMET")
        {
            std::string name;
            int n;
            expect(static_cast<bool>(iss >> name >> n), "bad PMET header");
            auto vals = read_double_list(iss);
            expect((int)vals.size() == n, "PMET size mismatch for " + name);
            I.product_metrics[name] = std::move(vals);
        }
        else if (tag == "RMET")
        {
            std::string name;
            int n;
            expect(static_cast<bool>(iss >> name >> n), "bad RMET header");
            auto vals = read_double_list(iss);
            expect((int)vals.size() == n, "RMET size mismatch for " + name);
            I.rack_metrics[name] = std::move(vals);
        }
        else if (tag == "SMET")
        {
            std::string name;
            double v;
            expect(static_cast<bool>(iss >> name >> v), "bad SMET");
            I.scalar_metrics[name] = v;
        }
        else if (tag == "AMET")
        {
            std::string name;
            int n;
            expect(static_cast<bool>(iss >> name >> n), "bad AMET header");
            auto vals = read_double_list(iss);
            expect((int)vals.size() == n, "AMET size mismatch for " + name);
            I.aisle_metrics[name] = std::move(vals);
        }
        else if (tag == "CMET")
        {
            std::string name;
            int n;
            expect(static_cast<bool>(iss >> name >> n), "bad CMET header");
            auto vals = read_double_list(iss);
            expect((int)vals.size() == n, "CMET size mismatch for " + name);
            I.circuit_metrics[name] = std::move(vals);
        }

        else
            die("Unknown tag: " + tag);
    }

    expect((int)I.rack_capacity.size() == I.num_racks, "CAP size mismatch");
    expect((int)I.product_circuit.size() == I.num_products, "CIR size mismatch");
    expect(ais_expected == -1 || (int)I.aisles_racks.size() == ais_expected, "AIS count mismatch");
    expect(adj_expected == -1 || (int)I.adjacency.size() == adj_expected, "ADJ count mismatch");
    expect(ord_expected == -1 || (int)I.orders.size() == ord_expected, "ORD count mismatch");

    // Optional sanity checks if metrics exist
    for (const auto &kv : I.product_metrics)
        expect((int)kv.second.size() == I.num_products, "product metric bad size: " + kv.first);
    for (const auto &kv : I.rack_metrics)
        expect((int)kv.second.size() == I.num_racks, "rack metric bad size: " + kv.first);
    for (const auto &kv : I.aisle_metrics)
        expect((int)kv.second.size() == I.num_aisles, "aisle metric bad size: " + kv.first);
    for (const auto &kv : I.circuit_metrics)
        expect((int)kv.second.size() == I.num_circuits, "circuit metric bad size: " + kv.first);

    return I;
}
