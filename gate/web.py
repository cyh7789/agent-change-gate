"""The gate made visible: progress, the comparison and the approval buttons on one page.

    python3 -m gate.web --spec agents/issue-triage.json \
                        --candidate agents/issue-triage.candidate.json \
                        --scenarios scenarios/issue-triage.json \
                        --repo owner/name

The evaluation runs on a background thread and the page polls state once a second. The
approval buttons are the one path running the other way: pressing one releases the thread
waiting at the gate.
"""
from __future__ import annotations

import argparse
import hmac
import http.server
import json
import secrets
import socketserver
import threading
import webbrowser
from pathlib import Path

from . import analysis, report, runner, writeback
from .scenarios import load
from .state import GateState

# Raw string: this is HTML and JS whose backslashes must reach the browser untouched. Without
# the r prefix, the \/ in a JS regex is an invalid escape on Python 3.12+ and a SyntaxError
# from 3.15.
PAGE = r"""<!doctype html>
<meta charset="utf-8"><title>Agent Change Gate</title>
<style>
 /* Layout follows the conventions of tools like this: a row of named phases, the verdict as
    the headline, details collapsed by default, and the decision card saying what the action
    will touch. Waiting on a person is not a failure, so it is amber and not red. */
 :root { color-scheme: dark;
   --bg:#0e1016; --panel:#151823; --line:#232838; --ink:#e8eaf2; --dim:#8990a8;
   --blue:#5b8cff; --green:#3fb37f; --amber:#e0a83c; --red:#e35d5d; --violet:#a98bf5; }
 * { box-sizing:border-box; }
 body { font:15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, system-ui, sans-serif;
        background:var(--bg); color:var(--ink); margin:0; display:flex; min-height:100vh; }
 code, .mono, table { font-family:ui-monospace, SFMono-Regular, Menlo, monospace; }

 /* Left column: what this run is, plus section anchors. Without it the page is only content and reads like a report rather than a tool */
 nav { width:216px; flex:none; border-right:1px solid var(--line); background:#0b0d13;
       padding:22px 16px; display:flex; flex-direction:column; gap:20px; }
 nav h1 { font-size:15px; margin:0; font-weight:650; letter-spacing:-.01em; }
 nav .who { font-size:12px; color:var(--dim); line-height:1.5; word-break:break-all; }
 nav .who b { display:block; color:#aab2c8; font-weight:500; }
 nav ul { list-style:none; margin:0; padding:0; }
 nav li a { display:flex; justify-content:space-between; gap:8px; padding:6px 9px;
            border-radius:7px; color:var(--dim); font-size:13px; text-decoration:none; }
 nav li a:hover { background:var(--panel); color:var(--ink); }
 nav li a i { font-style:normal; font-size:11px; color:#5a6076; }
 nav .foot { margin-top:auto; font-size:11.5px; color:#5a6076; display:flex;
             align-items:center; gap:7px; }
 .pulse { width:7px; height:7px; border-radius:50%; background:var(--green);
          animation:pulse 2s ease-in-out infinite; }
 @keyframes pulse { 0%,100%{opacity:.25} 50%{opacity:1} }

 main { flex:1; padding:26px 34px 70px; min-width:0; }
 header { display:flex; align-items:baseline; gap:14px; margin-bottom:14px; }
 h1 { font-size:19px; margin:0; font-weight:600; letter-spacing:-.01em; }
 .repo { color:var(--dim); font-size:13px; }

 /* Parameter row: the configuration this run actually used. This used to be one line of small text */
 .params { display:flex; gap:7px; flex-wrap:wrap; align-items:center; margin-bottom:18px; }
 .param { border:1px solid var(--line); background:var(--panel); border-radius:7px;
          padding:4px 9px; font-size:11.5px; color:var(--dim);
          font-family:ui-monospace, SFMono-Regular, Menlo, monospace; }
 .param b { color:#aab2c8; font-weight:500; }
 .ghost { margin-left:auto; background:transparent; border:1px solid var(--line);
          color:var(--dim); font-size:12px; padding:5px 11px; border-radius:7px; }
 .ghost:hover { color:var(--ink); border-color:#3a4157; }

 /* Table filter: usually 1 of the 16 rows has something to say, so let the reader pick the group */
 .chips { display:flex; gap:6px; margin:16px 0 2px; }
 .chip { border:1px solid var(--line); background:transparent; color:var(--dim);
         font-size:12px; padding:4px 11px; border-radius:999px; }
 .chip.on { color:var(--ink); border-color:var(--blue); background:#16203a; }

 /* Phase row: each cell says what it is doing rather than being a coloured block */
 .steps { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:8px; }
 .step { font-size:12.5px; padding:5px 11px; border-radius:999px; border:1px solid var(--line);
         color:var(--dim); background:var(--panel); white-space:nowrap; }
 .step.done { color:var(--green); border-color:#1f3a2e; }
 .step.now  { color:var(--ink); border-color:var(--blue); background:#16203a; }
 .step.wait { color:var(--amber); border-color:#3d3117; background:#211a09; }
 .step.bad  { color:var(--red); border-color:#3d1f1f; background:#210d0d; }
 /* The evaluation runs for twenty minutes. A thin bar in low contrast looks frozen, and the
    whole point of this stretch is that work is happening. A taller bar with a track and moving
    stripes still reads as running when the numbers hold still for thirty seconds. */
 .bar { height:10px; background:#1b1f2c; border:1px solid var(--line); border-radius:6px;
        overflow:hidden; margin:16px 0 8px; }
 .bar > div { height:100%; width:0; border-radius:5px; transition:width .4s ease;
              background:linear-gradient(90deg, #3f6fd8, var(--blue));
              box-shadow:0 0 14px rgba(91,140,255,.55); position:relative; }
 .bar > div::after { content:""; position:absolute; inset:0; border-radius:5px;
   background:repeating-linear-gradient(115deg, rgba(255,255,255,.16) 0 12px,
                                        transparent 12px 26px);
   animation:crawl 1.1s linear infinite; }
 .bar.done > div::after { display:none; }   /* once finished, stop pretending to move */
 @keyframes crawl { to { background-position:26px 0; } }
 .runline { color:var(--dim); font-size:13px; display:flex; gap:10px; align-items:baseline; }
 .runline b { color:var(--ink); font-weight:600; font-size:15px;
              font-family:ui-monospace, SFMono-Regular, Menlo, monospace; }
 .runline .arm { color:var(--blue); }

 /* The verdict as the headline: large, coloured by outcome, with one line under it saying why */
 .verdict { margin:26px 0 4px; }
 .verdict .v { font-size:30px; font-weight:650; letter-spacing:-.02em; }
 .verdict .why { color:var(--dim); font-size:14px; margin-top:6px; }
 /* During the run, progress is the headline: the number carries the same weight the verdict
    will, so something on screen is moving. When it finishes the verdict takes the same slot. */
 .verdict .count { font-size:52px; font-weight:680; letter-spacing:-.03em;
                   font-family:ui-monospace, SFMono-Regular, Menlo, monospace;
                   line-height:1.05; }
 .verdict .count i { font-style:normal; color:#4a5169; }
 .verdict .count u { text-decoration:none; color:var(--blue); }
 .v-noise { color:var(--ink); } .v-improve { color:var(--green); }
 .v-regress { color:var(--red); }

 .cards { display:flex; gap:10px; flex-wrap:wrap; margin:20px 0 4px; }
 .card { background:var(--panel); border:1px solid var(--line); border-radius:10px;
         padding:11px 15px; min-width:112px; }
 .card b { display:block; font-size:20px; font-weight:600; }
 .card span { color:var(--dim); font-size:11px; text-transform:uppercase; letter-spacing:.07em; }

 /* Approval: the three writes are a checklist, and the steps not yet reached stay visible */
 .gate { background:#1a1408; border:1px solid #46381a; border-radius:12px;
         padding:18px 20px; margin:22px 0; }
 .gate h2 { font:600 13px/1 -apple-system, system-ui, sans-serif; margin:0 0 12px;
            color:var(--amber); text-transform:uppercase; letter-spacing:.08em; }
 .call { font-size:18px; font-weight:600; margin-bottom:10px; }
 .args { border-collapse:collapse; font-size:13px; margin-bottom:14px; }
 .args td { padding:2px 18px 2px 0; }
 .args td:first-child { color:var(--dim); }
 .scope { color:#d8c79a; font-size:13px; margin:0 0 14px; max-width:62ch; }
 button { font:600 14px/1 -apple-system, system-ui, sans-serif; padding:10px 18px;
          border-radius:8px; border:0; cursor:pointer; margin-right:10px; }
 .allow { background:#2f7d4f; color:#fff; } .deny { background:#3a2020; color:#f0b4b4; }
 .checklist { list-style:none; padding:0; margin:16px 0 0; font-size:13px; }
 .checklist li { padding:3px 0; color:var(--dim); }
 .checklist .ok::before   { content:"✓ "; color:var(--green); }
 .checklist .no::before   { content:"✕ "; color:var(--red); }
 .checklist .open::before { content:"● "; color:var(--amber); }
 .checklist .todo::before { content:"○ "; }
 .checklist .open { color:var(--ink); }

 /* Per-scenario progress: one cell per scenario, one dot per completed run, so the fan-out is visibly working */
 .live { display:grid; grid-template-columns:repeat(auto-fill, minmax(196px, 1fr));
         gap:8px; margin-top:20px; }
 .cell { background:var(--panel); border:1px solid var(--line); border-radius:9px;
         padding:9px 12px; }
 .cell .id { font-size:12px; color:var(--dim); margin-bottom:6px; }
 .cell .arm { display:flex; align-items:center; gap:5px; margin-top:3px; }
 .cell .arm em { font-style:normal; font-size:10px; color:#5a6076; width:58px;
                 text-transform:uppercase; letter-spacing:.05em; }
 .dot { width:8px; height:8px; border-radius:50%; background:#232838; }
 .dot.pass { background:var(--green); } .dot.fail { background:var(--red); }
 .dot.fresh { animation:pop .55s ease-out; }
 @keyframes pop { 0% { transform:scale(.4); box-shadow:0 0 0 0 rgba(63,179,127,.8); }
                  60% { transform:scale(1.5); }
                  100% { transform:scale(1); box-shadow:0 0 0 9px rgba(63,179,127,0); } }
 table.rows { border-collapse:collapse; width:100%; margin-top:10px; font-size:13px; }
 table.rows th, table.rows td { text-align:left; padding:6px 12px 6px 0; border-bottom:1px solid #1a1e2c; }
 table.rows th { color:var(--dim); font-weight:normal; font-size:11px;
                 text-transform:uppercase; letter-spacing:.06em; }
 .same { color:#59617a; } .flaky { color:var(--amber); } .broken { color:var(--red); }
 .fixed { color:var(--green); } .unproven { color:var(--violet); } .incomplete { color:var(--violet); }
 .fold { color:var(--dim); cursor:pointer; user-select:none; padding:7px 0; font-size:13px; }
 .fold:hover { color:var(--ink); }
 h3 { font-size:12px; text-transform:uppercase; letter-spacing:.07em;
      color:var(--dim); font-weight:600; margin:30px 0 0; }
 pre { white-space:pre-wrap; color:#b7bed4; background:var(--panel); border:1px solid var(--line);
       border-radius:10px; padding:15px 17px; font-size:12.5px; margin-top:10px; }
 .landed { background:#0f1e16; border:1px solid #1f3a2e; border-radius:12px;
           padding:16px 20px; margin:22px 0; }
 a { color:var(--blue); }
</style>
<nav>
  <div><h1>Agent Change Gate</h1><div class="who" id="who"></div></div>
  <ul id="nav"></ul>
  <div class="foot"><span class="pulse"></span><span id="tickinfo">polling /state</span></div>
</nav>
<main>
  <header><h1 id="phasehead">starting</h1><span class="repo" id="repo"></span></header>
  <div class="steps" id="steps"></div>
  <div class="params" id="params"></div>
  <div id="gate"></div>
  <div id="verdict" class="verdict"></div>
  <div class="bar" id="bar"><div id="fill"></div></div>
  <div class="runline" id="progress"></div>
  <div class="cards" id="cards"></div>
  <div class="live" id="live"></div>
  <div id="table"></div>
  <div id="analysis"></div>
</main>
<script>
const TOKEN = "__TOKEN__";
const $ = id => document.getElementById(id);
// Some of what this page shows is written by an agent: tool names and arguments, failure
// reasons, the sandbox's analysis. Passing that to innerHTML would let the agent under
// evaluation decide what this page executes.
const esc = v => String(v ?? '').replace(/[&<>"']/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
// The heading says what is happening in a sentence instead of dumping the internal phase string
const PHASE_TITLE = {
  'starting': 'starting up',
  'evaluating': 'running the scenario set against both specs',
  'reading the results in the sandbox': 'reading the results in a sandbox',
  'report ready': 'report ready',
  'awaiting-approval': 'waiting for you to decide',
  'landing': 'landing the change',
  'landed': 'landed on GitHub',
  'rejected': 'rejected — nothing reached GitHub',
  'failed': 'run failed',
};
const cls = d => ({same:'same',flaky:'flaky',broken:'broken',fixed:'fixed',
                   unproven:'unproven',incomplete:'incomplete'}[d]||'');
let filter = 'all';   // all | moved | same

// Phase names explain themselves, so the reader does not have to know this tool first
const STEPS = [
  ['evaluating baseline',   s => s.arm === 'baseline' && s.phase === 'evaluating'],
  ['evaluating candidate',  s => s.arm === 'candidate' && s.phase === 'evaluating'],
  ['reading it in a sandbox', s => s.phase.startsWith('reading')],
  ['waiting for a person',  s => s.phase === 'awaiting-approval'],
  ['landed on GitHub',      s => s.phase === 'landed'],
];
function steps(s) {
  const at = STEPS.findIndex(([, is]) => is(s));
  return STEPS.map(([label], i) => {
    let k = 'todo';
    if (s.phase === 'failed' || s.error) k = i === 0 ? 'bad' : '';
    else if (i === at) k = label === 'waiting for a person' ? 'wait' : 'now';
    else if (at < 0 ? s.phase === 'landed' || s.phase === 'rejected' || s.phase.startsWith('done')
                    : i < at) k = 'done';
    return `<span class="step ${k}">${esc(label)}</span>`;
  }).join('');
}

// The verdict carries its reason, and the line beside it says how it was computed
function verdict(s) {
  if (!s.verdict) {
    if (!s.runs_total) return '';
    const pct = Math.round(100 * s.runs_done / s.runs_total);
    return `<div class="count"><u>${s.runs_done}</u><i> / ${s.runs_total}</i></div>
            <div class="why">scenario runs, one subagent each · ${pct}% done`
         + (s.arm ? ` · running the ${esc(s.arm)} spec` : '') + `</div>`;
  }
  const m = s.summary || {};
  const tone = s.verdict.includes('improvement') ? 'v-improve'
             : s.verdict.includes('regress') || s.verdict.includes('broke') ? 'v-regress' : 'v-noise';
  const bits = [];
  if (m.fixed) bits.push(`${m.fixed} fixed`);
  if (m.broken) bits.push(`${m.broken} broken`);
  if (m.flaky) bits.push(`${m.flaky} unstable in at least one arm`);
  if (m.unproven) bits.push(`${m.unproven} moved but not past the significance test`);
  if (m.incomplete) bits.push(`${m.incomplete} never finished`);
  const same = (s.rows||[]).filter(r => r.delta === 'same').length;
  if (same) bits.push(`${same} identical in both arms`);
  const cost = s.baseline_tokens ? (s.candidate_tokens / s.baseline_tokens) : 0;
  const costed = cost ? ` Costs ${cost.toFixed(2)}× the tokens of the current spec.` : '';
  return `<div id="verdict-anchor"></div><div class="v ${tone}">${esc(s.verdict)}</div>
          <div class="why">${esc(bits.join(' · '))}.${esc(costed)}</div>`;
}

// Approval card: the tool name as the title, its arguments underneath, and beside the buttons what this decision touches
function gate(s) {
  if (s.pending) {
    const c = s.pending;
    const name = [c.server, c.tool].filter(Boolean).join('.') || 'a tool call';
    const args = Object.entries(c.input || {}).map(([k, v]) =>
      `<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join('');
    return `<div class="gate">
      <h2>waiting for a person</h2>
      <div class="call mono">${esc(name)}</div>
      ${args ? `<table class="args">${args}</table>` : ''}
      <p class="scope">Approving lets the agent make this one call against
         ${esc(s.repo || 'the repository')}. Reject and the call is refused:
         nothing reaches GitHub, and the run stops here.</p>
      <button class="allow" onclick="decide(true)">Approve this write</button>
      <button class="deny" onclick="decide(false)">Reject</button>
      ${checklist(s)}</div>`;
  }
  if (s.result) {
    return `<div class="landed">${esc(s.result).replace(/(https?:\/\/\S+)/,
              '<a href="$1" target="_blank">$1</a>')}</div>`;
  }
  return (s.decided||[]).length ? `<div class="gate">${checklist(s)}</div>` : '';
}

// "Not yet" is three named steps rather than one greyed-out button.
// Which step stands where is computed server-side (GateState.writes); this page only draws it
const MARK = {done:'ok', refused:'no', open:'open', todo:'todo'};
function checklist(s) {
  const items = (s.writes || []).map(w =>
    `<li class="${MARK[w.state] || 'todo'}">${esc(w.label)}</li>`).join('');
  return `<ul class="checklist">${items}</ul>`;
}

// Usually 1 of the 16 rows has something to say, so that group is the default and the rest are a click away
function table(s) {
  const rows = s.rows || [];
  if (!rows.length) return '';
  const moved = rows.filter(r => r.delta !== 'same');
  const same = rows.filter(r => r.delta === 'same');
  const shown = filter === 'moved' ? moved : filter === 'same' ? same : rows;
  const chip = (k, label, n) =>
    `<button class="chip ${filter === k ? 'on' : ''}" onclick="setFilter('${k}')">${
      esc(label)} ${n}</button>`;
  const chips = `<div class="chips">${chip('all','all',rows.length)}${
    chip('moved','moved',moved.length)}${chip('same','same',same.length)}</div>`;
  const tr = r => `<tr><td>${esc(r.id)}</td><td>${esc(r.baseline)}</td><td>${esc(r.candidate)}</td>
    <td class="${cls(r.delta)}">${esc(r.delta)}</td><td>${esc((r.why||'').slice(0,70))}</td></tr>`;
  return `<h3 id="scenarios">per scenario</h3>` + chips +
    `<table class="rows"><tr><th>scenario</th><th>baseline</th><th>candidate</th>
     <th>change</th><th>why</th></tr>` + shown.map(tr).join('') + '</table>';
}
function setFilter(k) { filter = k; tick(); }

// Left column: what this run is, plus numbered section anchors
function rail(s) {
  const rows = s.rows || [];
  const moved = rows.filter(r => r.delta !== 'same').length;
  const links = [
    ['verdict', 'Verdict', s.verdict ? '' : 'pending'],
    ['scenarios', 'Scenarios', rows.length ? `${moved}/${rows.length} moved`
                                           : `${s.runs_done}/${s.runs_total || '?'} runs`],
    ['approvals', 'Approvals', s.repo ? `${(s.writes||[]).filter(w => w.state === 'done').length}/${(s.writes||[]).length} writes` : 'n/a'],
    ['sandbox', 'Sandbox read', s.analysis ? 'ready' : '–'],
  ];
  return links.map(([id, label, note]) =>
    `<li><a href="#${id}">${esc(label)}<i>${esc(note)}</i></a></li>`).join('');
}

// Parameter row: which measuring stick, how many repeats, how many scenarios per batch
function params(s) {
  const p = (k, v) => v ? `<span class="param">${esc(k)} <b>${esc(v)}</b></span>` : '';
  return p('scenarios', s.source ? s.source.replace(/^https?:\/\//, '') : '')
       + p('digest', s.digest ? s.digest.slice(0, 12) + '…' : '')
       + p('repeat', s.repeats || '')
       + p('batch', s.batch_size || '')
       + (s.report_md
          ? `<button class="ghost" onclick="copyReport()">Copy report</button>` : '');
}
async function copyReport() {
  const md = await (await fetch('/report.md')).text();
  await navigator.clipboard.writeText(md);
  document.querySelector('.ghost').textContent = 'Copied';
}

// The evaluation runs for over ten minutes, and a lone progress bar leaves the screen empty
// for all of it. One cell per scenario and one dot per completed run makes the fan-out visible.
let seen = {};   // how many dots each cell had last time, used to spot the ones added this round
function live(s) {
  if (s.verdict || !Object.keys(s.live || {}).length) return '';
  const slots = s.repeats || 1;
  const next = {};
  const html = Object.keys(s.live).sort().map(id => {
    const arms = ['baseline', 'candidate'].map(arm => {
      const got = (s.live[id] || {})[arm] || [];
      const key = id + '|' + arm;
      const before = seen[key] || 0;
      next[key] = got.length;
      const dots = Array.from({length: slots}, (_, i) => {
        if (i >= got.length) return '<span class="dot"></span>';
        // Only the dots added this round flash: the page redraws every second, and everything
        // flashing is the same as nothing flashing
        const fresh = i >= before ? ' fresh' : '';
        return `<span class="dot ${got[i] ? 'pass' : 'fail'}${fresh}"></span>`;
      }).join('');
      return `<div class="arm"><em>${arm}</em>${dots}</div>`;
    }).join('');
    return `<div class="cell"><div class="id mono">${esc(id)}</div>${arms}</div>`;
  }).join('');
  seen = next;
  return html;
}

async function tick() {
  const s = await (await fetch('/state')).json();
  $('repo').textContent = s.repo ? s.repo + (s.branch ? ' · ' + s.branch : '') : '';
  $('phasehead').textContent = s.error ? 'run failed' : PHASE_TITLE[s.phase] || s.phase;
  $('who').innerHTML = s.spec
    ? `<b>${esc(s.spec.split('/').pop())}</b>vs <b>${esc(s.candidate.split('/').pop())}</b>`
      + (s.repo ? `<div style="margin-top:8px">${esc(s.repo)}</div>` : '')
    : '';
  $('nav').innerHTML = rail(s);
  $('params').innerHTML = params(s);
  $('tickinfo').textContent = s.phase === 'landed' || s.phase.startsWith('done')
    ? 'run finished' : 'polling /state';
  $('steps').innerHTML = s.error
    ? `<span class="step bad">${esc(s.phase)}: ${esc(s.error)}</span>` : steps(s);
  const pct = s.runs_total ? Math.round(100 * s.runs_done / s.runs_total) : 0;
  $('fill').style.width = pct + '%';
  $('bar').className = 'bar' + (pct >= 100 ? ' done' : '');
  $('progress').innerHTML = s.verdict && s.runs_total
    ? `<span>${s.runs_done} / ${s.runs_total} scenario runs · one subagent each</span>` : '';
  $('gate').innerHTML = gate(s);
  $('verdict').innerHTML = verdict(s);
  const sm = s.summary || {};
  const n = v => v ? v.toLocaleString() : '–';
  $('cards').innerHTML = [
    ['scenarios', s.scenarios || '–'], ['runs per arm', s.repeats || '–'],
    ['flaky', s.verdict ? (sm.flaky ?? 0) : '–'],
    ['incomplete', s.verdict ? (sm.incomplete ?? 0) : '–'],
    ['baseline tok', n(s.baseline_tokens)], ['candidate tok', n(s.candidate_tokens)],
    ['gate stops', s.approvals],
  ].map(([k,v]) => `<div class="card"><b>${esc(v)}</b><span>${esc(k)}</span></div>`).join('');
  $('live').innerHTML = live(s);
  $('table').innerHTML = table(s);
  $('analysis').innerHTML = s.analysis
    ? '<h3 id="sandbox">read by code the agent ran in its sandbox</h3><pre>' + esc(s.analysis) + '</pre>' : '';
}
async function decide(allow) {
  await fetch('/decide', {method:'POST', headers:{'X-Gate-Token': TOKEN},
                          body: JSON.stringify({allow})});
  tick();
}
tick(); setInterval(tick, 1000);
</script>
"""


