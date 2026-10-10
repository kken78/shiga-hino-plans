#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""audit_index.py — 段4(人による確認)の目次ページを作る(DESIGN.md ADR-7 補遺 L)

使い方:
  python3 tools/audit_index.py
    → 台帳 data/audit_log/*.tsv を読み、人が確認する行(「要人確認:」で人の印がまだない行)が残る計画ごとに
      要人確認だけのワークシート build/audit/<id>.focus.worksheet.html を作り直し、
      それらへのリンクと残りの行数を並べた目次 build/audit/index.html を作る(公開しない。git 管理外)。

確認が終わった計画はワークシートで「台帳形式でエクスポート」し、
  python3 tools/audit_worksheet.py <id> --import <書き出したファイル>
で台帳に取り込む。取り込んだあとにこのツールをもう一度実行すると、目次の残りの行数が減る。
"""
import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit_worksheet as aw  # noqa: E402

ROOT = aw.ROOT
PLANS = ROOT / "data" / "plans"


def pending(pid):
    """(人が確認する行の数, そのうち人の印がまだない行の数)"""
    rows = aw.read_ledger(pid)
    focus = [r for r in rows if (r.get("note") or "").startswith(aw.FOCUS)]
    done = [r for r in focus if r["checked"] == "✓" and not (r.get("reviewer") or "").startswith("auto:")]
    return len(focus), len(focus) - len(done)


def plan_name(pid):
    p = PLANS / f"{pid}.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))["meta"]["name"]
    except (OSError, KeyError, ValueError):
        return pid


def main():
    items = []
    for f in sorted((ROOT / "data" / "audit_log").glob("*.tsv")):
        pid = f.stem
        total, left = pending(pid)
        if total == 0:
            continue
        if left:
            aw.cmd_worksheet(pid, focus=True)
        items.append((pid, plan_name(pid), total, left))
    items.sort(key=lambda x: (x[3] == 0, x[0]))
    rows = []
    for pid, name, total, left in items:
        link = (f'<a href="{html.escape(pid)}.focus.worksheet.html">{html.escape(name)}</a>' if left
                else html.escape(name))
        state = f"残り {left} 行" if left else "済み"
        rows.append(f"<tr><td>{link}<div class=id>{html.escape(pid)}</div></td>"
                    f'<td><a href="../../sources_raw/{html.escape(pid)}/">原典のフォルダ</a></td>'
                    f"<td class=n>{total}</td><td class=n>{state}</td></tr>")
    left_all = sum(x[3] for x in items)
    page = f"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>人による確認の目次</title>
<style>
body{{font-family:system-ui,sans-serif;margin:0;padding:16px;color:#222;background:#fafafa;font-size:16px}}
h1{{font-size:20px}} p{{line-height:1.7}} table{{border-collapse:collapse;width:100%;max-width:760px;background:#fff}}
td,th{{border-bottom:1px solid #ddd;padding:8px;text-align:left;vertical-align:top}} .n{{text-align:right;white-space:nowrap}}
.id{{color:#777;font-size:13px}} a{{color:#1a56a8}}
</style></head><body>
<h1>人による確認の目次</h1>
<p>人が確認する行は、残り {left_all} 行です(計画 {sum(1 for x in items if x[3])} 本)。計画名を開き、原典と見比べてチェックを付け、
「台帳形式でエクスポート」で書き出してください。書き出したファイルは
<code>python3 tools/audit_worksheet.py &lt;id&gt; --import &lt;ファイル&gt;</code> で台帳に取り込みます。</p>
<table><thead><tr><th>計画</th><th>原典</th><th class=n>確認する行</th><th class=n>状態</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table></body></html>
"""
    out = aw.AUDIT_DIR / "index.html"
    out.write_text(page, encoding="utf-8")
    print(f"OK  {out}  (残り {left_all} 行・計画 {len(items)} 本)")
    print(f"    ブラウザで開く: {out.resolve().as_uri()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
