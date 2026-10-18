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


PROGRAM_KEY_RE = re.compile(r"^e[0-9]{3,}$")
FY_RE = re.compile(r"^R(\d+|元)$")


def program_items(b, where):
    """programs ブロックの事業を文書順に返す: [(場所, item)]"""
    out = []
    for gi, g in enumerate(b.get("groups", []) or []):
        gw = f"{where}.groups[{gi}]"
        for ii, it in enumerate(g.get("items", []) or []):
            out.append((f"{gw}.items[{ii}]", it))
        for si, sg in enumerate(g.get("subgroups", []) or []):
            for ii, it in enumerate(sg.get("items", []) or []):
                out.append((f"{gw}.subgroups[{si}].items[{ii}]", it))
    return out


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
        # asset を省いた figure は原典へのリンク(PLAN_SCHEMA §6-B figure)。source にページが必須
        require(rep, where, b, ["asset", "alt"] if "asset" in b else ["alt"])
        if "asset" not in b:
            m = SOURCE_REF.match(str(b.get("source", "")))
            if m and not m.group(2):
                rep.err(where, f"asset を省いた figure は source にページが必要: {b['source']!r} (例: 'honpen p.13')")
        if b.get("alt") and len(b["alt"]) < 15:
            rep.warn(where, "alt が短すぎます。内容を説明する文章にする")

    elif t == "programs":
        if not b.get("groups"):
            rep.err(where, "groups が空")
        # 任意の説明文フィールド(intro/desc)は、あれば型を検証、無ければ素通り
        def _check_amount(w, a, what):
            if not isinstance(a, dict):
                rep.err(w, f"{what} は {{label, amount, unit}}")
                return
            if not isinstance(a.get("amount"), (int, float)) or isinstance(a.get("amount"), bool):
                rep.err(w, f"{what}.amount は数値: {a.get('amount')!r}")
            if not (isinstance(a.get("unit"), str) and a["unit"]):
                rep.err(w, f"{what}.unit は必須(例「千円」)")
            if a.get("label") is not None and not isinstance(a.get("label"), str):
                rep.err(w, f"{what}.label は文字列")
        def _check_items(items, w):
            for ii, it in enumerate(items or []):
                iw = f"{w}.items[{ii}]"
                d = it.get("desc")
                if d is not None and not isinstance(d, str):
                    rep.err(iw, "desc は文字列")
                # 事業の識別子(PLAN_SCHEMA programs。一意性は validate() で計画全体を見る)
                k = it.get("key")
                if not (isinstance(k, str) and PROGRAM_KEY_RE.match(k)):
                    rep.err(iw, f"key は e と3桁以上の数字(例 e001)。tools/program_keys.py で付ける: {k!r}")
                ys = it.get("years")
                if ys is not None:
                    if not (isinstance(ys, list) and ys and all(isinstance(y, str) and FY_RE.match(y) for y in ys)):
                        rep.err(iw, f"years は年度(R8 など)の配列: {ys!r}")
                    elif len(set(ys)) != len(ys):
                        rep.err(iw, f"years に同じ年度が重複: {ys!r}")
                c = it.get("cost")
                if c is not None:
                    _check_amount(iw, c, "cost")
                    by = c.get("by_year") if isinstance(c, dict) else None
                    if by is not None:
                        if not isinstance(by, list):
                            rep.err(iw, "cost.by_year は {fy, amount} の配列")
                        else:
                            for bi_, e in enumerate(by):
                                if not (isinstance(e, dict) and isinstance(e.get("fy"), str) and FY_RE.match(e["fy"])
                                        and isinstance(e.get("amount"), (int, float)) and not isinstance(e.get("amount"), bool)):
                                    rep.err(f"{iw}.cost.by_year[{bi_}]", "by_year の要素は {fy:\"R8\" など, amount:数値}")
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
            _check_intro(g.get("intro"), gw)
            if g.get("total") is not None:
                _check_amount(gw, g["total"], "total")
            _check_items(g.get("items"), gw)
            for si, sg in enumerate(g.get("subgroups", []) or []):
                sw = f"{gw}.subgroups[{si}]"
                _check_intro(sg.get("intro"), sw)
                _check_items(sg.get("items"), sw)


def _nonneg_int(v, lo):
    return isinstance(v, int) and not isinstance(v, bool) and v >= lo


def check_page_offsets(rep, w, offs):
    """sources[].pdf_page_offsets(PLAN_SCHEMA §4): 印刷ページの範囲ごとの差。from の小さい順・重ならない・to を省けるのは最後だけ"""
    if not (isinstance(offs, list) and offs):
        rep.err(w, "pdf_page_offsets は {from, to?, offset} の配列(1つ以上)")
        return
    prev_to = 0
    for k, e in enumerate(offs):
        ew = f"{w}.pdf_page_offsets[{k}]"
        if not isinstance(e, dict) or set(e) - {"from", "to", "offset"}:
            rep.err(ew, f"要素は {{from, to?, offset}}: {e!r}")
            return
        f_, t_, o_ = e.get("from"), e.get("to"), e.get("offset")
        if not _nonneg_int(f_, 1):
            rep.err(ew, f"from は1以上の整数: {f_!r}")
            return
        if not _nonneg_int(o_, 0):
            rep.err(ew, f"offset は0以上の整数: {o_!r}")
        if t_ is None:
            if k != len(offs) - 1:
                rep.err(ew, "to を省けるのは最後の範囲だけ")
                return
        elif not _nonneg_int(t_, f_):
            rep.err(ew, f"to は from 以上の整数: {t_!r}")
            return
        if prev_to is None or f_ <= prev_to:
            rep.err(ew, f"範囲は from の小さい順に並べ、重ねない(from={f_})")
            return
        prev_to = t_


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
    else:
        era = re.compile(r"^[RHS](\d+|元)$")
        s_, e_ = str(period["start"]), str(period["end"])
        ok = (era.match(s_) and (era.match(e_) or e_ == "定めなし")) or (s_ == e_ == "記載なし")
        if not ok:
            rep.err("$.meta.period", f"period は和暦(R7・H26 など)。end は「定めなし」も可、記載がなければ start・end とも「記載なし」: {s_}〜{e_}")

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
        offs = s.get("pdf_page_offsets")
        if offs is not None:
            check_page_offsets(rep, w, offs)
            if off is not None:
                rep.err(w, "pdf_page_offset と pdf_page_offsets は両方は書けない(差が途中で変わるなら pdf_page_offsets だけにする)")

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
    program_keys = {}   # 事業の key → 最初に出た場所(計画の中で一意。PLAN_SCHEMA programs)
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
            if b.get("type") == "programs":
                for iw, it in program_items(b, bw):
                    k = it.get("key")
                    if isinstance(k, str):
                        if k in program_keys:
                            rep.err(iw, f"事業の key が計画の中で重複: {k}(先に {program_keys[k]})")
                        else:
                            program_keys[k] = iw
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
