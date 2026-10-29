package dev.threelanguage.core

import kotlinx.serialization.json.*
import java.io.File
import kotlin.math.abs
import kotlin.test.Test
import kotlin.test.assertEquals

/**
 * Golden-прогон Kotlin-ядра по тем же fixtures, что и C++/Python.
 * Путь к фикстурам задаётся системным свойством tl.fixtures (см. tools/ci.sh).
 */
class CoreFixtureTest {
    private val json = Json { ignoreUnknownKeys = true }
    private fun fx(name: String): JsonObject =
        json.parseToJsonElement(File(System.getProperty("tl.fixtures"), name).readText()).jsonObject

    @Test fun srsCases() {
        for (c in fx("srs_cases.json")["cases"]!!.jsonArray.map { it.jsonObject }) {
            val cardObj = c["card"]!!.jsonObject
            val card = CardState(
                cardObj["stability"]!!.double, cardObj["difficulty"]!!.double,
                cardObj["due_ms"]!!.long, cardObj["reps"]!!.int,
                cardObj["lapses"]!!.int, cardObj["state"]!!.str)
            val exp = c["expected"]!!.jsonObject
            val got = Srs.nextState(card, c["grade"]!!.int, c["now_ms"]!!.long)
            assertEquals(exp["state"]!!.str, got.state, "state ${c["name"]}")
            assertEquals(exp["due_ms"]!!.long, got.dueMs, "due ${c["name"]}")
            assert(abs(got.stability - exp["stability"]!!.double) < 1e-6)
            assert(abs(got.difficulty - exp["difficulty"]!!.double) < 1e-6)
        }
    }

    @Test fun paceCases() {
        for (c in fx("pace_cases.json")["cases"]!!.jsonArray.map { it.jsonObject }) {
            val pace = Afk.PaceStats()
            c["samples"]!!.jsonObject["K"]!!.jsonArray.forEach { pace.add("K", it.long) }
            val v = Afk.judgeTask("K", c["active_ms"]!!.long, pace)
            val exp = c["expected"]!!.jsonObject
            assertEquals(exp["afk_suspect"]!!.boolean, v.afkSuspect, c["name"].str)
            assertEquals(exp["weight_ms"]!!.long, v.weightMs, c["name"].str)
            assertEquals(exp["record_to_pace"]!!.boolean, v.recordToPace, c["name"].str)
        }
    }
}
