"""生成静态网页到 docs/。
v2：日/夜主题手动切换（localStorage记忆，默认夜间）；
今日候选页含股价区间/板块交互筛选；被规则划掉的股票折叠展示并标注全部原因。"""
import html as _html
import json
from pathlib import Path

import pandas as pd

DOCS = Path(__file__).resolve().parent.parent / "docs"

CSS = """
:root{--bg:#101418;--panel:#171d24;--line:#242c36;--txt:#dbe2ea;--dim:#8a96a3;
--faint:#5f6b78;--up:#e8493f;--down:#2eaa6e;--accent:#e8b64c;--warn:#e8b64c}
html[data-theme=light]{--bg:#f4f5f7;--panel:#ffffff;--line:#e2e6eb;--txt:#1d2733;
--dim:#66727f;--faint:#93a0ad;--up:#d9342b;--down:#1e8e5a;--accent:#a06b00;--warn:#a06b00}
*{box-sizing:border-box;margin:0}
body{background:var(--bg);color:var(--txt);font:16px/1.7 "PingFang SC","Microsoft YaHei",
system-ui,sans-serif;padding:0 16px 64px}
.wrap{max-width:900px;margin:0 auto}
header{padding:28px 0 8px;display:flex;justify-content:space-between;align-items:flex-start}
h1{font-size:22px;font-weight:600;letter-spacing:.04em}
h1 .tick{color:var(--accent)}
.meta{color:var(--dim);font-size:13px;margin-top:4px}
#themeBtn{background:none;border:1px solid var(--line);border-radius:999px;
color:var(--dim);width:38px;height:38px;font-size:17px;cursor:pointer}
nav{display:flex;gap:8px;margin:16px 0 20px;flex-wrap:wrap}
nav a{color:var(--dim);text-decoration:none;font-size:14px;padding:6px 14px;
border:1px solid var(--line);border-radius:999px}
nav a.on{color:var(--txt);border-color:var(--accent)}
.filters{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px;align-items:center}
.filters input,.filters select{background:var(--panel);border:1px solid var(--line);
border-radius:8px;color:var(--txt);font-size:14px;padding:8px 10px}
.filters input{width:90px}
.filters button{background:none;border:1px solid var(--line);border-radius:8px;
color:var(--dim);font-size:14px;padding:8px 12px;cursor:pointer}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;
padding:14px 16px;margin-bottom:10px}
.row{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap}
.name{font-size:17px;font-weight:600}
.code{color:var(--dim);font-size:13px;margin-left:8px;font-weight:400}
.ind{color:var(--faint);font-size:12px;margin-left:6px}
.num{font-family:ui-monospace,Menlo,Consolas,monospace;font-variant-numeric:tabular-nums}
.up{color:var(--up)}.down{color:var(--down)}
.kv{display:flex;gap:16px;flex-wrap:wrap;margin-top:6px;font-size:13px;color:var(--dim)}
.kv b{color:var(--txt);font-weight:500}
.chip{font-size:12px;padding:2px 8px;border-radius:4px;border:1px solid var(--line);
color:var(--dim);margin:2px 4px 0 0;display:inline-block}
.chip.warn{color:var(--warn);border-color:var(--warn)}
.chip.why{color:var(--dim)}
.rej .name{text-decoration:line-through;color:var(--dim);font-weight:500}
.rej{opacity:.72}
details{margin-top:20px}
summary{cursor:pointer;color:var(--dim);font-size:14px;padding:8px 0}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{text-align:right;padding:9px 10px;border-bottom:1px solid var(--line)}
th:first-child,td:first-child{text-align:left}
th{color:var(--dim);font-weight:500;font-size:12px}
.tag{font-size:12px;padding:2px 8px;border-radius:4px;border:1px solid}
.tag.qs{color:var(--down);border-color:var(--down)}
.tag.jbm{color:var(--up);border-color:var(--up)}
.tag.zy{color:var(--accent);border-color:var(--accent)}
.note{border-left:3px solid var(--accent);padding:10px 14px;background:var(--panel);
color:var(--dim);font-size:13px;margin:20px 0}
input[type=search]{width:100%;background:var(--panel);border:1px solid var(--line);
border-radius:8px;color:var(--txt);font-size:15px;padding:10px 14px;margin-bottom:14px}
.empty{color:var(--dim);text-align:center;padding:48px 0}
"""

THEME_HEAD = """<script>
(function(){var t=localStorage.getItem('theme');
if(t)document.documentElement.setAttribute('data-theme',t)})();
</script>"""

