const $ = (s) => document.querySelector(s);
const LANG_NAMES = { python: "Python", javascript: "JavaScript", cpp: "C++", java: "Java" };

const EXAMPLES = {
  lds: `Given a set of distinct positive integers, return the largest subset such that every pair (a, b) in the subset satisfies a % b == 0 or b % a == 0.

If there are multiple subsets of the maximum size, you may return any one of them.

Input: the first line is n, then n integers.
Output: the elements of the subset separated by spaces.`,
  ga: `Given an array of strings, group the anagrams together. You can return the answer in any order; the order of groups and the order of words within a group do not matter.

Input: the first line is n, then n whitespace-separated words.
Output: each group on its own line, words separated by a space.`,
  sum: `Given an array of n integers, return their sum. There is exactly one correct answer.

Input: the first line is n, then n integers.
Output: the sum.`,
};

let HEALTH = null;

async function loadHealth() {
  try {
    HEALTH = await (await fetch("/api/health")).json();
  } catch { HEALTH = { all_languages: ["python","javascript","cpp","java"], available_languages: [], api_key_configured: false }; }

  const langs = $("#langs");
  langs.innerHTML = "";
  // Single-select: pick the ONE base language to build/verify/review. The other
  // three are translated from it. Default to Java (the base), else first available.
  const def = HEALTH.available_languages.includes("java")
    ? "java" : HEALTH.available_languages[0];
  HEALTH.all_languages.forEach((l) => {
    const avail = HEALTH.available_languages.includes(l);
    const id = "lang_" + l;
    const lbl = document.createElement("label");
    if (!avail) lbl.className = "off";
    lbl.innerHTML = `<input type="radio" name="lang" id="${id}" value="${l}" ${l === def ? "checked" : ""} ${avail ? "" : "disabled"}/> ${LANG_NAMES[l]}${avail ? "" : " (no runtime)"}`;
    langs.appendChild(lbl);
  });

  const banner = $("#banner");
  if (!HEALTH.api_key_configured) {
    banner.className = "banner err";
    banner.textContent = "ANTHROPIC_API_KEY is not set on the server. Export it and restart: `export ANTHROPIC_API_KEY=sk-ant-...` then `python serve.py`.";
    banner.classList.remove("hidden");
    $("#run").disabled = true;
  } else if (HEALTH.available_languages.length < HEALTH.all_languages.length) {
    const missing = HEALTH.all_languages.filter((l) => !HEALTH.available_languages.includes(l));
    banner.className = "banner warn";
    banner.textContent = "Some language runtimes are unavailable and will be skipped: " + missing.join(", ");
    banner.classList.remove("hidden");
  }
}

const esc = (s) => String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));

function codeBlock(code) {
  return `<div class="codewrap"><button class="copy">Copy</button><pre class="code"><code>${esc(code)}</code></pre></div>`;
}

// passed/total are Python @property values that asdict() doesn't serialize, so
// derive them from the cases array here.
function execCounts(ex) {
  const cases = (ex && ex.cases) || [];
  return { total: cases.length, passed: cases.filter((c) => c.passed).length };
}

function langBadge(lr) {
  if (!lr.available) return `<span class="badge muted">no runtime</span>`;
  if (!lr.driver_code) return `<span class="badge bad">error</span>`;
  if (lr.translated) return `<span class="badge warn">translated</span>`;
  if (lr.verified_ok) return `<span class="badge ok">verified ✓</span>`;
  return `<span class="badge bad">agent flagged</span>`;
}

function renderExecution(lr) {
  const ex = lr.executions[lr.executions.length - 1];
  if (!ex) return "<p class='muted'>No execution result.</p>";
  let html = "";
  if (ex.driver_compile_error)
    html += `<p class="v-bad">Driver compile error:</p>${codeBlock(ex.driver_compile_error)}`;
  html += `<table><thead><tr><th>Kind</th><th>Submission</th><th>Input</th><th>Result</th><th>Detail</th></tr></thead><tbody>`;
  ex.cases.forEach((c) => {
    if (c.kind === "reference") return;
    html += `<tr><td><span class="kpill">${c.kind}</span></td><td>${esc(c.solution)}</td>` +
      `<td>${esc(c.test_input)}</td>` +
      `<td class="${c.passed ? "v-ok" : "v-bad"}">${c.passed ? "PASS" : "FAIL"}</td>` +
      `<td>${esc(c.detail)}</td></tr>`;
  });
  html += "</tbody></table>";
  if (ex.harness_warnings && ex.harness_warnings.length)
    html += `<details><summary>Harness warnings (${ex.harness_warnings.length})</summary><ul>` +
      ex.harness_warnings.map((w) => `<li class="muted">${esc(w)}</li>`).join("") + "</ul></details>";
  return html;
}

