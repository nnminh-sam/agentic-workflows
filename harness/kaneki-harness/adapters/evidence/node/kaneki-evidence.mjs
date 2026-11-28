// kaneki-evidence: record what a test observed for one check of the ticket's test cases (TC-n.aN).
//
//   import { evidence } from "./support/kaneki-evidence.mjs";
//   const res = await fetch(`${base}/learning/x`);
//   evidence("TC-61.a1", { observed: res.status, expected: 404 });
//   assert.equal(res.status, 404);
//
// While `sdlc evidence run` runs a case it sets KANEKI_EVIDENCE_FILE; each call appends one JSON line there with the
// check id, the observed and expected values, whether they match, and the test line that made the call. The harness
// turns those lines into evidence.json (an observed value and a source per check). Outside a run (CI, a developer's
// machine) the variable is unset and evidence() does nothing, so the test stays an ordinary regression test.
// Copied into the repository by `sdlc evidence helper`; it has no dependencies.

import { appendFileSync } from "node:fs";
import { isDeepStrictEqual } from "node:util";

const CHECK_ID = /^TC-\d+\.a\d+$/;

function callerRef() {
  const lines = String(new Error().stack || "").split("\n").slice(1);
  for (const line of lines) {
    if (line.includes("kaneki-evidence")) continue;
    const m = line.match(/\(?((?:file:\/\/)?[^()\s]+?):(\d+):\d+\)?\s*$/);
    if (!m) continue;
    let file = m[1].replace(/^file:\/\/\/?/, "");
    const cwd = process.cwd().replace(/\\/g, "/");
    file = file.replace(/\\/g, "/");
    if (file.toLowerCase().startsWith(cwd.toLowerCase() + "/")) file = file.slice(cwd.length + 1);
    return `${file}:${m[2]}`;
  }
  return undefined;
}

/**
 * @param {string} check  the check id, TC-<n>.a<m>
 * @param {{observed: unknown, expected?: unknown, pass?: boolean, note?: string, param?: unknown}} what
 * @returns {boolean} whether the observation matches (pass, else observed deep-equals expected)
 */
export function evidence(check, what = {}) {
  if (!CHECK_ID.test(String(check))) throw new Error(`evidence(): "${check}" is not a check id like TC-12.a1`);
  const hasExpected = Object.prototype.hasOwnProperty.call(what, "expected");
  const pass = typeof what.pass === "boolean" ? what.pass
    : hasExpected ? isDeepStrictEqual(what.observed, what.expected) : undefined;
  const file = process.env.KANEKI_EVIDENCE_FILE;
  if (file) {
    const row = { check, observed: what.observed === undefined ? null : what.observed,
                  pass: pass === undefined ? null : pass, ref: callerRef(),
                  at: new Date().toISOString() };
    if (hasExpected) row.expected = what.expected;
    if (what.note) row.note = String(what.note);
    if (what.param !== undefined) row.param = what.param;
    appendFileSync(file, JSON.stringify(row) + "\n", "utf8");
  }
  return pass === true;
}

export default evidence;
