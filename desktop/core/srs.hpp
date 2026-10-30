#pragma once
// FSRS-lite — C++20-порт эталона shared/algorithm/reference.py (docs/SRS.md).
// Обязан проходить fixtures/srs_cases.json (допуск 1e-6).
#include <cmath>
#include <cstdint>
#include <string>
#include <algorithm>

namespace tl {

struct CardState {
    double stability = 0.0;   // дни
    double difficulty = 5.0;  // 1..10
    int64_t due_ms = 0;
    int reps = 0, lapses = 0;
    std::string state = "new"; // new|learning|review|relearn
};

struct SrsParams {
    double s0[4] = {0.4, 1.4, 3.2, 15.2};
    double d0_center = 6.6;
    double recall_a = 0.1, recall_b = 0.15, recall_c = 0.9;
    double mult_hard = 0.6, mult_good = 1.0, mult_easy = 1.6;
    double lapse_factor = 0.5, lapse_d = 0.4, lapse_s = 0.6, lapse_r = 0.65;
    double max_interval_days = 365.0;
};

inline double retention(double t_days, double s) {
    static const double ln09 = std::log(0.9);
    if (s <= 0.0) return 0.0;
    return std::exp(-t_days * -ln09 / s);
}

inline CardState next_state(const CardState& card, int grade, int64_t now_ms,
                            double desired_retention = 0.9,
                            double lang_difficulty_bias = 0.0,
                            const SrsParams& w = {}) {
    static const double ln09 = std::log(0.9);
    CardState c = card;
    double elapsed = card.reps ? std::max<int64_t>(0, now_ms - card.due_ms) / 86'400'000.0 : 0.0;
    double R = (card.state == "review" || card.state == "relearn")
               ? retention(elapsed, card.stability) : 0.0;
    auto clampd = [](double v, double lo, double hi){ return std::max(lo, std::min(hi, v)); };

    if (card.state == "new") {
        c.stability = w.s0[grade - 1];
        c.difficulty = clampd(w.d0_center - (grade - 3) + lang_difficulty_bias, 1.0, 10.0);
        c.state = grade >= 3 ? "review" : "learning";
        c.reps += 1;
    } else if (grade == 1) {
        c.lapses += 1;
        c.stability = std::max(0.3,
            w.lapse_factor * std::pow(c.difficulty, -w.lapse_d) *
            (std::pow(c.stability + 1.0, w.lapse_s) - 1.0) *
            std::exp((1.0 - R) * w.lapse_r));
        c.difficulty = std::min(10.0, c.difficulty + 1.0);
        c.state = "relearn";
        c.reps += 1;
    } else {
        double mult = grade == 2 ? w.mult_hard : grade == 3 ? w.mult_good : w.mult_easy;
        double d_new = clampd(c.difficulty - 0.2 * (grade - 3) + 0.1, 1.0, 10.0);
        double bump = std::exp(w.recall_a) * (11 - d_new) * std::pow(c.stability, -w.recall_b)
                    * (std::exp(w.recall_c * (1.0 - R)) - 1.0) * mult;
        c.stability *= (1.0 + bump);
        c.difficulty = d_new;
        if ((c.state == "learning" || c.state == "relearn") && grade >= 3) c.state = "review";
        c.reps += 1;
    }

    if (c.state == "learning" || c.state == "relearn") {
        int64_t step = (grade == 1) ? 600'000LL : 86'400'000LL / 2;
        c.due_ms = now_ms + step;
    } else {
        double t_days = std::min(c.stability * (std::log(desired_retention) / ln09),
                                 w.max_interval_days);
        c.due_ms = now_ms + static_cast<int64_t>(t_days * 86'400'000.0);
    }
    return c;
}

} // namespace tl