def run_gate(a, state: GateState) -> None:
    try:
        scenarios = load(a.scenarios)
        base_spec = json.loads(Path(a.spec).read_text())
        cand_spec = json.loads(Path(a.candidate).read_text())
        total = len(scenarios) * max(1, a.repeat)
        state.update(scenarios=len(scenarios), repeats=a.repeat, runs_total=total * 2,
                     repo=a.repo or "", branch=a.branch if a.repo else "",
                     spec=a.spec, candidate=a.candidate, batch_size=a.batch_size,
                     source=scenarios.source, digest=scenarios.digest,
                     phase="evaluating", arm="baseline")

        base = runner.run_arm("baseline", base_spec, scenarios, a.batch_size, a.repeat,
                              on_result=lambda r: state.count_run("baseline", r.scenario_id, r.ok))
        state.update(baseline_tokens=base.total_tokens, arm="candidate")
        cand = runner.run_arm("candidate", cand_spec, scenarios, a.batch_size, a.repeat,
                              on_result=lambda r: state.count_run("candidate", r.scenario_id, r.ok))

        comparison = report.Comparison(scenarios, base, cand)
        state.update(candidate_tokens=cand.total_tokens, rows=comparison.rows(),
                     summary=comparison.summary(), verdict=comparison.verdict(),
                     phase="reading the results in the sandbox", arm="")
        read = None if a.no_analysis else analysis.interpret(comparison.rows())
        md = comparison.to_markdown(read)
        Path("change-gate-report.md").write_text(md + "\n")
        state.update(analysis=read, report_md=md, phase="report ready")

        if not a.repo:
            state.update(phase="done (evaluation only)")
            return
        pending = writeback.propose(repo=a.repo, branch=a.branch, path=a.spec,
                                    content=json.dumps(cand_spec, indent=1),
                                    title=f"Agent spec change: {comparison.verdict()}",
                                    report_md=md)
        if pending is None:
            state.update(phase="done", error="the write-back agent never reached a write tool")
            return
        landed, output = writeback.land(pending, lambda p: state.ask(p.call))
        state.update(phase="landed" if landed else "rejected", result=output)
    except Exception as e:                      # the console has to say what broke, not just stop
        state.update(phase="failed", error=f"{type(e).__name__}: {e}")