THEME_BTN_JS = """<script>
var b=document.getElementById('themeBtn'),r=document.documentElement;
function ic(){b.textContent=r.getAttribute('data-theme')==='light'?'☾':'☀'}
b.onclick=function(){var n=r.getAttribute('data-theme')==='light'?'dark':'light';
if(n==='dark'){r.removeAttribute('data-theme');localStorage.setItem('theme','dark')}
else{r.setAttribute('data-theme','light');localStorage.setItem('theme','light')}ic()};
ic();
</script>"""

DISCLAIMER = ("本站为个人研究工具，所有信号由固定规则自动计算，不构成投资建议。"
              "A股为 T+1 交易，信号日买入次日方可卖出，隔夜风险自担。")


def _e(s):
    return _html.escape(str(s), quote=True)


def _page(title, active, body, updated):
    tabs = [("index.html", "今日候选", "screen"),
            ("fundamental.html", "基本面评分", "fund"),
            ("backtest.html", "策略回测", "bt")]
    nav = "".join(f'<a href="{h}" class="{"on" if k == active else ""}">{t}</a>'
                  for h, t, k in tabs)
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>{title}</title>{THEME_HEAD}
<style>{CSS}</style></head>
<body><div class="wrap"><header><div><h1><span class="tick">▮</span> A股每日扫描</h1>
<p class="meta">数据更新：{updated} · 收盘后自动运行</p></div>
<button id="themeBtn" aria-label="切换日间/夜间模式">☀</button></header>
<nav>{nav}</nav>{body}
<p class="note">{DISCLAIMER}</p></div>{THEME_BTN_JS}</body></html>"""


def _card_pass(r):
    cls = "up" if r["pct"] >= 0 else "down"
    warn = "".join(f'<span class="chip warn">{_e(w)}</span>' for w in r["warnings"])
    tno = f'{r["turnover"]}%' if r["turnover"] is not None else "—"
    mc = f'{r["mcap_yi"]:.0f}亿' if r["mcap_yi"] is not None else "—"
    return f"""<div class="card ok" data-price="{r['close']}" data-ind="{_e(r['industry'])}">
<div class="row"><span><span class="name">{_e(r['name'])}</span>
<span class="code num">{_e(r['code'])}</span><span class="ind">{_e(r['industry'])}</span></span>
<span class="num {cls}">{r['close']}　{"+" if r['pct'] >= 0 else ""}{r['pct']}%</span></div>
<div class="kv"><span>成交额 <b class="num">{r['amount_yi']}亿</b></span>
<span>换手 <b class="num">{tno}</b></span><span>市值 <b class="num">{mc}</b></span>
<span>MACD柱 <b class="num up">{r['hist']}</b>（首日转红）</span></div>
{('<div>' + warn + '</div>') if warn else ''}</div>"""


def _card_reject(r):
    chips = "".join(f'<span class="chip why">{_e(x)}</span>' for x in r["reasons"])
    return f"""<div class="card rej" data-price="{r['close']}" data-ind="{_e(r['industry'])}">
<div class="row"><span><span class="name">{_e(r['name'])}</span>
<span class="code num">{_e(r['code'])}</span><span class="ind">{_e(r['industry'])}</span></span>
<span class="num" style="color:var(--dim)">{r['close']}</span></div>
<div>{chips}</div></div>"""


FILTER_JS = """<script>
function applyF(){var lo=parseFloat(document.getElementById('pmin').value)||0,
hi=parseFloat(document.getElementById('pmax').value)||1e9,
ind=document.getElementById('ind').value;
document.querySelectorAll('.card[data-price]').forEach(function(c){
var p=parseFloat(c.dataset.price),i=c.dataset.ind;
c.style.display=(p>=lo&&p<=hi&&(ind===''||i===ind))?'':'none'})}
['pmin','pmax','ind'].forEach(function(id){
document.getElementById(id).addEventListener('input',applyF)});
document.getElementById('rst').onclick=function(){
['pmin','pmax'].forEach(function(id){document.getElementById(id).value=''});
document.getElementById('ind').value='';applyF()};
</script>"""


def render_screen(allrows: pd.DataFrame, updated: str, index_pct: float):
    if allrows.empty:
        body = '<p class="empty">今日无股票触发「MACD 红柱首日」信号——空仓也是仓位。</p>'
    else:
        ok = allrows[allrows["status"] == "pass"]
        rej = allrows[allrows["status"] == "reject"]
        inds = sorted({i for i in allrows["industry"] if i and i != "未知"})
        opts = '<option value="">全部板块</option>' + "".join(
            f'<option>{_e(i)}</option>' for i in inds)
        filters = f"""<div class="filters">
