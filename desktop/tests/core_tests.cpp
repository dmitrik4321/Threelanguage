// Golden-прогон C++-ядра: читает shared/algorithm/fixtures/*.json (свой мини-сканер —
// фикстуры плоские), прогоняет tl::next_state / tl::judge_task и сверяет с Python-эталоном.
// Паритет алгоритмов Android/Desktop проверяется именно здесь (см. docs/ARCHITECTURE.md).
#include "../core/srs.hpp"
#include "../core/afk.hpp"
#include <cstdio>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>
#include <cmath>

static std::string slurp(const std::string& p) {
    std::ifstream f(p); std::stringstream ss; ss << f.rdbuf(); return ss.str();
}
static long long get_int(const std::string& s, const std::string& key, size_t from = 0) {
    auto pos = s.find("\"" + key + "\"", from);
    if (pos == std::string::npos) return -1;
    pos = s.find(':', pos);
    return std::stoll(s.substr(pos + 1));
}
static double get_dbl(const std::string& s, const std::string& key, size_t from = 0) {
    auto pos = s.find("\"" + key + "\"", from);
    if (pos == std::string::npos) return -999;
    pos = s.find(':', pos);
    return std::stod(s.substr(pos + 1));
}
static std::string get_str(const std::string& s, const std::string& key, size_t from = 0) {
    auto pos = s.find("\"" + key + "\"", from);
    if (pos == std::string::npos) return "";
    pos = s.find(':', pos); pos = s.find('"', pos);
    auto end = s.find('"', pos + 1);
    return s.substr(pos + 1, end - pos - 1);
}
static bool get_bool_near(const std::string& s, const std::string& key, size_t from) {
    auto pos = s.find("\"" + key + "\"", from);
    if (pos == std::string::npos) return false;
    pos = s.find(':', pos);
    return s.compare(pos + 1, 4, "true") == 0 || s.compare(pos + 1, 5, " true") == 0;
}

int main(int argc, char** argv) {
    std::string fxdir = argc > 1 ? argv[1] : "../shared/algorithm/fixtures";
    int fails = 0;

    // ---------------- SRS ----------------
    {
        std::string txt = slurp(fxdir + "/srs_cases.json");
        if (txt.empty()) { printf("FAIL: no srs_cases.json\n"); return 1; }
        size_t p = 0;
        while ((p = txt.find("\"name\"", p)) != std::string::npos) {
            std::string name = get_str(txt, "name", p);
            size_t exp_pos = txt.find("\"expected\"", p);
            if (exp_pos == std::string::npos) break;
            double stability = get_dbl(txt, "stability", p);
            double difficulty = get_dbl(txt, "difficulty", p);
            long long due = get_int(txt, "due_ms", p);
            int reps = (int)get_int(txt, "reps", p);
            int lapses = (int)get_int(txt, "lapses", p);
            std::string state = get_str(txt, "state", p);
            int grade = (int)get_int(txt, "grade", p);
            long long now = get_int(txt, "now_ms", p);
            double e_stab = get_dbl(txt, "stability", exp_pos);
            double e_diff = get_dbl(txt, "difficulty", exp_pos);
            long long e_due = get_int(txt, "due_ms", exp_pos);
            std::string e_state = get_str(txt, "state", exp_pos);

            tl::CardState c{stability, difficulty, due, reps, lapses, state};
            auto got = tl::next_state(c, grade, now);
            bool ok = std::abs(got.stability - e_stab) < 1e-6 &&
                      std::abs(got.difficulty - e_diff) < 1e-6 &&
                      got.due_ms == e_due && got.state == e_state;
            printf("%s srs:%s\n", ok ? "PASS" : "FAIL", name.c_str());
            if (!ok) {
                printf("  got stab=%.6f diff=%.6f due=%ld state=%s | want stab=%.6f diff=%.6f due=%lld state=%s\n",
                       got.stability, got.difficulty, got.due_ms, got.state.c_str(),
                       e_stab, e_diff, e_due, e_state.c_str());
                ++fails;
            }
            p = exp_pos + 1;
        }
    }

    // ---------------- Pace / AFK verdict ----------------
    {
        std::string txt = slurp(fxdir + "/pace_cases.json");
        if (txt.empty()) { printf("FAIL: no pace_cases.json\n"); return 1; }
        size_t p = 0;
        while ((p = txt.find("\"name\"", p)) != std::string::npos) {
            std::string name = get_str(txt, "name", p);
            long long active = get_int(txt, "active_ms", p);
            size_t sp = txt.find("\"samples\"", p);
            size_t arr0 = txt.find('[', sp), arr1 = txt.find(']', sp);
            tl::PaceStats pace;
            std::string nums = txt.substr(arr0 + 1, arr1 - arr0 - 1);
            size_t i = 0;
            while (i < nums.size() && (i = nums.find_first_of("0123456789", i)) != std::string::npos) {
                pace.add("K", std::stoll(nums.substr(i)));
                i = nums.find(',', i);
                if (i == std::string::npos) break;
            }
            size_t ep = txt.find("\"expected\"", p);
            bool e_susp = get_bool_near(txt, "afk_suspect", ep);
            long long e_w = get_int(txt, "weight_ms", ep);
            bool e_rec = get_bool_near(txt, "record_to_pace", ep);
            auto v = tl::judge_task("K", active, pace);
            bool ok = v.afk_suspect == e_susp && v.weight_ms == e_w && v.record_to_pace == e_rec;
            printf("%s pace:%s\n", ok ? "PASS" : "FAIL", name.c_str());
            if (!ok) ++fails;
            p = ep + 1;
        }
    }

    printf(fails ? "CORE TESTS FAILED: %d\n" : "ALL CORE TESTS PASSED (%d fails)\n", fails);
    return fails ? 1 : 0;
}