def serve(state: GateState, port: int, token: str) -> None:
    """The approval endpoint requires a token.

    Binding to 127.0.0.1 only keeps other machines out. Anything running on this machine can
    POST /decide, and that endpoint releases irreversible actions. The token goes only to
    whoever can load the page.
    """
    page = PAGE.replace("__TOKEN__", token).encode()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, body: bytes, kind: str):
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/state":
                self._send(json.dumps(state.snapshot()).encode(), "application/json")
            elif self.path == "/report.md":
                body = (state.snapshot().get("report_md") or "").encode()
                self._send(body, "text/markdown; charset=utf-8")
            else:
                self._send(page, "text/html; charset=utf-8")

        def do_POST(self):
            if not hmac.compare_digest(self.headers.get("X-Gate-Token", ""), token):
                self.send_error(403, "approval requires the console token")
                return
            length = int(self.headers.get("Content-Length", 0))
            allow = bool(json.loads(self.rfile.read(length) or b"{}").get("allow"))
            state.decide(allow)
            self._send(b"{}", "application/json")

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", port), Handler) as httpd:
        httpd.serve_forever()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gate.web")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--scenarios", required=True)
    ap.add_argument("--repo")
    ap.add_argument("--branch", default="change-gate/candidate")
    ap.add_argument("--batch-size", type=int, default=4)
    ap.add_argument("--repeat", type=int, default=5)
    ap.add_argument("--no-analysis", action="store_true")
    ap.add_argument("--port", type=int, default=8791)
    ap.add_argument("--no-open", action="store_true")
    a = ap.parse_args(argv)

    state = GateState()
    token = secrets.token_urlsafe(16)
    threading.Thread(target=run_gate, args=(a, state), daemon=True).start()
    url = f"http://127.0.0.1:{a.port}/"
    print(f"gate console: {url}")
    if not a.no_open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    serve(state, a.port, token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