<span class="meta">股价</span><input id="pmin" type="number" placeholder="最低" min="0">
<span class="meta">—</span><input id="pmax" type="number" placeholder="最高" min="0">
<select id="ind">{opts}</select><button id="rst">重置</button></div>"""
        okhtml = "".join(_card_pass(r) for _, r in ok.iterrows()) or \
            '<p class="empty">触发信号的股票全部被规则划掉了，见下方原因。</p>'
        rejhtml = "".join(_card_reject(r) for _, r in rej.iterrows())
        rejblock = f"""<details><summary>被规则划掉 {len(rej)} 只（点击展开，划线并标注原因）
</summary>{rejhtml}</details>""" if len(rej) else ""
        body = (f'{filters}<p class="meta" style="margin-bottom:12px">触发红柱首日 '
                f'{len(allrows)} 只 · 合格 {len(ok)} · 划掉 {len(rej)} · '
                f'沪深300 当日 {index_pct:+.2f}%</p>' + okhtml + rejblock + FILTER_JS)
    (DOCS / "index.html").write_text(_page("今日候选 · A股每日扫描", "screen", body, updated),
                                     encoding="utf-8")


def render_fundamental(scores: pd.DataFrame, updated: str):
    if scores.empty:
        body = '<p class="empty">基本面数据尚未生成，等下一次自动运行。</p>'
    else:
        rows = []
        for _, r in scores.sort_values("score", ascending=False).iterrows():
            tag = {"情绪杀": '<span class="tag qs">情绪杀</span>',
                   "基本面杀": '<span class="tag jbm">基本面杀</span>',
                   "存疑": '<span class="tag zy">存疑</span>'}.get(r.get("label"), "—")
            flags = "、".join(r["flags"]) if isinstance(r["flags"], list) and r["flags"] else "—"
            rows.append(f"<tr><td>{_e(r['name'])}<span class='code num'>{_e(r['code'])}"
                        f"</span></td><td class='num'>{r['score']}</td><td>{tag}</td>"
                        f"<td style='text-align:left;color:var(--dim)'>{_e(flags)}</td></tr>")
        body = f"""<input type="search" id="q" placeholder="输入代码或名称筛选…">
<div class="card" style="overflow-x:auto"><table id="tb">
<thead><tr><th>股票</th><th>基本面分</th><th>下跌归因</th><th>风险信号</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table></div>
<script>document.getElementById('q').addEventListener('input',function(e){{
var k=e.target.value.trim();
document.querySelectorAll('#tb tbody tr').forEach(function(tr){{
tr.style.display=tr.cells[0].textContent.indexOf(k)>=0?'':'none'}})}})</script>"""
    (DOCS / "fundamental.html").write_text(
        _page("基本面评分 · A股每日扫描", "fund", body, updated), encoding="utf-8")


def render_backtest(result, updated, period_desc=""):
    if not result:
        body = ('<p class="empty">还没有回测结果。到仓库的 Actions 页手动运行'
                '「策略回测」即可生成。</p>')
    else:
        def col(s):
            if s["n"] == 0:
                return "<td class='num'>0</td>" + "<td>—</td>" * 5
            return (f"<td class='num'>{s['n']}</td><td class='num'>{s['win_rate']}%</td>"
                    f"<td class='num'>{s['avg_ret']}%</td><td class='num'>{s['total_ret']}%"
                    f"</td><td class='num'>{s['max_dd']}%</td>"
                    f"<td class='num'>{s['avg_hold']}天</td>")
        body = f"""<div class="card" style="overflow-x:auto"><table>
<thead><tr><th>规则</th><th>交易数</th><th>胜率</th><th>平均单笔</th><th>累计收益</th>
<th>最大回撤</th><th>平均持仓</th></tr></thead><tbody>
<tr><td>纯 MACD 红柱</td>{col(result["pure"])}</tr>
<tr><td>红柱 + 全部过滤（与今日候选页同一套规则）</td>{col(result["filtered"])}</tr>
</tbody></table></div>
<p class="meta">回测区间：{period_desc} · 同期沪深300：{result.get("benchmark_total_ret")}% ·
信号日次日开盘买入，-5%止损或MACD转绿次日开盘卖出，双边手续费0.1% ·
市值条件用当前市值近似，基本面按交易当时已披露报告期匹配</p>"""
    (DOCS / "backtest.html").write_text(
        _page("策略回测 · A股每日扫描", "bt", body, updated), encoding="utf-8")


def save_backtest_json(result, period_desc):
    (DOCS / "backtest.json").write_text(
        json.dumps({"result": result, "period": period_desc}, ensure_ascii=False),
        encoding="utf-8")


def load_backtest_json():
    p = DOCS / "backtest.json"
    if p.exists():
        d = json.loads(p.read_text(encoding="utf-8"))
        return d["result"], d["period"]
    return None, ""
