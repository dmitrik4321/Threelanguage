#pragma once
// Анти-АФК темп-монитор — C++20-порт reference.py (docs/AFK.md).
#include <cstdint>
#include <deque>
#include <map>
#include <string>
#include <vector>
#include <algorithm>
#include <optional>

namespace tl {

constexpr int64_t IDLE_CUT_MS = 25'000;
constexpr int PACE_MULTIPLIER = 10;
constexpr int MIN_CLEAN_SAMPLES = 5;
constexpr int64_t CEILING_MS = 600'000;
constexpr int64_t MICRO_FLOOR_MS = 120'000;
constexpr int WINDOW_N = 30;

struct AfkVerdict { bool afk_suspect; int64_t weight_ms; bool record_to_pace; };

class PaceStats {
public:
    void add(const std::string& kind, int64_t active_ms) {
        auto& arr = samples_[kind];
        arr.push_back(active_ms);
        while ((int)arr.size() > WINDOW_N) arr.pop_front();
    }
    std::optional<int64_t> median(const std::string& kind) const {
        auto it = samples_.find(kind);
        if (it == samples_.end() || it->second.empty()) return std::nullopt;
        std::vector<int64_t> arr(it->second.begin(), it->second.end());
        std::sort(arr.begin(), arr.end());
        size_t m = arr.size() / 2;
        if (arr.size() % 2) return arr[m];
        return (arr[m - 1] + arr[m]) / 2;
    }
    std::optional<int64_t> percentile(const std::string& kind, double p) const {
        auto it = samples_.find(kind);
        if (it == samples_.end() || it->second.empty()) return std::nullopt;
        std::vector<int64_t> arr(it->second.begin(), it->second.end());
        std::sort(arr.begin(), arr.end());
        size_t k = (size_t)std::llround((arr.size() - 1) * p);
        k = std::min(k, arr.size() - 1);
        return arr[k];
    }
    size_t count(const std::string& kind) const {
        auto it = samples_.find(kind);
        return it == samples_.end() ? 0 : it->second.size();
    }
private:
    std::map<std::string, std::deque<int64_t>> samples_;
};

inline AfkVerdict judge_task(const std::string& kind, int64_t active_ms, const PaceStats& pace) {
    auto med = pace.median(kind);
    int64_t limit = (!med || pace.count(kind) < MIN_CLEAN_SAMPLES)
        ? CEILING_MS : std::max((int64_t)PACE_MULTIPLIER * *med, MICRO_FLOOR_MS);
    bool suspect = active_ms > limit;
    int64_t weight = (suspect && med) ? *med : active_ms;
    return {suspect, weight, !suspect};
}

struct AfkEvent { int64_t t_rel_ms; std::string type; }; // input|focus_out|focus_in

inline int64_t active_ms_from_events(const std::vector<AfkEvent>& events, int64_t task_end_ms) {
    int64_t total = 0, last_event = 0;
    bool focused = true;
    for (const auto& e : events) {
        int64_t t = std::min(e.t_rel_ms, task_end_ms);
        int64_t gap = t - last_event;
        if (focused) total += (gap > IDLE_CUT_MS) ? IDLE_CUT_MS : std::max<int64_t>(0, gap);
        if (e.type == "focus_out") focused = false;
        else if (e.type == "focus_in") { focused = true; last_event = t; continue; }
        last_event = t;
    }
    if (focused) {
        int64_t gap = task_end_ms - last_event;
        total += (gap > IDLE_CUT_MS) ? std::min(gap, IDLE_CUT_MS) : std::max<int64_t>(0, gap);
    }
    return total;
}

} // namespace tl
