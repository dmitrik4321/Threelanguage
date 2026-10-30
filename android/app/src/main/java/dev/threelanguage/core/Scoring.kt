package dev.threelanguage.core

import kotlin.math.ln
import kotlin.math.roundToInt

/** Оценка урока 0..100 — порт reference.py::score_lesson (docs/SCORING.md). */
data class AnswerRecord(
    val kind: String,
    val correct: Double,      // 0..1 частичный кредит
    val activeMs: Long,
    val afkExcluded: Boolean,
    val hintUsed: Boolean = false,
    val tooFast: Boolean = false,
    val selfCorrected: Int = 0,
    val skipped: Boolean = false,
    val replays: Int = 0,
)

object Scoring {
    fun scoreLesson(answers: List<AnswerRecord>, pace: Afk.PaceStats): Int {
        if (answers.isEmpty()) return 0
        val n = answers.size
        val acc = answers.map { it.correct * (if (it.hintUsed) 0.7 else 1.0) }
        val a = acc.average()

        val bvals = answers.filterNot { it.afkExcluded }.map { an ->
            val p75 = (pace.percentile(an.kind, 0.75) ?: 15_000L).coerceAtLeast(1L)
            val r = an.activeMs.toDouble() / p75
            var v = when {
                r <= 1.0 -> 1.0
                r <= 3.0 -> maxOf(0.0, 1 - 0.7 * (r - 1) / 2)
                else -> 0.0
            }
            if (an.tooFast) v *= 0.5
            v
        }
        val b = if (bvals.isNotEmpty()) bvals.average() else 0.5

        val c = if (n >= 4) 1.0 - entropyNorm(acc.map { it * 10 + 1 }) else 0.7

        val d = answers.map { an ->
            var v = 1.0
            if (an.skipped) v -= 0.8
            if (an.replays > 2) v -= 0.1 * (an.replays - 2)
            v += minOf(0.15, 0.05 * an.selfCorrected)
            v.coerceIn(0.0, 1.0)
        }.average()

        return (100 * (0.45 * a + 0.15 * b + 0.20 * c + 0.20 * d))
            .roundToInt().coerceIn(0, 100)
    }

    fun lessonScoreToGrade(score: Int): Int = when {
        score < 45 -> 1; score < 70 -> 2; score < 90 -> 3; else -> 4
    }

    private fun entropyNorm(vals: List<Double>): Double {
        if (vals.isEmpty()) return 0.0
        val tot = vals.sum().takeIf { it > 0 } ?: 1.0
        val ps = vals.map { it / tot }
        val h = -ps.filter { it > 0 }.sumOf { p -> p * ln(p) }
        return if (ps.size > 1) h / ln(ps.size.toDouble()) else 0.0
    }
}