function renderCodeReview(cr) {
  if (!cr) return "";
  const ok = cr.is_correct && !(cr.issues && cr.issues.length);
  let html = `<h4>Validator review <span class="muted">(by agent)</span> <span class="badge ${ok ? "ok" : "bad"}">${ok ? "passed ✓" : "issues found"}</span>` +
    (cr.confidence ? ` <span class="muted">(${esc(cr.confidence)} confidence)</span>` : "") + `</h4>`;
  if (cr.issues && cr.issues.length)
    html += `<ul>` + cr.issues.map((i) => `<li class="v-bad">${esc(i)}</li>`).join("") + `</ul>`;
  if (cr.fix_suggestions) html += `<p class="muted">${esc(cr.fix_suggestions)}</p>`;
  if (cr.reasoning) html += `<details><summary>Review reasoning</summary><p class="muted">${esc(cr.reasoning)}</p></details>`;
  return html;
}

function renderLang(lr) {
  if (!lr.available) return `<p class="muted">${esc(lr.message)}</p>`;
  if (!lr.driver_code)
    return `<p class="banner err">No validator was produced for this language. ` +
      `${esc(lr.message)}</p><p class="muted">This is an API / network / budget ` +
      `error, not a validator-quality issue — check the run log and retry.</p>`;
  if (lr.translated) {
    let html = `<p class="notes"><b>${esc(lr.message || "Translated from the reviewed base validator.")}</b> ` +
      `Not executed by design — it inherits the base validator's verified &amp; reviewed logic. ` +
      `<b>Strategy:</b> ${esc(lr.strategy || "?")}</p>`;
    if (lr.validation_notes) html += `<p class="muted">${esc(lr.validation_notes)}</p>`;
    html += `<h4>Validator-embedded driver <span class="muted">(translated)</span></h4>${codeBlock(lr.driver_code || "")}`;
    return html;
  }
  const ex = lr.executions[lr.executions.length - 1];
  const c = execCounts(ex);
  const counts = ex ? `${c.passed}/${c.total} cases pass` : "";
  let html = `<p class="notes"><b>Strategy:</b> ${esc(lr.strategy || "?")} · <b>${counts}</b> · ${esc(lr.message)}</p>`;
  if (lr.validation_notes) html += `<p class="notes">${esc(lr.validation_notes)}</p>`;
  html += `<h4>Validator-embedded driver</h4>${codeBlock(lr.driver_code || "")}`;
  html += renderCodeReview(lr.code_review);
  if (lr.full_program) {
    html += `<h4>Complete runnable solution <span class="muted">(reference solution + validator in main, includes entry point)</span></h4>${codeBlock(lr.full_program)}`;
  }
  if (lr.solutions && lr.solutions.length) {
    html += `<details><summary>All correct solutions (${lr.solutions.length} approaches)</summary>`;
    lr.solutions.forEach((s) =>
      html += `<p class="muted"><b>${esc(s.label)}</b> — ${esc(s.approach || "")}</p>${codeBlock(s.code)}`);
    html += `</details>`;
  }
  html += `<h4>Test cases (run)</h4>${renderExecution(lr)}`;
  html += `<details><summary>Generated test submissions</summary>`;
  if (lr.test_suite) {
    html += `<p class="muted">reference</p>${codeBlock(lr.test_suite.reference_solution.code)}`;
    (lr.test_suite.equivalent_solutions || []).forEach((s) =>
      html += `<p class="muted">equivalent · ${esc(s.name)} — ${esc(s.note)}</p>${codeBlock(s.code)}`);
    (lr.test_suite.wrong_solutions || []).forEach((s) =>
      html += `<p class="muted">wrong · ${esc(s.name)} — ${esc(s.note)}</p>${codeBlock(s.code)}`);
  }
  html += `</details>`;
  return html;
}

function renderValidatorReview(data) {
  const vr = data.validator_review;
  if (!vr) return "";
  const ok = vr.overall_ok;
  let html = `<div class="panel"><h3>Validator review <span class="badge ${ok ? "ok" : "warn"}">${ok ? "consistent ✓" : "see notes"}</span> ` +
    `<span class="muted">(cross-language)</span></h3>`;
  html += `<p class="notes">${esc(vr.summary || "")}</p>`;
  if (vr.cross_language_consistency) html += `<p class="muted"><b>Consistency:</b> ${esc(vr.cross_language_consistency)}</p>`;
  (vr.per_language || []).forEach((pl) => {
    html += `<p class="notes"><b>${LANG_NAMES[pl.language] || pl.language}</b> <span class="badge ${pl.ok ? "ok" : "bad"}">${pl.ok ? "ok" : "issues"}</span></p>`;
    if (pl.issues && pl.issues.length)
      html += `<ul>` + pl.issues.map((i) => `<li class="v-bad">${esc(i)}</li>`).join("") + `</ul>`;
    if (!pl.ok && pl.fix_suggestions) html += `<p class="muted">${esc(pl.fix_suggestions)}</p>`;
  });
  html += `</div>`;
  return html;
}

