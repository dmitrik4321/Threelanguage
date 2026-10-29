package dev.threelanguage.core

import kotlin.math.exp
import kotlin.math.ln
import kotlin.math.pow

/**
 * FSRS-lite — Kotlin-порт эталона shared/algorithm/reference.py.
 * ОБЯЗАН проходить fixtures/srs_cases.json с допуском 1e-6 (см. docs/SRS.md).
 */
data class CardState(
    var stability: Double = 0.0,
    var difficulty: Double = 5.0,
    var dueMs: Long = 0,
    var reps: Int = 0,
    var lapses: Int = 0,
    var state: String = "new", // new|learning|review|relearn
)

object Srs {
    private val LN09 = ln(0.9)
    private val S0 = doubleArrayOf(0.4, 1.4, 3.2, 15.2)
    private const val D0_CENTER = 6.6
    private const val RECALL_A = 0.1
    private const val RECALL_B = 0.15
    private const val RECALL_C = 0.9
    private const val LAPSE_FACTOR = 0.5
    private const val LAPSE_D = 0.4
    private const val LAPSE_S = 0.6
    private const val LAPSE_R = 0.65
    private const val MAX_INTERVAL_DAYS = 365.0

    fun retention(tDays: Double, s: Double): Double =
        if (s <= 0.0) 0.0 else exp(-tDays * -LN09 / s)

    /** grade: 1 Again, 2 Hard, 3 Good, 4 Easy. Возвращает копию в новом состоянии. */
    fun nextState(card: CardState, grade: Int, nowMs: Long,
                  desiredRetention: Double = 0.9, langDifficultyBias: Double = 0.0): CardState {
        val c = card.copy()
        val elapsedDays = if (card.reps != 0)
            ((nowMs - card.dueMs).coerceAtLeast(0L)).toDouble() / 86_400_000.0 else 0.0
        val r = if (card.state == "review" || card.state == "relearn")
            retention(elapsedDays, card.stability) else 0.0

        when {
            card.state == "new" -> {
                c.stability = S0[grade - 1]
                c.difficulty = (D0_CENTER - (grade - 3) + langDifficultyBias)
                    .coerceIn(1.0, 10.0)
                c.state = if (grade >= 3) "review" else "learning"
                c.reps += 1
            }
            grade == 1 -> { // lapse
                c.lapses += 1
                c.stability = maxOf(0.3,
                    LAPSE_FACTOR * c.difficulty.pow(-LAPSE_D) *
                        ((c.stability + 1).pow(LAPSE_S) - 1) *
                        exp((1 - r) * LAPSE_R))
                c.difficulty = minOf(10.0, c.difficulty + 1.0)
                c.state = "relearn"
                c.reps += 1
            }
            else -> {
                val mult = when (grade) { 2 -> 0.6; 3 -> 1.0; else -> 1.6 }
                val dNew = (c.difficulty - 0.2 * (grade - 3) + 0.1).coerceIn(1.0, 10.0)
                val bump = exp(RECALL_A) * (11 - dNew) * c.stability.pow(-RECALL_B) *
                    (exp(RECALL_C * (1 - r)) - 1) * mult
                c.stability *= (1 + bump)
                c.difficulty = dNew
                if ((c.state == "learning" || c.state == "relearn") && grade >= 3) c.state = "review"
                c.reps += 1
            }
        }

        c.dueMs = if (c.state == "learning" || c.state == "relearn") {
            val stepMs = if (grade == 1) 600_000L else 86_400_000L / 2
            nowMs + stepMs
        } else {
            val tDays = minOf(c.stability * (ln(desiredRetention) / LN09), MAX_INTERVAL_DAYS)
            nowMs + (tDays * 86_400_000.0).toLong()
        }
        return c
    }
}
