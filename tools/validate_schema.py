#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""validate_schema.py — data/plans/<id>.json のスキーマ検証。

PLAN_SCHEMA.md v1 の規則を機械的に強制する。規則を緩めたくなったら
先に PLAN_SCHEMA.md を改定すること(CLAUDE.md「やってはいけないこと」)。

使い方: python3 tools/validate_schema.py data/plans/<id>.json [...]
終了コード: 0=OK / 1=エラーあり
"""
import json
import re
import sys

SCHEMA_VERSION = 1

TEXT_BLOCKS = {
    "narrative", "heading", "bullets", "cards", "callout", "vision",
    "aims", "challenges", "fields", "actions", "note", "custom_html",
    "toc",
}
DATA_BLOCKS = {
    "kpi_grid", "targets", "bigstat", "table", "chart",
    "timeline", "programs", "figure",
}
ALL_BLOCKS = TEXT_BLOCKS | DATA_BLOCKS

SUMMARY_ALLOWED = {"narrative", "vision", "aims", "kpi_grid", "targets", "actions", "note"}
SUMMARY_MAX_BLOCKS = 6

CHART_KINDS = {"bars", "stacked_bars", "stacked_100", "line", "pair_bars", "rank_bars"}
THEME_TOKENS = {"accent", "accent2", "danger", "paper"}
TRENDS = {"up", "down", "flat"}

RELATIVE_YEAR = re.compile(r"本年度|今年度|来年度|昨年度|再来年度")
HEX_COLOR = re.compile(r"^#?[0-9a-fA-F]{3,8}$")
ID_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
SOURCE_REF = re.compile(r"^([a-z][a-z0-9_-]*)(\s+p\.[\d０-９]+([-–〜]\d+)?)?$")


class Reporter:
    def __init__(self, path):
        self.path = path
        self.errors = []
        self.warnings = []

    def err(self, where, msg):
        self.errors.append(f"  [ERROR] {where}: {msg}")

    def warn(self, where, msg):
        self.warnings.append(f"  [WARN]  {where}: {msg}")


def check_relative_year(rep, where, value):
    if isinstance(value, str) and RELATIVE_YEAR.search(value):
        rep.err(where, f"相対年度表記は禁止(和暦で明示する): {value[:40]!r}")


def walk_strings(obj, fn, path="$"):
    if isinstance(obj, dict):
        for k, v in obj.items():
            walk_strings(v, fn, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            walk_strings(v, fn, f"{path}[{i}]")
    elif isinstance(obj, str):
        fn(path, obj)


def require(rep, where, obj, keys):
    ok = True
    for k in keys:
        if k not in obj or obj[k] in (None, "", []):
            rep.err(where, f"必須フィールドがありません: {k}")
            ok = False
    return ok


def check_number(rep, where, v, label):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        rep.err(where, f"{label} は数値型で書く(桁区切り文字列は不可): {v!r}")


def check_color_token(rep, where, c):
    if c is None:
        return
    if HEX_COLOR.match(str(c)):
        rep.err(where, f"生HEX色は禁止。テーマトークン名を使う: {c!r}")
    elif c not in THEME_TOKENS:
        rep.err(where, f"未知の色トークン: {c!r} (使用可: {sorted(THEME_TOKENS)})")


def check_source(rep, where, block, source_ids):
    src = block.get("source")
    if not src:
        rep.err(where, f"データ系ブロック type={block.get('type')} に source がありません")
        return
    m = SOURCE_REF.match(str(src))
    if not m:
        rep.err(where, f"source の形式が不正: {src!r} (例: 'honpen p.12')")
        return
    if m.group(1) not in source_ids:
        rep.err(where, f"出典台帳にないid: {m.group(1)!r}")


def check_block(rep, where, b, source_ids):
    t = b.get("type")
    if t not in ALL_BLOCKS:
        rep.err(where, f"未知のブロックtype: {t!r}")
        return
    if t in DATA_BLOCKS:
        check_source(rep, where, b, source_ids)

    if t == "custom_html":
        if "<script" in b.get("html", "").lower():
            rep.err(where, "custom_html に <script は書けない")
        if not b.get("reason"):
            rep.err(where, "custom_html には reason(既存語彙で足りない理由)が必須")

    elif t == "kpi_grid":
        for i, it in enumerate(b.get("items", []) or [{}]):
            w = f"{where}.items[{i}]"
            require(rep, w, it, ["label", "value", "unit"])
            if "value" in it:
                check_number(rep, w, it["value"], "value")
            if it.get("trend") and it["trend"] not in TRENDS:
                rep.err(w, f"trend は {sorted(TRENDS)} のいずれか: {it['trend']!r}")

    elif t == "targets":
        for i, it in enumerate(b.get("items", []) or [{}]):
            w = f"{where}.items[{i}]"
            require(rep, w, it, ["label", "unit", "base", "target"])
            for key in ("base", "target", "current"):
                node = it.get(key)
                if node is not None:
                    if not isinstance(node, dict) or "v" not in node or "asof" not in node:
                        rep.err(w, f"{key} は {{v, asof}} の形で書く")
                    else:
                        check_number(rep, w, node["v"], f"{key}.v")
            for bad in ("rate", "achieved", "bar", "width", "pct"):
                if bad in it:
                    rep.err(w, f"達成率・座標をデータに書かない(レンダラーが計算): {bad}")

    elif t == "bigstat":
        require(rep, where, b, ["value", "unit", "label"])
        if "value" in b:
            check_number(rep, where, b["value"], "value")

    elif t == "table":
        require(rep, where, b, ["head", "rows"])
        ncol = len(b.get("head", []))
        for i, row in enumerate(b.get("rows", [])):
            if len(row) != ncol:
                rep.err(f"{where}.rows[{i}]", f"列数が head({ncol})と不一致: {len(row)}")
        # 任意の折りたたみ fold は、あれば型を検証、無ければ素通り
        fold = b.get("fold")
        if fold is not None:
            if isinstance(fold, str):
                pass
            elif isinstance(fold, dict):
                if not isinstance(fold.get("label"), str):
                    rep.err(f"{where}.fold", "fold は文字列 または {label:str, summary?:str}")
                if fold.get("summary") is not None and not isinstance(fold.get("summary"), str):
                    rep.err(f"{where}.fold", "fold.summary は文字列")
            else:
                rep.err(f"{where}.fold", "fold は文字列 または {label:str, summary?:str}")

    elif t == "chart":
        kind = b.get("kind")
        if kind not in CHART_KINDS:
            rep.err(where, f"未知のchart kind: {kind!r} (使用可: {sorted(CHART_KINDS)})")
        for k in b.get("keys", []) or []:
            check_color_token(rep, where, k.get("c"))
        for ln in b.get("lines", []) or []:
            check_color_token(rep, where, ln.get("c"))
        if kind in ("stacked_bars", "stacked_100"):
            keys_n = len(b.get("keys", []))
            for i, y in enumerate(b.get("years", [])):
                series = y.get("series", [])
                if len(series) != keys_n:
                    rep.err(f"{where}.years[{i}]", f"series の要素数({len(series)})が keys({keys_n})と不一致")
                for v in series:
                    check_number(rep, f"{where}.years[{i}]", v, "series値")
                if y.get("total") is not None:
                    check_number(rep, f"{where}.years[{i}]", y["total"], "total")
                    if abs(sum(series) - y["total"]) > 1e-9:
                        rep.err(f"{where}.years[{i}]",
                                f"series合計({sum(series)})とtotal({y['total']})が不一致")
        if kind == "line":
            nx = len(b.get("xlabels", []))
            for i, ln in enumerate(b.get("lines", [])):
                if len(ln.get("vals", [])) != nx:
                    rep.err(f"{where}.lines[{i}]", f"vals の要素数が xlabels({nx})と不一致")
                for v in ln.get("vals", []):
                    check_number(rep, f"{where}.lines[{i}]", v, "vals値")

    elif t == "figure":
        require(rep, where, b, ["asset", "alt"])
        if b.get("alt") and len(b["alt"]) < 15:
            rep.warn(where, "alt が短すぎます。内容を説明する文章にする")

    elif t == "programs":
        if not b.get("groups"):
            rep.err(where, "groups が空")
        # 任意の説明文フィールド(intro/desc)は、あれば型を検証、無ければ素通り
        def _check_items(items, w):
            for ii, it in enumerate(items or []):
                d = it.get("desc")
                if d is not None and not isinstance(d, str):
                    rep.err(f"{w}.items[{ii}]", "desc は文字列")
        def _check_intro(intro, w):
            if intro is None:
                return
            if not isinstance(intro, list):
                rep.err(f"{w}.intro", "intro は {h,b} のリスト")
                return
            for ki, e in enumerate(intro):
                if not (isinstance(e, dict) and isinstance(e.get("h"), str)
                        and isinstance(e.get("b"), str)):
                    rep.err(f"{w}.intro[{ki}]", "intro 要素は {h:str, b:str}")
        for gi, g in enumerate(b.get("groups", [])):
            gw = f"{where}.groups[{gi}]"
            _check_items(g.get("items"), gw)
            for si, sg in enumerate(g.get("subgroups", []) or []):
                sw = f"{gw}.subgroups[{si}]"
                _check_intro(sg.get("intro"), sw)
                _check_items(sg.get("items"), sw)


def validate(path):
    rep = Reporter(path)
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except Exception as e:
        rep.err("$", f"JSONとして読めません: {e}")
        return rep

    # トップレベル
    if d.get("schema_version") != SCHEMA_VERSION:
        rep.err("$", f"schema_version は {SCHEMA_VERSION}")
    if not ID_RE.match(str(d.get("id", ""))):
        rep.err("$.id", f"idは英小文字始まり[a-z0-9_-]: {d.get('id')!r}")
    require(rep, "$", d, ["meta", "sources", "tabs"])

    meta = d.get("meta", {})
    require(rep, "$.meta", meta, ["name", "period", "lead", "dept", "url"])
    period = meta.get("period", {})
    if not (isinstance(period, dict) and period.get("start") and period.get("end")):
        rep.err("$.meta.period", "period は {start, end}(和暦文字列)")

    # 出典台帳
    source_ids = set()
    for i, s in enumerate(d.get("sources", [])):
        w = f"$.sources[{i}]"
        require(rep, w, s, ["id", "label"])
        sid = s.get("id", "")
        if sid in source_ids:
            rep.err(w, f"出典idが重複: {sid}")
        source_ids.add(sid)
        off = s.get("pdf_page_offset")
        if off is not None and (isinstance(off, bool) or not isinstance(off, int) or off < 0):
            rep.err(w, f"pdf_page_offset は0以上の整数: {off!r}")

    # タブ
    tabs = d.get("tabs", [])
    if not tabs:
        rep.err("$.tabs", "タブがありません")
    else:
        if tabs[0].get("id") != "summary" or tabs[0].get("label") != "まとめ":
            rep.err("$.tabs[0]", 'まとめタブ規約違反: tabs[0]は id:"summary", label:"まとめ"')
        if not (2 <= len(tabs) <= 7):
            rep.warn("$.tabs", f"タブは2〜7個が目安: {len(tabs)}個")

    tab_ids, anchors = set(), set()
    for ti, tab in enumerate(tabs):
        w = f"$.tabs[{ti}]"
        require(rep, w, tab, ["id", "label", "blocks"])
        tid = tab.get("id", "")
        if tid in tab_ids:
            rep.err(w, f"タブidが重複: {tid}")
        tab_ids.add(tid)
        if isinstance(tab.get("label"), str) and len(tab["label"]) > 7:
            rep.warn(w, f"タブラベルは全角7文字以内が目安: {tab['label']!r}")

        blocks = tab.get("blocks", [])
        if ti == 0:
            if len(blocks) > SUMMARY_MAX_BLOCKS:
                rep.warn(w, f"まとめタブはブロック{SUMMARY_MAX_BLOCKS}個以内が目安: {len(blocks)}個")
            for bi, b in enumerate(blocks):
                if b.get("type") not in SUMMARY_ALLOWED:
                    rep.err(f"{w}.blocks[{bi}]",
                            f"まとめタブに置けないtype: {b.get('type')!r} "
                            f"(使用可: {sorted(SUMMARY_ALLOWED)})")
        for bi, b in enumerate(blocks):
            bw = f"{w}.blocks[{bi}]"
            check_block(rep, bw, b, source_ids)
            a = b.get("anchor")
            if a:
                if a in anchors:
                    rep.err(bw, f"anchorが重複: {a}")
                anchors.add(a)

    # 相対年度表記(全文字列走査)
    walk_strings(d, lambda p, s: check_relative_year(rep, p, s))
    return rep


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    bad = 0
    for path in argv[1:]:
        rep = validate(path)
        status = "NG" if rep.errors else "OK"
        print(f"{status}  {path}  (errors={len(rep.errors)}, warnings={len(rep.warnings)})")
        for line in rep.errors + rep.warnings:
            print(line)
        if rep.errors:
            bad += 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
