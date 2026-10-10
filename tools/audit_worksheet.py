#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""audit_worksheet.py — 検収(audit 突合)の台帳とワークシート(DESIGN.md ADR-7)

2層構成:
- 台帳 data/audit_log/<id>.tsv … 検収状態の真実の源(git 管理)。13列。
- ワークシート build/audit/<id>.worksheet.html … 揮発するビュー(公開しない)。
  ブラウザでのチェックは localStorage の下書きにすぎない。確定は
  「エクスポート → --import で台帳へ反映 → git コミット」で行う。台帳が正。

使い方:
  python3 tools/audit_worksheet.py <id> --init           # 1) 台帳のベースライン作成(初回のみ)
  python3 tools/audit_worksheet.py <id>                  # 2) ワークシート生成
  python3 tools/audit_worksheet.py <id> --import <file>  # 5) エクスポートを検証して台帳へ反映
  python3 tools/audit_worksheet.py <id> --sync           # キー以外の列(block_no 等)だけ変わったとき台帳を合わせる

突合キー(ADR-7 補遺 B): hash(tab, btype, block_label, label, value, unit, source)。
block_no は位置を示すだけの非キー列。値が訂正されるとキーが変わり、その行は
自動的に未検収へ戻る(意図的な安全側の挙動)。

fail-loud(補遺 G): キーの重複、照合表の列数(NF)が8でない行、台帳・エクスポートの
読み込み失敗は、すべて例外で止める。空の block_label は正当なので例外にしない。
"""
import hashlib
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AUDIT_DIR = ROOT / "build" / "audit"
LEDGER_DIR = ROOT / "data" / "audit_log"
PLANS_DIR = ROOT / "data" / "plans"

AUDIT_COLS = ["tab", "block_no", "btype", "block_label", "label", "value", "unit", "source"]
KEY_COLS = ["tab", "btype", "block_label", "label", "value", "unit", "source"]
LEDGER_COLS = ["key"] + AUDIT_COLS + ["checked", "reviewer", "date", "note"]
CHECKED_VALUES = {"", "✓"}
LEDGER_COMMENT = ("# checked=✓ は「原典と一致することを確認した」の意(「見た」ではない)。"
                  "不一致は data/plans/<id>.json を直して再描画する(キーが変わり未検収に戻る)。"
                  "この台帳が検収状態の正で、ブラウザの下書き(localStorage)は揮発する。")


class AuditError(Exception):
    pass


def row_key(r):
    """突合キー。7要素を区切り文字 U+001F で連結した SHA-256 の先頭16桁。"""
    raw = "\x1f".join(r[c] for c in KEY_COLS)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def read_audit(pid):
    """照合表 build/audit/<id>.tsv を読む。列数が8でない行は例外(G2)。"""
    path = AUDIT_DIR / f"{pid}.tsv"
    if not path.exists():
        raise AuditError(f"照合表がありません: {path}(先に render_plan.py {pid} を実行)")
    lines = path.read_text(encoding="utf-8").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if not lines or lines[0].split("\t") != AUDIT_COLS:
        raise AuditError(f"照合表の見出しが想定と違います: {path}\n  想定: {AUDIT_COLS}")
    rows = []
    for n, line in enumerate(lines[1:], start=2):
        f = line.split("\t")
        if len(f) != len(AUDIT_COLS):
            raise AuditError(f"照合表 {path.name} の {n} 行目の列数が {len(f)}(8 であるべき)。"
                             "値にタブや改行が含まれている可能性があります")
        rows.append(dict(zip(AUDIT_COLS, f)))
    seen = {}
    for r in rows:
        k = row_key(r)
        if k in seen:
            raise AuditError(f"突合キーが重複しました(G1): {k}\n  {seen[k]}\n  {r}\n"
                             "同じ表に同じ値が並ぶ場合は、ブロックに title か fold.label を付けて区別する")
        seen[k] = r
        r["key"] = k
    return rows


def parse_ledger_text(text, where):
    """台帳形式(13列・見出し+コメント行)を読む。key を再計算して照合する(F-2)。"""
    lines = [ln for ln in text.replace("\r\n", "\n").split("\n") if ln != ""]
    if not lines or lines[0].split("\t") != LEDGER_COLS:
        raise AuditError(f"{where}: 見出しが台帳の形式と違います\n  想定: {LEDGER_COLS}")
    out = []
    for n, line in enumerate(lines[1:], start=2):
        if line.startswith("#"):
            continue
        f = line.split("\t")
        if len(f) != len(LEDGER_COLS):
            raise AuditError(f"{where}: {n} 行目の列数が {len(f)}(13 であるべき)")
        r = dict(zip(LEDGER_COLS, f))
        if row_key(r) != r["key"]:
            raise AuditError(f"{where}: {n} 行目の key が内容と一致しません(手編集か文字化けの可能性)")
        if r["checked"] not in CHECKED_VALUES:
            raise AuditError(f"{where}: {n} 行目の checked の値が不正です: {r['checked']!r}(空か ✓)")
        if r["checked"] == "✓" and not (r["reviewer"].strip() and r["date"].strip()):
            raise AuditError(f"{where}: {n} 行目は ✓ なのに確認者か日付が空です。"
                             "確認者(人は GitHub のユーザー名)を入れてから印を付けてください")
        out.append(r)
    keys = [r["key"] for r in out]
    if len(keys) != len(set(keys)):
        raise AuditError(f"{where}: key が重複しています")
    return out


def ledger_path(pid):
    return LEDGER_DIR / f"{pid}.tsv"


def read_ledger(pid):
    path = ledger_path(pid)
    if not path.exists():
        raise AuditError(f"台帳がありません: {path}(初回は --init で作成)")
    return parse_ledger_text(path.read_text(encoding="utf-8"), str(path))


def write_ledger(pid, rows):
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    body = ["\t".join(LEDGER_COLS), LEDGER_COMMENT]
    for r in rows:
        body.append("\t".join(r.get(c, "") for c in LEDGER_COLS))
    ledger_path(pid).write_text("\n".join(body) + "\n", encoding="utf-8")


def cmd_init(pid):
    path = ledger_path(pid)
    if path.exists():
        raise AuditError(f"台帳はすでにあります: {path}(上書きしない。--init は初回のみ)")
    rows = read_audit(pid)
    write_ledger(pid, [dict(r, checked="", reviewer="", date="", note="") for r in rows])
    print(f"OK  {path}  ({len(rows)} 行・全行未検収)")


def cmd_import(pid, file):
    current = read_audit(pid)
    exp = parse_ledger_text(Path(file).read_text(encoding="utf-8"), file)
    cur_keys = {r["key"] for r in current}
    exp_keys = {r["key"] for r in exp}
    missing, extra = cur_keys - exp_keys, exp_keys - cur_keys
    if missing or extra:
        raise AuditError(f"エクスポートの行が現在の照合表と一致しません(F-1): 不足 {len(missing)} 行・"
                         f"余分 {len(extra)} 行。ワークシートを作り直してから突合をやり直してください")
    by_key = {r["key"]: r for r in exp}
    write_ledger(pid, [by_key[r["key"]] for r in current])  # 行順は照合表に合わせる
    n = sum(1 for r in exp if r["checked"] == "✓")
    print(f"OK  {ledger_path(pid)}  (検収済み {n} / {len(exp)} 行)")


PAGE_RE = re.compile(r"p\.(\d+)")


def cmd_worksheet(pid):
    current = read_audit(pid)
    ledger = {r["key"]: r for r in read_ledger(pid)}
    stale = sum(1 for k in ledger if k not in {r["key"] for r in current})
    plan = json.loads((PLANS_DIR / f"{pid}.json").read_text(encoding="utf-8"))
    sources = {s["id"]: {"label": s.get("label", s["id"]), "url": s.get("url", "")}
               for s in plan.get("sources", [])}
    rows = []
    for i, r in enumerate(current):
        lg = ledger.get(r["key"], {})
        sid = r["source"].split(" ", 1)[0]
        m = PAGE_RE.search(r["source"])
        rows.append({**{c: r[c] for c in AUDIT_COLS}, "key": r["key"], "order": i,
                     "sid": sid, "page": int(m.group(1)) if m else None,
                     "checked": lg.get("checked", ""), "reviewer": lg.get("reviewer", ""),
                     "date": lg.get("date", ""), "note": lg.get("note", "")})
    data = {"id": pid, "name": plan.get("meta", {}).get("name", pid), "sources": sources,
            "cols": LEDGER_COLS, "comment": LEDGER_COMMENT, "rows": rows}
    out = AUDIT_DIR / f"{pid}.worksheet.html"
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    out.write_text(TEMPLATE.replace("{{TITLE}}", html.escape(data["name"]))
                   .replace("{{DATA}}", payload), encoding="utf-8")
    done = sum(1 for r in rows if r["checked"] == "✓")
    print(f"OK  {out}  ({len(rows)} 行・検収済み {done}・台帳にあって照合表に無い行 {stale})")
    # ブラウザでそのまま開ける形も出す(相対パスでは開けない環境があるため)
    print(f"    ブラウザで開く: {out.resolve().as_uri()}")


TEMPLATE = r"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>検収ワークシート｜{{TITLE}}</title>
<style>
/* 内部ツール。tokens.css は適用しない(ADR-7 補遺 J)。文字は12px以上(ADR-8 に準拠) */
:root{--ink:#232a33;--ink2:#4c5561;--mut:#6b7380;--line:#dfe4ea;--paper:#f4f6f8;--card:#fff;
  --ok:#2f6d5f;--warn:#9a5b00;--accent:#1E4E9C}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font-size:15px;line-height:1.6;
  font-family:"Hiragino Kaku Gothic ProN","Hiragino Sans","Yu Gothic",Meiryo,system-ui,sans-serif}
header{position:sticky;top:0;z-index:2;background:var(--card);border-bottom:1px solid var(--line);padding:10px 16px}
h1{font-size:17px;margin:0 0 6px}
.bar{display:flex;flex-wrap:wrap;gap:8px 14px;align-items:center;font-size:14px}
.bar input[type=text]{font:inherit;padding:6px 8px;border:1px solid var(--line);border-radius:6px;width:9em}
button{font:inherit;font-size:14px;font-weight:700;min-height:40px;padding:0 14px;border-radius:8px;
  border:1px solid var(--accent);background:var(--accent);color:#fff;cursor:pointer}
button.sub{background:var(--card);color:var(--accent)}
.prog{font-weight:700}
.draft{color:var(--warn);font-size:13px}
main{max-width:960px;margin:0 auto;padding:12px 16px 60px}
.help{font-size:13px;color:var(--ink2);background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 12px}
h2{font-size:15px;margin:22px 0 8px;padding-left:10px;border-left:4px solid var(--accent)}
h2 a{font-size:13px;font-weight:400;margin-left:8px}
.row{display:grid;grid-template-columns:44px 1fr;gap:4px 10px;background:var(--card);border:1px solid var(--line);
  border-radius:8px;padding:10px 12px;margin:6px 0}
.row.done{border-color:color-mix(in srgb,var(--ok) 45%,var(--line));background:color-mix(in srgb,var(--ok) 5%,var(--card))}
.row input[type=checkbox]{width:28px;height:28px;margin:2px 0 0;accent-color:var(--ok)}
.v{font-size:18px;font-weight:800}
.v small{font-size:13px;font-weight:700;color:var(--mut);margin-left:3px}
.l{font-weight:700}
.meta{font-size:12px;color:var(--mut)}
.note{grid-column:2;font:inherit;font-size:13px;padding:5px 8px;border:1px solid var(--line);border-radius:6px;width:100%}
.who{font-size:12px;color:var(--ok)}
.hide-done .row.done{display:none}
@media (max-width:560px){header{position:static}}
</style>
</head>
<body>
<header>
  <h1>検収ワークシート：<span id="nm"></span></h1>
  <div class="bar">
    <span class="prog" id="prog"></span>
    <label>確認者 <input type="text" id="rv" placeholder="GitHub のユーザー名"></label>
    <label><input type="checkbox" id="only"> 未確認のみ表示</label>
    <button type="button" id="exp">台帳形式でエクスポート</button>
    <button type="button" class="sub" id="drop">下書きを破棄</button>
    <span class="draft" id="dr"></span>
  </div>
</header>
<main>
  <p class="help">原典の該当ページを開き、値・単位・項目名が一致したら左の欄に印を付けてください。
  印は「一致を確認した」の意味です。一致しない行は印を付けず、メモに内容を書いてください(後で JSON を直します)。
  ブラウザ上の印は下書きで、消えることがあります。確定するには「エクスポート」したファイルを
  <code>python3 tools/audit_worksheet.py ID --import ファイル</code> で台帳に反映し、コミットしてください。</p>
  <div id="list"></div>
</main>
<script>
// 台帳が正。localStorage はブラウザに残る下書き(揮発する)。キーには計画 id を含める(ADR-7 補遺 J)
const D = {{DATA}};
const LS = "hino-audit:" + D.id;
let draft = {};
try { draft = JSON.parse(localStorage.getItem(LS) || "{}"); } catch (e) { draft = {}; }
const st = {};
D.rows.forEach(r => {
  st[r.key] = Object.assign({checked: r.checked, reviewer: r.reviewer, date: r.date, note: r.note}, draft[r.key] || {});
});
document.getElementById("nm").textContent = D.name;
const rv = document.getElementById("rv");
try { rv.value = localStorage.getItem("hino-audit:reviewer") || ""; } catch (e) {}
rv.addEventListener("change", () => { try { localStorage.setItem("hino-audit:reviewer", rv.value.trim()); } catch (e) {} });

function save(k) {
  draft[k] = st[k];
  try { localStorage.setItem(LS, JSON.stringify(draft)); } catch (e) {}
  refresh();
}
function refresh() {
  const n = D.rows.filter(r => st[r.key].checked === "✓").length;
  document.getElementById("prog").textContent = `確認済み ${n} / ${D.rows.length} 行`;
  document.getElementById("dr").textContent = Object.keys(draft).length ? `ブラウザに未反映の下書き ${Object.keys(draft).length} 行` : "";
}
function esc(s) { return String(s).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c])); }

// 並び: 出典ごとにページ順。ページの無い行は「ページ不明」として末尾(ADR-7)
const groups = new Map();
const sorted = D.rows.slice().sort((a, b) =>
  (a.page === null) - (b.page === null) || a.sid.localeCompare(b.sid) ||
  (a.page ?? 0) - (b.page ?? 0) || a.order - b.order);
sorted.forEach(r => {
  const g = r.page === null ? `ページ不明(${r.sid})` : `${r.sid} p.${r.page}`;
  if (!groups.has(g)) groups.set(g, {sid: r.sid, rows: []});
  groups.get(g).rows.push(r);
});
const list = document.getElementById("list");
let h = "";
for (const [g, {sid, rows}] of groups) {
  const s = D.sources[sid] || {label: sid, url: ""};
  const link = s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener">原典の掲載ページ</a>` : "";
  h += `<h2>${esc(g)}　${esc(s.label)}${link}</h2>`;
  for (const r of rows) {
    h += `<div class="row" data-k="${r.key}"><input type="checkbox" aria-label="一致を確認">` +
      `<div><div class="v">${esc(r.value)}<small>${esc(r.unit)}</small></div>` +
      `<div class="l">${esc(r.label)}</div>` +
      `<div class="meta">${esc(r.block_label || "(見出しなし)")}・${esc(r.tab)} / ${esc(r.btype)} #${esc(r.block_no)}・出典 ${esc(r.source)}</div>` +
      `<div class="who"></div></div>` +
      `<input class="note" type="text" placeholder="メモ(不一致の内容など)"></div>`;
  }
}
list.innerHTML = h;
list.querySelectorAll(".row").forEach(el => {
  const k = el.dataset.k, cb = el.querySelector("input[type=checkbox]"), nt = el.querySelector(".note"), who = el.querySelector(".who");
  const paint = () => {
    cb.checked = st[k].checked === "✓";
    el.classList.toggle("done", cb.checked);
    who.textContent = cb.checked ? `確認：${st[k].reviewer || "(未記入)"} ${st[k].date}` : "";
  };
  nt.value = st[k].note;
  paint();
  cb.addEventListener("change", () => {
    if (cb.checked && !rv.value.trim()) {
      // 確認者が空のまま印を付けさせない(台帳は「誰が確認したか」の記録。--import でも弾く)
      cb.checked = false;
      document.getElementById("dr").textContent = "先に上の「確認者」に GitHub のユーザー名を入れてください";
      rv.focus();
      return;
    }
    if (cb.checked) {
      st[k] = Object.assign({}, st[k], {checked: "✓", reviewer: rv.value.trim(), date: new Date().toISOString().slice(0, 10)});
    } else {
      st[k] = Object.assign({}, st[k], {checked: "", reviewer: "", date: ""});
    }
    paint(); save(k);
  });
  nt.addEventListener("change", () => { st[k] = Object.assign({}, st[k], {note: nt.value}); save(k); });
});
document.getElementById("only").addEventListener("change", e => list.classList.toggle("hide-done", e.target.checked));
document.getElementById("drop").addEventListener("click", () => {
  if (!Object.keys(draft).length) return;
  draft = {};
  try { localStorage.removeItem(LS); } catch (e) {}
  location.reload();
});
// エクスポート: 全行(未確認を含む)を台帳形式で出す。--import で検証してから台帳を上書きする
document.getElementById("exp").addEventListener("click", () => {
  const clean = s => String(s).replace(/[\t\r\n]+/g, " ").trim();
  const lines = [D.cols.join("\t"), D.comment];
  D.rows.forEach(r => {
    const s = st[r.key];
    lines.push([r.key, r.tab, r.block_no, r.btype, r.block_label, r.label, r.value, r.unit, r.source,
      s.checked, clean(s.reviewer), clean(s.date), clean(s.note)].join("\t"));
  });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([lines.join("\n") + "\n"], {type: "text/tab-separated-values"}));
  a.download = D.id + ".tsv";
  document.body.appendChild(a); a.click(); a.remove();
});
refresh();
</script>
</body>
</html>
"""