function renderResults(data) {
  const r = $("#results");
  r.innerHTML = "";
  r.classList.remove("hidden");

  // classification
  const cls = data.classification || {};
  const accepted = data.accepted;
  let head = `<div class="panel"><h3>Classification ` +
    (accepted ? `<span class="badge ok">${esc(data.category)}</span>` : `<span class="badge bad">${esc(data.category || "SINGLE")}</span>`) +
    `</h3><p class="cat">${esc(cls.short_summary || data.message || "")}</p>` +
    `<details><summary>Reasoning</summary><p class="muted">${esc(cls.reasoning || "")}</p></details></div>`;
  r.innerHTML += head;

  if (!accepted) {
    r.innerHTML += `<div class="panel"><p>${esc(data.message)}</p><p class="muted">This tool only transforms multiple-outputs questions; single-output questions are left to the normal autograder.</p></div>`;
    return;
  }

  // test cases (the coverage-assessment write-up was removed by request — just
  // list the test inputs that are generated and run on the base language)
  const inputs = data.inputs || [];
  let tcHtml = `<div class="panel"><h3>Test cases <span class="muted">(${inputs.length})</span></h3><ul>`;
  inputs.forEach((t) => tcHtml += `<li class="muted"><span class="kpill">${esc(t.kind || "normal")}</span> <b>${esc(t.name)}</b>: <code>${esc(JSON.stringify(t.stdin))}</code></li>`);
  tcHtml += `</ul>`;
  if (data.test_cases_path) tcHtml += `<p class="muted">Written to <code>${esc(data.test_cases_path)}</code>.</p>`;
  tcHtml += `</div>`;
  r.innerHTML += tcHtml;

  // cross-language validator review
  r.innerHTML += renderValidatorReview(data);

  // per-language tabs
  const langs = Object.keys(data.languages || {});
  let tabs = `<div class="panel"><div class="tabs">`;
  langs.forEach((l, i) => {
    tabs += `<button class="tab ${i === 0 ? "active" : ""}" data-lang="${l}">${LANG_NAMES[l] || l} ${langBadge(data.languages[l])}</button>`;
  });
  tabs += `</div><div id="lang-body"></div></div>`;
  r.innerHTML += tabs;

  const body = $("#lang-body");
  const show = (l) => { body.innerHTML = renderLang(data.languages[l]); };
  if (langs.length) show(langs[0]);
  document.querySelectorAll(".tab").forEach((t) =>
    t.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
      t.classList.add("active");
      show(t.dataset.lang);
    }));

  document.querySelectorAll(".copy").forEach((b) =>
    b.addEventListener("click", () => {
      navigator.clipboard.writeText(b.parentElement.querySelector("code").textContent);
      b.textContent = "Copied"; setTimeout(() => (b.textContent = "Copy"), 1200);
    }));
}

async function run() {
  const description = $("#desc").value.trim();
  if (!description) { alert("Enter a problem description."); return; }
  const sel = document.querySelector('#langs input[name="lang"]:checked');
  if (!sel) { alert("Select a base language."); return; }
  const languages = [sel.value];   // base language; the other 3 are translated from it

  $("#run").disabled = true;
  $("#results").classList.add("hidden");
  $("#status").classList.remove("hidden");
  $("#log-wrap").classList.remove("hidden");
  $("#log").textContent = "";
  $("#status-text").textContent = "Working…";

  try {
    const res = await fetch("/api/process", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ description, languages, max_iterations: Number($("#iters").value) }),
    });
    const data = await res.json();
    if (data.log) $("#log").textContent = data.log.map((e) => `[${e.stage}] ${e.message}`).join("\n");
    if (!res.ok) {
      $("#banner").className = "banner err";
      $("#banner").textContent = data.error || "Request failed.";
      $("#banner").classList.remove("hidden");
    } else {
      renderResults(data);
    }
  } catch (e) {
    $("#banner").className = "banner err";
    $("#banner").textContent = "Network error: " + e;
    $("#banner").classList.remove("hidden");
  } finally {
    $("#status").classList.add("hidden");
    $("#run").disabled = !HEALTH || !HEALTH.api_key_configured;
  }
}

document.addEventListener("DOMContentLoaded", async () => {
  await loadHealth();
  $("#run").addEventListener("click", run);
  document.querySelectorAll("[data-ex]").forEach((b) =>
    b.addEventListener("click", () => { $("#desc").value = EXAMPLES[b.dataset.ex]; }));

  // Optional auto-run for demos/screenshots: ?demo=lds|ga|sum&langs=python,java
  const p = new URLSearchParams(location.search);
  const demo = p.get("demo");
  if (demo && EXAMPLES[demo]) {
    $("#desc").value = EXAMPLES[demo];
    const want = (p.get("langs") || "").split(",").filter(Boolean);
    if (want.length) {
      // Single-select: pick the first requested base language that has a runtime.
      const target = want.find((w) => {
        const el = document.querySelector(`#langs input[value="${w}"]`);
        return el && !el.disabled;
      });
      if (target) document.querySelector(`#langs input[value="${target}"]`).checked = true;
    }
    if (p.get("iters")) $("#iters").value = p.get("iters");
    if (!$("#run").disabled) run();
  }
});
