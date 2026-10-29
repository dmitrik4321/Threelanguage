#pragma once
// Сверка ответов — C++20-порт reference.py::partial_credit (NFKC + diacritic-strip + Levenshtein).
// Для CJK-NFKC нужен ICU; в MVP — упрощённая нормализация + TODO(icu) для full NFKC.
#include <string>
#include <vector>
#include <algorithm>
#include <cctype>

namespace tl {

inline std::string utf8_fold_ascii(std::string s) {
    for (auto& ch : s) if ((unsigned char)ch < 128) ch = (char)std::tolower((unsigned char)ch);
    return s;
}

inline int levenshtein(const std::u32string& a, const std::u32string& b) {
    std::u32string A = a, B = b;
    if (A.size() < B.size()) std::swap(A, B);
    std::vector<int> prev(B.size() + 1), cur(B.size() + 1);
    for (size_t j = 0; j <= B.size(); ++j) prev[j] = (int)j;
    for (size_t i = 1; i <= A.size(); ++i) {
        cur[0] = (int)i;
        for (size_t j = 1; j <= B.size(); ++j) {
            cur[j] = std::min({prev[j] + 1, cur[j - 1] + 1,
                               prev[j - 1] + (A[i - 1] != B[j - 1] ? 1 : 0)});
        }
        prev = cur;
    }
    return prev[B.size()];
}

// минимальный UTF-8 -> u32 (без BOM-валидации — вход доверенный из БД)
inline std::u32string utf8_to_u32(const std::string& s) {
    std::u32string out; size_t i = 0;
    while (i < s.size()) {
        unsigned char c = s[i];
        uint32_t cp; int len;
        if (c < 0x80) { cp = c; len = 1; }
        else if ((c >> 5) == 0x6) { cp = c & 0x1F; len = 2; }
        else if ((c >> 4) == 0xE) { cp = c & 0x0F; len = 3; }
        else { cp = c & 0x07; len = 4; }
        for (int k = 1; k < len && i + k < s.size(); ++k)
            cp = (cp << 6) | (s[i + k] & 0x3F);
        out.push_back(cp); i += len;
    }
    return out;
}

inline double partial_credit(const std::vector<std::string>& expected, const std::string& given) {
    auto g = utf8_to_u32(utf8_fold_ascii(given));
    double best = 0.0;
    for (const auto& e : expected) {
        auto en = utf8_to_u32(utf8_fold_ascii(e));
        if (g == en) return 1.0;
        int d = levenshtein(g, en);
        double credit = std::max(0.0, 1.0 - d / (double)std::max<size_t>(1, en.size()));
        best = std::max(best, d <= 1 ? credit : credit * 0.6);
    }
    return best; // TODO: rounding parity с эталоном при генерации fixtures scoring_cases.json
}

} // namespace tl