def cmd_sync(pid):
    """照合キーが同じ行について、台帳の照合表由来の列(block_no など)を今の照合表に合わせる。
    検収の印(checked・reviewer・date・note)はそのまま。キーに含まれない列だけが変わったとき
    (例: 文章ブロックを足してブロック番号がずれたとき)に使う。行の過不足があれば止まる。"""
    current = read_audit(pid)
    led = read_ledger(pid)
    cur_keys = {r["key"] for r in current}
    led_keys = {r["key"] for r in led}
    missing, extra = cur_keys - led_keys, led_keys - cur_keys
    if missing or extra:
        raise AuditError(f"台帳の行が今の照合表と一致しません: 台帳に無い {len(missing)} 行・"
                         f"照合表に無い {len(extra)} 行。値が変わった場合は --sync ではなく作り直しの手順で")
    by_key = {r["key"]: r for r in led}
    rows = []
    changed = 0
    for r in current:
        old = by_key[r["key"]]
        new = dict(old)
        for c in AUDIT_COLS:
            new[c] = r[c]
        changed += any(old[c] != new[c] for c in AUDIT_COLS)
        rows.append(new)
    write_ledger(pid, rows)
    n = sum(1 for r in rows if r["checked"] == "✓")
    print(f"OK  {ledger_path(pid)}  (列を更新した行 {changed}・検収済み {n} / {len(rows)} 行はそのまま)")


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    pid = argv[1]
    try:
        if "--init" in argv:
            cmd_init(pid)
        elif "--sync" in argv:
            cmd_sync(pid)
        elif "--import" in argv:
            i = argv.index("--import")
            if i + 1 >= len(argv):
                raise AuditError("--import の後にエクスポートしたファイルを指定してください")
            cmd_import(pid, argv[i + 1])
        else:
            cmd_worksheet(pid)
    except AuditError as e:
        print(f"NG  {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
