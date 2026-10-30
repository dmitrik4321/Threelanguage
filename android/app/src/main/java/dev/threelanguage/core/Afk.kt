package dev.threelanguage.core

/**
 * Анти-АФК «темп-монитор» — порт reference.py (docs/AFK.md).
 * Правило из ТЗ: ≥×10 от медианы темпа => вес = медиана, замер не идёт в среднее.
 */
object Afk {
    const val IDLE_CUT_MS = 25_000L
    const val PACE_MULTIPLIER = 10
    const val MIN_CLEAN_SAMPLES = 5
    const val CEILING_MS = 600_000L
    const val MICRO_FLOOR_MS = 120_000L
    const val WINDOW_N = 30

    class PaceStats {
        val samples: MutableMap<String, MutableList<Long>> = mutableMapOf()

        fun median(kind: String): Long? {
            val arr = samples[kind]?.sorted() ?: return null
            if (arr.isEmpty()) return null
            val m = arr.size / 2
            return if (arr.size % 2 == 1) arr[m] else (arr[m - 1] + arr[m]) / 2
        }

        fun percentile(kind: String, p: Double): Long? {
            val arr = samples[kind]?.sorted() ?: return null
            if (arr.isEmpty()) return null
            val k = Math.round((arr.size - 1) * p).toInt().coerceIn(0, arr.size - 1)
            return arr[k]
        }

        fun add(kind: String, activeMs: Long) {
            val arr = samples.getOrPut(kind) { mutableListOf() }
            arr.add(activeMs)
            while (arr.size > WINDOW_N) arr.removeAt(0)
        }
    }

    data class Verdict(val afkSuspect: Boolean, val weightMs: Long, val recordToPace: Boolean)

    fun judgeTask(kind: String, activeMs: Long, pace: PaceStats): Verdict {
        val med = pace.median(kind)
        val limit = if (med == null || (pace.samples[kind]?.size ?: 0) < MIN_CLEAN_SAMPLES)
            CEILING_MS else maxOf(PACE_MULTIPLIER * med, MICRO_FLOOR_MS)
        val suspect = activeMs > limit
        val weight = if (suspect && med != null) med else activeMs
        return Verdict(suspect, weight, !suspect)
    }

    /** Активное время по журналу событий (input|focus_out|focus_in), как в эталоне. */
    fun activeMsFromEvents(events: List<Pair<Long, String>>, taskEndMs: Long): Long {
        var total = 0L; var lastEvent = 0L; var focused = true
        for ((tRaw, typ) in events) {
            val t = minOf(tRaw, taskEndMs)
            val gap = t - lastEvent
            if (focused) total += if (gap > IDLE_CUT_MS) IDLE_CUT_MS else maxOf(0L, gap)
            when (typ) {
                "focus_out" -> focused = false
                "focus_in" -> { focused = true; lastEvent = t; continue }
            }
            lastEvent = t
        }
        if (focused) {
            val gap = taskEndMs - lastEvent
            total += if (gap > IDLE_CUT_MS) minOf(gap, IDLE_CUT_MS) else maxOf(0L, gap)
        }
        return total
    }
}
