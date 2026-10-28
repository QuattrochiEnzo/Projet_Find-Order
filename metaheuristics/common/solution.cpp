#include "solution.hpp"
#include <fstream>
#include <stdexcept>

static void die(const std::string &m) { throw std::runtime_error(m); }

void write_solution_file(const std::string &out_path, const std::vector<int> &assignment)
{
    std::ofstream f(out_path);
    if (!f)
        die("Cannot write: " + out_path);
    f << assignment.size() << "\n";
    for (int r : assignment)
        f << r << "\n";
}
