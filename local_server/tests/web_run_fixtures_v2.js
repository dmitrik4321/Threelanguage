"use strict";
/* Паритетный прогон shared/algorithm/web_core.js на fixtures ядра.
 * Вызывается из local_server/tests/test_web_parity.py через node; печатает
 * JSON-карту {fixturePath: {srs:{name:...}, afk:{name:...}, pace:{name:...}}}
 * (по именам кейсов — тот же контракт, что ожидает python-тест). */
const C = require("../../shared/algorithm/web_core.js");

function runFixture(fx) {
  const out = { srs: {}, afk: {}, pace: {} };
  for (const c of fx.cases || []) {
    if (c.card !== undefined && c.grade !== undefined) {           // srs_cases
      let card = C.newCard(c.card);
      card = C.nextState(card, c.grade, c.now_ms);
      out.srs[c.name] = card;
    } else if (c.events !== undefined) {                            // afk_cases
      const active = C.activeMsFromEvents(c.events, c.task_end_ms);
      const v = C.judgeTask(c.kind || "typing", active,
                            { samples: c.samples || {} });
      out.afk[c.name] = { active_ms: active, verdict: v };
    } else if (c.samples !== undefined && c.active_ms !== undefined) { // pace_cases
      const v = C.judgeTask(c.kind || "typing", c.active_ms,
                            { samples: c.samples });
      out.pace[c.name] = { afk_suspect: v.afk_suspect,
                           weight_ms: v.weight_ms,
                           record_to_pace: v.record_to_pace };
    }
  }
  return out;
}

const results = {};
for (const f of process.argv.slice(2)) {
  results[f] = runFixture(JSON.parse(require("fs").readFileSync(f, "utf8")));
}
process.stdout.write(JSON.stringify(results));
