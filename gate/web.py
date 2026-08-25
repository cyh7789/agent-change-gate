"""看得見的 gate：進度、比較表、核准按鈕，全部在一頁上。

    python3 -m gate.web --spec agents/issue-triage.json \
                        --candidate agents/issue-triage.candidate.json \
                        --scenarios scenarios/issue-triage.json \
                        --repo owner/name

評測在背景執行緒跑，頁面每秒輪詢一次狀態。核准按鈕是唯一反向的一條路，
按下去才會解開等在核准閘前面的那個執行緒。
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

PAGE = """<!doctype html>
<meta charset="utf-8"><title>Agent Change Gate</title>
<style>
 :root { color-scheme: dark; }
 body { font: 15px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace;
        background:#11131a; color:#e6e8ee; margin:0; padding:28px 32px; }
 h1 { font-size:20px; margin:0 0 4px; letter-spacing:.02em; }
 .sub { color:#8b93a7; margin-bottom:22px; }
 .bar { height:8px; background:#232735; border-radius:4px; overflow:hidden; margin:10px 0 6px; }
 .bar > div { height:100%; background:#4f8cff; width:0; transition:width .3s; }
 .cards { display:flex; gap:14px; flex-wrap:wrap; margin:18px 0 6px; }
 .card { background:#171a24; border:1px solid #232735; border-radius:8px; padding:12px 16px; min-width:120px; }
 .card b { display:block; font-size:22px; margin-bottom:2px; }
 .card span { color:#8b93a7; font-size:12px; text-transform:uppercase; letter-spacing:.08em; }
 table { border-collapse:collapse; width:100%; margin-top:14px; font-size:13px; }
 th, td { text-align:left; padding:5px 10px; border-bottom:1px solid #1e2230; }
 th { color:#8b93a7; font-weight:normal; font-size:12px; text-transform:uppercase; letter-spacing:.06em; }
 .same { color:#5b6379; } .flaky { color:#e0b341; } .broken { color:#ff6b6b; }
 .fixed { color:#4ade80; } .incomplete { color:#a78bfa; }
 .gate { background:#1d1608; border:1px solid #5c451a; border-radius:8px; padding:18px 20px; margin:20px 0; }
 .gate code { display:block; color:#e0b341; margin:8px 0 14px; word-break:break-all; }
 button { font:inherit; padding:8px 20px; border-radius:6px; border:0; cursor:pointer; margin-right:10px; }
 .allow { background:#2f7d4f; color:#fff; } .deny { background:#7d2f2f; color:#fff; }
 .verdict { font-size:18px; margin:16px 0 4px; }
 pre { white-space:pre-wrap; color:#b9c0d4; background:#171a24; border:1px solid #232735;
       border-radius:8px; padding:14px 16px; font-size:13px; }
 a { color:#4f8cff; }
</style>
<h1>Agent Change Gate</h1>
<div class="sub" id="phase">starting</div>
<div class="bar"><div id="fill"></div></div>
<div class="sub" id="progress"></div>
<div class="cards" id="cards"></div>
<div id="gate"></div>
<div class="verdict" id="verdict"></div>
<div id="table"></div>
<div id="analysis"></div>
<script>
const TOKEN = "__TOKEN__";
const $ = id => document.getElementById(id);
// 這一頁顯示的東西有一部分是 agent 寫的（工具摘要、失敗原因、sandbox 的分析）。
// 直接塞進 innerHTML 等於讓被評測的 agent 決定這一頁執行什麼。
const esc = v => String(v ?? '').replace(/[&<>"']/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const cls = d => ({same:'same',flaky:'flaky',broken:'broken',fixed:'fixed',incomplete:'incomplete'}[d]||'');
async function tick() {
  const s = await (await fetch('/state')).json();
  $('phase').textContent = s.error ? 'error: ' + s.error : s.phase + (s.arm ? ' · ' + s.arm : '');
  const pct = s.runs_total ? Math.round(100 * s.runs_done / s.runs_total) : 0;
  $('fill').style.width = pct + '%';
  $('progress').textContent = s.runs_total
    ? `${s.runs_done} / ${s.runs_total} scenario runs · one subagent each`
    : '';
  const sm = s.summary || {};
  $('cards').innerHTML = [
    ['scenarios', s.scenarios], ['runs per arm', s.repeats],
    ['flaky', sm.flaky ?? '–'], ['incomplete', sm.incomplete ?? '–'],
    ['baseline tok', (s.baseline_tokens||0).toLocaleString()],
    ['candidate tok', (s.candidate_tokens||0).toLocaleString()],
    ['gate stops', s.approvals],
  ].map(([k,v]) => `<div class="card"><b>${esc(v)}</b><span>${esc(k)}</span></div>`).join('');
  $('gate').innerHTML = s.pending
    ? `<div class="gate"><b>Approval required</b><code>${esc(s.pending)}</code>
       <button class="allow" onclick="decide(true)">Approve</button>
       <button class="deny" onclick="decide(false)">Reject</button></div>`
    : (s.result ? `<div class="gate">${s.result.replace(/(https?:\\/\\/\\S+)/,'<a href="$1" target="_blank">$1</a>')}</div>` : '');
  $('verdict').textContent = s.verdict ? 'Verdict: ' + s.verdict : '';
  $('table').innerHTML = (s.rows||[]).length ? `<table><tr><th>scenario</th><th>baseline</th>
    <th>candidate</th><th>change</th><th>why</th></tr>` + s.rows.map(r =>
    `<tr><td>${esc(r.id)}</td><td>${esc(r.baseline)}</td><td>${esc(r.candidate)}</td>
     <td class="${cls(r.delta)}">${esc(r.delta)}</td><td>${esc((r.why||'').slice(0,70))}</td></tr>`).join('') + '</table>' : '';
  $('analysis').innerHTML = s.analysis ? '<pre>' + esc(s.analysis) + '</pre>' : '';
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
                     phase="evaluating", arm="baseline")

        base = runner.run_arm("baseline", base_spec, scenarios, a.batch_size, a.repeat,
                              on_result=lambda r: state.count_run())
        state.update(baseline_tokens=base.total_tokens, arm="candidate")
        cand = runner.run_arm("candidate", cand_spec, scenarios, a.batch_size, a.repeat,
                              on_result=lambda r: state.count_run())

        comparison = report.Comparison(scenarios, base, cand)
        state.update(candidate_tokens=cand.total_tokens, rows=comparison.rows(),
                     summary=comparison.summary(), verdict=comparison.verdict(),
                     phase="reading the results in the sandbox", arm="")
        read = None if a.no_analysis else analysis.interpret(comparison.rows())
        md = comparison.to_markdown(read)
        Path("change-gate-report.md").write_text(md + "\n")
        state.update(analysis=read, phase="report ready")

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
        landed, output = writeback.land(pending, lambda p: state.ask(p.tool_summary))
        state.update(phase="landed" if landed else "rejected", result=output)
    except Exception as e:                      # 介面要說出哪裡壞了，不能只是停住
        state.update(phase="failed", error=f"{type(e).__name__}: {e}")


def serve(state: GateState, port: int, token: str) -> None:
    """核准端點要帶 token。

    綁 127.0.0.1 只擋掉別台機器；這台機器上跑的任何東西都能 POST /decide，
    而那個端點放行的是不可逆的動作。token 只發給拿得到頁面的人。
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
