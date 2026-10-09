#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""render_plan.py — data/plans/<id>.json → docs/plans/<id>.html + build/audit/<id>.tsv

設計原則(DESIGN.md / PLAN_SCHEMA.md v1):
- 図表の座標・バー幅・達成率は必ずここで数値から計算する(手置き禁止)
- 出力は自己完結HTML(CSS/JS/画像すべてinline、file://可)
- 計画ごとの個性はテーマ4変数のみ

使い方: python3 tools/render_plan.py <id> [--no-audit]

実装状態(Phase 1で完成させる):
  実装済み: narrative heading bullets cards callout vision aims challenges
            fields actions note custom_html kpi_grid targets bigstat table
            figure timeline programs
            chart(bars/stacked_bars/stacked_100/line/pair_bars/rank_bars)
  未実装:   (なし)
  移植メモ: stacked_100/pair_bars/rank_bars は sources_raw/hub-legacy.html の
            .hkodomo(stack100/pairBars)と .hpshi(render)の座標計算を移植。
            いずれも元は横バーの div 実装なので、SVG(viewBox 720×可変高)へ
            横バーのまま写像した(縦棒の stacked_bars とはレイアウトが異なる)。
            ・stack100: 各行=100%横積み。セグメント幅=v/total、ラベルは
              セグメント幅≥9%のみ表示、文字色は txtOn 相当(WCAG輝度>0.30で暗)
            ・pair_bars: a/b の2横バー。幅=v/max。b の値は淡色(元 pairBars 同じ)
            ・rank_bars: avg 行を末尾へ寄せ元順(降順)維持=元 render と同一。
              幅=max(v/top,1%)。強調 hino→accent2 / 1→accent / 0→accent淡 /
              avg→灰トーン。色はテーマトークン経由(元の生HEXを写像)
"""
import base64
import html
import json
import math
import mimetypes
import sys
from pathlib import Path
from tokens import load_tokens, apply_tokens
# tools/ が sys.path[0] に入る前提(python3 tools/render_plan.py 起動に依存)。DESIGN.md ADR-6

ROOT = Path(__file__).resolve().parent.parent
TPL = ROOT / "templates"
OUT = ROOT / "docs" / "plans"
AUDIT = ROOT / "build" / "audit"
ASSETS = ROOT / "data" / "assets"

DEFAULT_THEME = {"accent": "#1E4E9C", "accent2": "#C33D2E",
                 "danger": "#c25b46", "paper": "#f4f6f8"}
CHART_FONT = 10.5  # チャート内フォント(2段のうち小)
CHART_FONT2 = 11.5  # チャート内フォント(2段のうち大)


def esc(s):
    return html.escape(str(s), quote=True)


class Num(float):
    """JSON の小数リテラルを保持する float(PLAN_SCHEMA §8 小数の表示桁)。

    json.loads(parse_float=Num) で読み込む。演算結果は通常の float に戻るため、
    レンダラーが計算した値(合計・目盛り等)には影響しない。str() はリテラルを返すので、
    audit にも JSON に書いた表記(4.40 等)がそのまま出る。
    """

    def __new__(cls, lit):
        obj = super().__new__(cls, lit)
        obj.lit = lit
        return obj

    def __str__(self):
        return self.lit


def num_abs(n):
    """絶対値。Num はリテラルの桁を保ったまま符号だけ外す。"""
    lit = getattr(n, "lit", None)
    if lit is not None:
        return Num(lit.lstrip("-"))
    return -n if n < 0 else n


def fmt(n):
    """数値の表示整形(桁区切り)。整形はレンダラーの仕事(SCHEMA §7-8)。
    JSON の小数リテラル(Num)は書かれた桁数で表示する(SCHEMA §8)。"""
    lit = getattr(n, "lit", None)
    if lit is not None and "." in lit and "e" not in lit.lower():
        digits = len(lit.split(".", 1)[1])
        return f"{n:,.{digits}f}"
    if isinstance(n, float) and not n.is_integer():
        return f"{n:,}"
    return f"{int(n):,}"


def nice_max(v):
    """軸の上限を切りのよい値へ(kodomo niceMax 相当)。"""
    if v <= 0:
        return 1
    exp = math.floor(math.log10(v))
    for m in (1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        cand = m * 10 ** exp
        if cand >= v:
            return cand
    return 10 ** (exp + 1)


def mix(hex1, hex2, w):
    """hex1 を hex2 側へ w(0-1) 混色。自動配色用。"""
    a = [int(hex1.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
    b = [int(hex2.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
    return "#" + "".join(f"{round(x + (y - x) * w):02x}" for x, y in zip(a, b))


class Renderer:
    def __init__(self, d):
        self.d = d
        self.theme = {**DEFAULT_THEME, **d.get("theme", {})}
        self.audit_rows = []  # (tab, block, label, value, unit, source)
        # 自動配色(系列に c 指定がない場合): テーマから決定的に導出
        a, a2 = self.theme["accent"], self.theme["accent2"]
        self.auto_palette = [a, mix(a, "#ffffff", 0.38), "#527ea1",
                             a2, mix(a2, "#ffffff", 0.38), "#8a8f98"]

    # ---------- 色 ----------
    def color(self, token, fallback_index=0):
        if token is None:
            return self.auto_palette[fallback_index % len(self.auto_palette)]
        if token in self.theme:
            return self.theme[token]
        raise ValueError(f"未知の色トークン(validate漏れ?): {token}")

    def _ink(self, hexc):
        """セグメント上の文字色。元 txtOn(lum>0.30→暗)を移植。"""
        h = hexc.lstrip("#")

        def f(c):
            c = c / 255
            return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
        r, g, bl = (int(h[i:i + 2], 16) for i in (0, 2, 4))
        lum = 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(bl)
        return "#22302c" if lum > 0.30 else "#ffffff"

    def series_colors(self, keys):
        used = 0
        out = []
        for k in keys:
            if k.get("c"):
                out.append(self.color(k["c"]))
            else:
                # 指定色と衝突しない自動色を順に割当
                while used < len(self.auto_palette) and self.auto_palette[used] in out:
                    used += 1
                out.append(self.auto_palette[used % len(self.auto_palette)])
                used += 1
        return out

    # ---------- 監査 ----------
    def audit(self, tab, block_no, btype, label, value, unit, source):
        self.audit_rows.append((tab, f"{block_no}:{btype}", label,
                                value, unit or "", source or ""))

    # ---------- ブロックディスパッチ ----------
    def block(self, b, tab_id, block_no):
        t = b["type"]
        fn = getattr(self, f"b_{t}", None)
        if fn is None:
            raise NotImplementedError(
                f"ブロック type={t} は未実装。PLAN_SCHEMA.md を確認し、"
                f"render_plan.py に b_{t} を実装すること(実装先行は禁止)。")
        inner = fn(b, tab_id, block_no)
        anchor = f' id="{esc(b["anchor"])}"' if b.get("anchor") else ""
        src = b.get("source")
        # 連続する完全一致 source はラン末尾に1回だけ表示(_src_display_indices)
        show_src = bool(src) and block_no in getattr(self, "src_display", set())
        src_html = f'<div class="src">{esc(self.source_label(src))}</div>' if show_src else ""
        return f'<div{anchor}>{inner}</div>{src_html}'

    def source_label(self, ref):
        sid, _, page = str(ref).partition(" ")
        for s in self.d["sources"]:
            if s["id"] == sid:
                return s["label"] + ((" " + page) if page else "")
        return ref

    # ---------- テキスト系 ----------
    def b_narrative(self, b, *_):
        paras = "".join(f"<p>{esc(p)}</p>" for p in str(b["text"]).split("\n") if p.strip())
        return f'<div class="b-narrative">{paras}</div>'

    def b_heading(self, b, *_):
        sub = f'<div class="sub">{esc(b["sub"])}</div>' if b.get("sub") else ""
        return f'<div class="b-heading"><h2>{esc(b["text"])}</h2>{sub}</div>'

    def b_note(self, b, *_):
        return f'<div class="b-note"><span>{esc(b["text"])}</span></div>'

    def b_bullets(self, b, *_):
        t = f'<div class="t">{esc(b["title"])}</div>' if b.get("title") else ""
        lis = "".join(f"<li>{esc(x)}</li>" for x in b["items"])
        return f'<div class="b-bullets">{t}<ul>{lis}</ul></div>'

    def b_cards(self, b, *_, extra_cls=""):
        cards = "".join(
            f'<div class="card"><div class="ct">{esc(it["title"])}</div>'
            f'<div class="cd">{esc(it.get("desc", ""))}</div></div>'
            for it in b["items"])
        return f'<div class="b-cards {extra_cls}">{cards}</div>'

    def b_challenges(self, b, *a):
        return self.b_cards(b, *a, extra_cls="b-challenges")

    def b_callout(self, b, *_):
        t = f'<div class="t">{esc(b["title"])}</div>' if b.get("title") else ""
        chips = ""
        if b.get("chips"):
            chips = '<div class="chips">' + "".join(
                f'<span class="chip">{esc(c)}</span>' for c in b["chips"]) + "</div>"
        return f'<div class="b-callout">{t}<div>{esc(b["text"])}</div>{chips}</div>'

    def b_vision(self, b, *_):
        lbl = esc(b.get("label", "将来像"))
        return (f'<div class="b-vision"><div class="lbl">{lbl}</div>'
                f'<div class="tx">{esc(b["text"])}</div></div>')

    def b_aims(self, b, *_):
        rows = []
        for it in b["items"]:
            no = f'<span class="no">{esc(it["no"])}</span>' if it.get("no") else ""
            d = f'<div class="d">{esc(it["desc"])}</div>' if it.get("desc") else ""
            rows.append(f'<div class="aim">{no}<div><div class="t">{esc(it["title"])}</div>{d}</div></div>')
        return f'<div class="b-aims">{"".join(rows)}</div>'

    def b_fields(self, b, *_):
        rows = "".join(
            f'<div class="field"><div class="ft">{esc(it["term"])}</div>'
            f'<div class="fd">{esc(it["desc"])}</div></div>' for it in b["items"])
        return f'<div class="b-fields">{rows}</div>'

    def b_actions(self, b, *_):
        t = esc(b.get("title", "わたしたちにできること"))
        lis = "".join(f"<li>{esc(x)}</li>" for x in b["items"])
        return f'<div class="b-actions"><div class="t">{t}</div><ul>{lis}</ul></div>'

    def b_custom_html(self, b, *_):
        # validate 済み(scriptなし・reasonあり)。使用は棚卸し対象(DESIGN.md 撤退基準)
        return f'<div class="b-custom" data-reason="{esc(b["reason"])}">{b["html"]}</div>'

    def b_toc(self, b, *_):
        # 目次エントリは render() の事前パス(_collect_toc)が self.toc_entries に用意。
        # 収集0〜1件なら描画抑制(縦長タブでの利用が前提)。
        entries = getattr(self, "toc_entries", [])
        if len(entries) < 2:
            return ""
        label = f'<span class="tl">{esc(b["label"])}</span>' if b.get("label") else ""
        links = "".join(f'<a href="#{esc(e["id"])}">{esc(e["label"])}</a>'
                        for e in entries)
        return f'<nav class="b-toc">{label}{links}</nav>'

    # ---------- データ系 ----------
    def b_kpi_grid(self, b, tab_id, block_no):
        cells = []
        for it in b["items"]:
            self.audit(tab_id, block_no, "kpi", it["label"], it["value"],
                       it["unit"], b.get("source"))
            tr_map = {"up": "▲", "down": "▼", "flat": "―"}
            tr = it.get("trend")
            trh = f'<span class="tr {tr}">{tr_map[tr]}</span>' if tr else ""
            delta = f'<div class="kd">{trh}<span>{esc(it["delta"])}</span></div>' if it.get("delta") else ""
            asof = f'<div class="asof">{esc(it["asof"])}</div>' if it.get("asof") else ""
            cells.append(
                f'<div class="kpi"><div class="kl">{esc(it["label"])}</div>'
                f'<div class="kn num">{fmt(it["value"])}<span class="ku">{esc(it["unit"])}</span></div>'
                f'{delta}{asof}</div>')
        return f'<div class="b-kpis">{"".join(cells)}</div>'

    def b_bigstat(self, b, tab_id, block_no):
        self.audit(tab_id, block_no, "bigstat", b["label"], b["value"],
                   b["unit"], b.get("source"))
        d = f'<div class="d">{esc(b["desc"])}</div>' if b.get("desc") else ""
        return (f'<div class="b-bigstat"><span class="v num">{fmt(b["value"])}</span>'
                f'<span class="u">{esc(b["unit"])}</span>'
                f'<span class="l">{esc(b["label"])}</span>{d}</div>')

    def b_targets(self, b, tab_id, block_no):
        rows = []
        for it in b["items"]:
            base, target = it["base"], it["target"]
            cur = it.get("current")
            now = cur or base
            hib = it.get("higher_is_better", True)
            # 達成率は必ずここで計算(手置き禁止)
            if hib:
                pct = now["v"] / target["v"] * 100 if target["v"] else 0
            else:
                pct = target["v"] / now["v"] * 100 if now["v"] else 0
            pctc = max(0.0, min(100.0, pct))
            for tag, node in (("base", base), ("current", cur), ("target", target)):
                if node:
                    self.audit(tab_id, block_no, f"target.{tag}",
                               it["label"], node["v"], it["unit"], b.get("source"))
            bar = (f'<svg viewBox="0 0 100 6" preserveAspectRatio="none" aria-hidden="true">'
                   f'<rect x="0" y="0" width="100" height="6" rx="3" fill="{mix(self.theme["accent"], "#ffffff", 0.82)}"/>'
                   f'<rect x="0" y="0" width="{pctc:.1f}" height="6" rx="3" fill="{self.theme["accent2"]}"/></svg>')
            cur_label = "現状" if cur else "基準"
            rows.append(
                f'<div class="target"><div class="tl">{esc(it["label"])}</div>'
                f'<div class="tv"><span class="cur num">{fmt(now["v"])}</span>'
                f'<span class="asof">{cur_label}・{esc(now["asof"])}</span>'
                f'<span class="ar">→</span>'
                f'<span class="tar num">{fmt(target["v"])}</span>'
                f'<span class="asof">目標・{esc(target["asof"])}</span>'
                f'<span class="tu">{esc(it["unit"])}</span></div>'
                f'{bar}<div class="pct num">目標比 {pct:.1f}%</div></div>')
        return f'<div class="b-targets">{"".join(rows)}</div>'

    def b_table(self, b, tab_id, block_no):
        head = "".join(f"<th>{esc(h)}</th>" for h in b["head"])
        trs = []
        for row in b["rows"]:
            tds = []
            for ci, cell in enumerate(row):
                cls, v = "", cell
                if isinstance(cell, dict):
                    cls, v = cell.get("cls", ""), cell["v"]
                is_num = isinstance(v, (int, float)) and not isinstance(v, bool)
                if is_num:
                    # audit は生の数値(負値も -n のまま)を記録
                    self.audit(tab_id, block_no, "table",
                               f"{row[0]}/{b['head'][ci]}", v, "", b.get("source"))
                    # 表示のみ和文会計表記に変換: 負値は「▲＋絶対値」+ 赤(neg)
                    if v < 0:
                        v = "▲" + fmt(num_abs(v))
                        if "neg" not in cls.split():
                            cls = (cls + " neg").strip()
                    else:
                        v = fmt(v)
                classes = " ".join(c for c in (("num" if is_num else ""), cls) if c)
                cls_attr = f' class="{esc(classes)}"' if classes else ""
                tds.append(f"<td{cls_attr}>{esc(v)}</td>")
            trs.append(f"<tr>{''.join(tds)}</tr>")
        note = f'<div class="b-note"><span>{esc(b["note"])}</span></div>' if b.get("note") else ""
        table_html = (f'<div class="b-table"><table><thead><tr>{head}</tr></thead>'
                      f'<tbody>{"".join(trs)}</tbody></table></div>')
        fold = b.get("fold")
        if fold:
            # 折りたたみ(details/summary)。summary=label(+要約)常時表示、開くと表。
            label = fold if isinstance(fold, str) else fold.get("label", "")
            summ = "" if isinstance(fold, str) else fold.get("summary", "")
            summ_html = f'<div class="fts">{esc(summ)}</div>' if summ else ""
            return (f'<details class="b-foldtbl"><summary>'
                    f'<div class="ftwrap"><div class="ftl">{esc(label)}</div>{summ_html}</div>'
                    f'<span class="caret" aria-hidden="true"></span></summary>'
                    f'{table_html}{note}</details>')
        return f'{table_html}{note}'

    def b_figure(self, b, tab_id, block_no):
        path = ASSETS / b["asset"]
        if not path.exists():
            raise FileNotFoundError(f"アセットがありません: {path}")
        mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        uri = f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()
        cap = f"<figcaption>{esc(b['caption'])}</figcaption>" if b.get("caption") else ""
        self.audit(tab_id, block_no, "figure", b.get("caption", b["asset"]),
                   b["asset"], "", b.get("source"))
        return (f'<figure class="b-figure"><img src="{uri}" alt="{esc(b["alt"])}">'
                f"{cap}</figure>")

    def b_timeline(self, b, tab_id, block_no):
        rows = []
        for it in b["items"]:
            span = "〜".join(it["span"]) if isinstance(it["span"], list) else it["span"]
            self.audit(tab_id, block_no, "timeline", it["label"], span, "", b.get("source"))
            rows.append(f'<div class="tlrow"><span class="sp num">{esc(span)}</span>'
                        f'<span>{esc(it["label"])}</span></div>')
        return f'<div class="b-timeline">{"".join(rows)}</div>'

    def b_programs(self, b, tab_id, block_no):
        def ev(it):
            no = f'<span class="no">{esc(it["no"])}</span>' if it.get("no") else ""
            dept = f'<div class="ed">{esc(it["dept"])}</div>' if it.get("dept") else ""
            desc = it.get("desc")
            if desc:
                # 案2: 事業内容(desc)は折りたたみ。summary=事業名+担当課(常時表示)、
                # 開くと desc を表示。JS不使用(details/summary のネイティブ挙動)。
                paras = "".join(f"<p>{esc(p)}</p>"
                                for p in str(desc).split("\n") if p.strip())
                return (f'<details class="ev evd"><summary>{no}'
                        f'<div class="etwrap"><div class="et">{esc(it["name"])}</div>{dept}</div>'
                        f'<span class="caret" aria-hidden="true"></span></summary>'
                        f'<div class="edesc">{paras}</div></details>')
            # desc が無い事業は従来のカード表示にフォールバック
            return (f'<div class="ev">{no}'
                    f'<div class="etwrap"><div class="et">{esc(it["name"])}</div>{dept}</div></div>')

        def intro_block(sg):
            intro = sg.get("intro")
            if not intro:
                return ""
            rows = "".join(
                f'<div class="pintro-item"><div class="pih">{esc(e["h"])}</div>'
                f'<div class="pib">{esc(e["b"])}</div></div>' for e in intro)
            return f'<div class="pintro">{rows}</div>'

        groups = []
        n_items = 0
        for g in b["groups"]:
            gid = g.get("_toc_id")  # _collect_toc が注入(toc目次のジャンプ先)
            gid_attr = f' id="{esc(gid)}"' if gid else ""
            head = f'<div class="phead"{gid_attr}>{esc(g.get("no", ""))} {esc(g["name"])}</div>'
            body = ""
            if g.get("subgroups"):
                for sg in g["subgroups"]:
                    body += (f'<div class="psub">{esc(sg.get("no", ""))} {esc(sg["name"])}</div>'
                             f'{intro_block(sg)}'
                             f'<div class="pgrid">{"".join(ev(i) for i in sg["items"])}</div>')
                    n_items += len(sg["items"])
            else:
                body = f'<div class="pgrid">{"".join(ev(i) for i in g["items"])}</div>'
                n_items += len(g["items"])
            groups.append(f'<div class="pgroup">{head}{body}</div>')
        self.audit(tab_id, block_no, "programs", "事業数", n_items, "件", b.get("source"))
        return f'<div class="b-programs">{"".join(groups)}</div>'

    # ---------- chart ----------
    def b_chart(self, b, tab_id, block_no):
        kind = b["kind"]
        fn = getattr(self, f"chart_{kind}", None)
        if fn is None:
            raise NotImplementedError(
                f"chart kind={kind} は未実装(冒頭docstringの実装状態を参照)。")
        svg, legend = fn(b, tab_id, block_no)
        title = f'<div class="cttl">{esc(b["title"])}</div>' if b.get("title") else ""
        unit = f'<div class="cunit">単位：{esc(b["unit"])}</div>' if b.get("unit") else ""
        note = f'<div class="b-note"><span>{esc(b["note"])}</span></div>' if b.get("note") else ""
        return f'<div class="b-chart">{title}{unit}{legend}{svg}</div>{note}'

    def _legend(self, names, colors):
        items = "".join(
            f'<span><span class="sw" style="background:{c}"></span>{esc(k)}</span>'
            for k, c in zip(names, colors))
        return f'<div class="legend">{items}</div>'

    def _grid(self, W, pad_l, pad_r, pad_t, ih, top, steps=4, fmt_fn=fmt):
        out = []
        for g in range(steps + 1):
            gv = top / steps * g
            gy = pad_t + ih - gv / top * ih
            out.append(f'<line x1="{pad_l}" y1="{gy:.1f}" x2="{W - pad_r}" y2="{gy:.1f}" '
                       f'stroke="#e4e8ee" stroke-width="1"/>')
            out.append(f'<text x="{pad_l - 7}" y="{gy + 4:.1f}" text-anchor="end" '
                       f'font-size="{CHART_FONT}" fill="#7d8692">{fmt_fn(round(gv, 2))}</text>')
        return "".join(out)

    def chart_bars(self, b, tab_id, block_no):
        years = b["years"]
        W, H, pl, pr, pt, pb = 720, 300, 48, 14, 18, 46
        iw, ih = W - pl - pr, H - pt - pb
        top = nice_max(max(y["v"] for y in years))
        n = len(years)
        slot = iw / n
        bw = min(58, slot * 0.56)
        color = self.color(b.get("c"), 0)
        parts = [self._grid(W, pl, pr, pt, ih, top)]
        for i, y in enumerate(years):
            self.audit(tab_id, block_no, "chart.bars", y["label"], y["v"],
                       b.get("unit"), b.get("source"))
            cx = pl + slot * i + slot / 2
            h = y["v"] / top * ih
            parts.append(f'<rect x="{cx - bw / 2:.1f}" y="{pt + ih - h:.1f}" '
                         f'width="{bw:.1f}" height="{h:.1f}" rx="3" fill="{color}"/>')
            parts.append(f'<text x="{cx:.1f}" y="{pt + ih - h - 6:.1f}" text-anchor="middle" '
                         f'font-size="{CHART_FONT2}" font-weight="700" fill="#232a33">{fmt(y["v"])}</text>')
            parts.append(self._xlabel(cx, pt + ih, y))
        svg = (f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{esc(b.get("title", ""))}" '
               f'preserveAspectRatio="xMidYMid meet">{"".join(parts)}</svg>')
        return svg, ""

    def _xlabel(self, cx, base_y, y):
        sub = (f'<text x="{cx:.1f}" y="{base_y + 32}" text-anchor="middle" '
               f'font-size="{CHART_FONT}" fill="#7d8692">{esc(y["sub"])}</text>') if y.get("sub") else ""
        return (f'<text x="{cx:.1f}" y="{base_y + 18}" text-anchor="middle" '
                f'font-size="{CHART_FONT2}" font-weight="700" fill="#4c5561">{esc(y["label"])}</text>{sub}')

    def chart_stacked_bars(self, b, tab_id, block_no):
        keys, years = b["keys"], b["years"]
        colors = self.series_colors(keys)
        W, H, pl, pr, pt, pb = 720, 300, 48, 14, 18, 46
        iw, ih = W - pl - pr, H - pt - pb
        totals = [y.get("total", sum(y["series"])) for y in years]
        top = nice_max(max(totals))
        n = len(years)
        slot = iw / n
        bw = min(58, slot * 0.56)
        parts = [self._grid(W, pl, pr, pt, ih, top)]
        for i, y in enumerate(years):
            cx = pl + slot * i + slot / 2
            acc = 0.0
            for si, v in enumerate(y["series"]):
                self.audit(tab_id, block_no, "chart.stacked",
                           f'{y["label"]}/{keys[si]["k"]}', v, b.get("unit"), b.get("source"))
                h = v / top * ih
                y0 = pt + ih - (acc + v) / top * ih
                parts.append(f'<rect x="{cx - bw / 2:.1f}" y="{y0:.1f}" width="{bw:.1f}" '
                             f'height="{h:.1f}" fill="{colors[si]}"/>')
                acc += v
            parts.append(f'<text x="{cx:.1f}" y="{pt + ih - acc / top * ih - 6:.1f}" '
                         f'text-anchor="middle" font-size="{CHART_FONT2}" font-weight="700" '
                         f'fill="#232a33">{fmt(totals[i])}</text>')
            parts.append(self._xlabel(cx, pt + ih, y))
        svg = (f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{esc(b.get("title", ""))}" '
               f'preserveAspectRatio="xMidYMid meet">{"".join(parts)}</svg>')
        return svg, self._legend([k["k"] for k in keys], colors)

    def chart_line(self, b, tab_id, block_no):
        xlabels, lines = b["xlabels"], b["lines"]
        colors = self.series_colors(lines)
        W, H, pl, pr, pt, pb = 720, 300, 44, 14, 18, 34
        iw, ih = W - pl - pr, H - pt - pb
        top = b.get("ymax") or nice_max(max(v for ln in lines for v in ln["vals"]))
        n = len(xlabels)
        step = iw / (n - 1) if n > 1 else iw
        parts = [self._grid(W, pl, pr, pt, ih, top)]
        for li, ln in enumerate(lines):
            pts = []
            for i, v in enumerate(ln["vals"]):
                self.audit(tab_id, block_no, "chart.line",
                           f'{ln["k"]}/{xlabels[i]}', v, b.get("unit"), b.get("source"))
                pts.append(f"{pl + step * i:.1f},{pt + ih - v / top * ih:.1f}")
            dash = ' stroke-dasharray="6 4"' if ln.get("dash") else ""
            parts.append(f'<polyline points="{" ".join(pts)}" fill="none" '
                         f'stroke="{colors[li]}" stroke-width="2.5" '
                         f'stroke-linejoin="round" stroke-linecap="round"{dash}/>')
        show_every = max(1, math.ceil(n / 15))
        for i, xl in enumerate(xlabels):
            if i % show_every:
                continue
            parts.append(f'<text x="{pl + step * i:.1f}" y="{pt + ih + 18}" text-anchor="middle" '
                         f'font-size="{CHART_FONT}" fill="#7d8692">{esc(xl)}</text>')
        svg = (f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{esc(b.get("title", ""))}" '
               f'preserveAspectRatio="xMidYMid meet">{"".join(parts)}</svg>')
        return svg, self._legend([ln["k"] for ln in lines], colors)

    def chart_stacked_100(self, b, tab_id, block_no):
        """100%横積みバー(元 .hkodomo stack100 を移植)。
        各 year を1行の横バーにし、series を割合(v/total)で分割する。
        セグメント幅≥9%のときだけ整数%を中央表示(元 showTx = v>=9 と同じ)。"""
        keys, years = b["keys"], b["years"]
        colors = self.series_colors(keys)
        W, pl, pr, pt, pb = 720, 96, 14, 16, 12
        row_h, bar_h = 34, 22
        n = len(years)
        H = pt + n * row_h + pb
        iw = W - pl - pr
        parts = []
        for i, y in enumerate(years):
            total = y.get("total", sum(y["series"]))
            ry = pt + row_h * i
            by = ry + (row_h - bar_h) / 2
            parts.append(f'<text x="{pl - 8}" y="{by + bar_h / 2 + 4:.1f}" text-anchor="end" '
                         f'font-size="{CHART_FONT2}" font-weight="700" fill="#4c5561">{esc(y["label"])}</text>')
            accf = 0.0
            for si, v in enumerate(y["series"]):
                self.audit(tab_id, block_no, "chart.stacked100",
                           f'{y["label"]}/{keys[si]["k"]}', v, b.get("unit"), b.get("source"))
                frac = (v / total) if total else 0.0
                if frac <= 0:
                    continue
                x0 = pl + accf * iw
                w = frac * iw
                parts.append(f'<rect x="{x0:.1f}" y="{by:.1f}" width="{w:.1f}" '
                             f'height="{bar_h}" fill="{colors[si]}"/>')
                if frac * 100 >= 9:
                    parts.append(f'<text x="{x0 + w / 2:.1f}" y="{by + bar_h / 2 + 4:.1f}" '
                                 f'text-anchor="middle" font-size="{CHART_FONT}" font-weight="700" '
                                 f'fill="{self._ink(colors[si])}">{frac * 100:.0f}</text>')
                accf += frac
            # 角丸の縁取りを上から重ねて pill 表現(元は overflow:hidden の角丸容器)
            parts.append(f'<rect x="{pl}" y="{by:.1f}" width="{iw:.1f}" height="{bar_h}" '
                         f'rx="6" fill="none" stroke="#dde3ea"/>')
        svg = (f'<svg viewBox="0 0 {W} {H:.0f}" role="img" aria-label="{esc(b.get("title", ""))}" '
               f'preserveAspectRatio="xMidYMid meet">{"".join(parts)}</svg>')
        return svg, self._legend([k["k"] for k in keys], colors)

    def chart_pair_bars(self, b, tab_id, block_no):
        """2系列の横バー比較(元 .hkodomo pairBars を移植)。
        各 row に a / b の2本を上下に描く。幅=v/max。bの値ラベルは淡色。
        元は max 既定=100(%前提)。ここでは block.max 指定が無ければ nice_max。"""
        keys, rows = b["keys"], b["rows"]
        colors = self.series_colors(keys)
        has_b = len(keys) >= 2
        W, pl, pr, pt, pb = 720, 150, 56, 16, 14
        iw = W - pl - pr
        sub_h, inner_gap, row_gap = 15, 4, 16
        grp_h = sub_h * 2 + inner_gap if has_b else sub_h
        row_pitch = grp_h + row_gap
        n = len(rows)
        H = pt + n * row_pitch - row_gap + pb
        allv = [r["a"] for r in rows] + [r["b"] for r in rows if has_b and r.get("b") is not None]
        top = b.get("max") or nice_max(max(allv) if allv else 1)
        unit = b.get("unit", "")

        def hbar(x_w, y0, fill, val, vcolor):
            w = max(val / top * iw, 0.0)
            return (f'<rect x="{pl}" y="{y0:.1f}" width="{iw:.1f}" height="{sub_h}" rx="7" fill="#eef0f2"/>'
                    f'<rect x="{pl}" y="{y0:.1f}" width="{w:.1f}" height="{sub_h}" rx="7" fill="{fill}"/>'
                    f'<text x="{pl + w + 6:.1f}" y="{y0 + sub_h - 3:.1f}" font-size="{CHART_FONT}" '
                    f'font-weight="700" fill="{vcolor}">{esc(fmt(val))}{esc(unit)}</text>')

        parts = []
        for i, r in enumerate(rows):
            gy = pt + row_pitch * i
            parts.append(f'<text x="{pl - 8}" y="{gy + grp_h / 2 + 4:.1f}" text-anchor="end" '
                         f'font-size="{CHART_FONT}" font-weight="700" fill="#4c5561">{esc(r["label"])}</text>')
            self.audit(tab_id, block_no, "chart.pair",
                       f'{r["label"]}/{keys[0]["k"]}', r["a"], b.get("unit"), b.get("source"))
            parts.append(hbar(iw, gy, colors[0], r["a"], "#3a414c"))
            if has_b and r.get("b") is not None:
                self.audit(tab_id, block_no, "chart.pair",
                           f'{r["label"]}/{keys[1]["k"]}', r["b"], b.get("unit"), b.get("source"))
                parts.append(hbar(iw, gy + sub_h + inner_gap, colors[1], r["b"], "#7b8a83"))
        svg = (f'<svg viewBox="0 0 {W} {H:.0f}" role="img" aria-label="{esc(b.get("title", ""))}" '
               f'preserveAspectRatio="xMidYMid meet">{"".join(parts)}</svg>')
        return svg, self._legend([k["k"] for k in keys], colors)

    def chart_rank_bars(self, b, tab_id, block_no):
        """降順ランキング横バー(元 .hpshi render を移植)。
        rows=[[名称, 値, 強調], ...]。強調: 0/1/"hino"/"avg"。
        avg 行は末尾へ寄せ、それ以外は入力順(=降順で用意される)を保つ
        (元 render の data.filter(!=avg).concat(avg) と同じ。値ソートはしない)。
        幅=max(v/top, 1%)(元 Math.max(v/max*100,1))。色は元の生HEXを
        テーマトークンへ写像: hino→accent2 / 1→accent / 0→accent淡 / avg→灰。"""
        src = b["rows"]

        def flag(r):
            return r[2] if len(r) > 2 else 0
        rows = [r for r in src if flag(r) != "avg"] + [r for r in src if flag(r) == "avg"]
        n = len(rows)
        # --- 名前欄 pl とビューボックス幅 W を最長ラベルから自動算出((a)+(d)) ---
        # 幅見積りは安全側: 和文=フォントサイズpx、半角英数記号=0.55倍。左余白+バー間隔込み。
        # 短ラベル(フィクスチャ等)は pl が自動的に細くなり、長ラベルでも頭欠けしない。
        # chart_rank_bars 内に閉じており、他チャートには波及しない。
        name_fs = CHART_FONT
        pr, pt, pb = 56, 12, 12
        LEFT_MARGIN, GAP, PL_CAP, MIN_BAR_AREA = 12, 8, 340, 460

        def _label_px(s, fs):
            return sum((fs if ord(c) > 0x2e80 else fs * 0.55) for c in str(s))
        max_label = max((_label_px(r[0], name_fs) for r in rows), default=0.0)
        pl = max(72, math.ceil(max_label + LEFT_MARGIN + GAP))
        if pl > PL_CAP and max_label > 0:
            # 上限超過時は省略(…)せず、フォントを段階縮小して全ラベルを収める
            name_fs = max(7.0, name_fs * (PL_CAP - LEFT_MARGIN - GAP) / max_label)
            pl = PL_CAP
        W = max(720, pl + pr + MIN_BAR_AREA)   # (d) 名前欄が広い分だけ横に拡張しバー長を確保
        bar_h = 15
        row_pitch = 22 if n <= 24 else 17
        iw = W - pl - pr
        H = pt + (row_pitch * (n - 1) if n else 0) + bar_h + pb
        vals = [r[1] for r in rows]
        top = nice_max(max(vals)) if vals else 1
        accent, accent2 = self.color("accent"), self.color("accent2")
        fill0 = mix(accent, "#ffffff", 0.45)   # 元 #9db4d8(accent淡)相当
        avg_tone = "#b0b7c3"                    # 元 avg 別トーン
        unit = b.get("unit", "")
        has_hino = has_avg = False
        parts = []
        for i, r in enumerate(rows):
            name, v, f = r[0], r[1], flag(r)
            self.audit(tab_id, block_no, "chart.rank", name, v, b.get("unit"), b.get("source"))
            y = pt + row_pitch * i
            cy = y + bar_h / 2
            w = max(v / top * iw, iw * 0.01)
            if f == "hino":
                fill, nmc, nmw, vc = accent2, accent2, "700", accent2
                has_hino = True
            elif f == "avg":
                fill, nmc, nmw, vc = avg_tone, "#3a414c", "700", "#3a414c"
                has_avg = True
            elif f == 1:
                fill, nmc, nmw, vc = accent, "#3a414c", "400", "#3a414c"
            else:
                fill, nmc, nmw, vc = fill0, "#3a414c", "400", "#3a414c"
            parts.append(f'<text x="{pl - 8}" y="{cy + 4:.1f}" text-anchor="end" '
                         f'font-size="{name_fs:.1f}" font-weight="{nmw}" fill="{nmc}">{esc(name)}</text>')
            parts.append(f'<rect x="{pl}" y="{y:.1f}" width="{iw:.1f}" height="{bar_h}" rx="7" fill="#f2f0e9"/>')
            parts.append(f'<rect x="{pl}" y="{y:.1f}" width="{w:.1f}" height="{bar_h}" rx="7" fill="{fill}"/>')
            parts.append(f'<text x="{W - 6}" y="{cy + 4:.1f}" text-anchor="end" '
                         f'font-size="{CHART_FONT}" font-weight="700" fill="{vc}">{esc(fmt(v))}{esc(unit)}</text>')
        svg = (f'<svg viewBox="0 0 {W} {H:.0f}" role="img" aria-label="{esc(b.get("title", ""))}" '
               f'preserveAspectRatio="xMidYMid meet">{"".join(parts)}</svg>')
        leg_names, leg_cols = [], []
        if has_hino:
            leg_names.append("日野町")
            leg_cols.append(accent2)
        if has_avg:
            leg_names.append("平均")
            leg_cols.append(avg_tone)
        legend = self._legend(leg_names, leg_cols) if leg_names else ""
        return svg, legend

    # ---------- ページ組み立て ----------
    def _collect_toc(self, tab):
        """タブ内に toc ブロックがある場合のみ、その toc より後ろにある heading と
        programs節 を文書順に収集し id を採番・注入して目次エントリ [{id,label}] を
        返す。toc より前のブロック(導入 heading 等)は収集しない。
        - heading: 明示 anchor 優先、無ければ sec-<tabid>-<n> を anchor に注入
          (ラッパー div が id 化する)
        - programs節: 明示 anchor 優先、無ければ sec-<tabid>-g<n> を _toc_id に
          注入(b_programs が .phead に id 出力)
        toc の無いタブでは何もしない(id 注入もしない)。toc が複数ある場合は
        最初の toc 以降を対象とする(素直な実装)。"""
        blocks = tab.get("blocks", [])
        toc_idx = next((i for i, b in enumerate(blocks)
                        if b.get("type") == "toc"), None)
        if toc_idx is None:
            return []
        tid = tab["id"]
        entries, hn, gn = [], 0, 0
        for b in blocks[toc_idx + 1:]:
            t = b.get("type")
            if t == "heading":
                hn += 1
                hid = b.get("anchor") or f"sec-{tid}-{hn}"
                b["anchor"] = hid
                entries.append({"id": hid, "label": str(b.get("text", ""))})
            elif t == "programs":
                for g in b.get("groups", []):
                    gn += 1
                    gid = g.get("anchor") or f"sec-{tid}-g{gn}"
                    g["_toc_id"] = gid
                    label = f'{g.get("no", "")} {g.get("name", "")}'.strip()
                    entries.append({"id": gid, "label": label})
        return entries

    def _src_display_indices(self, tab):
        """連続する完全一致 source を末尾1回に集約するため、出典行を「表示する」
        ブロックindexの集合を返す(集約は表示のみ。各ブロックの source は JSON と
        audit.tsv に個別保持され追跡性は不変)。
        - heading が来たら連続をリセット(新セクション。またいだ誤集約を防ぐ)
        - source を持たないブロック(narrative/note/bullets等)は透過
          (連続を切らない=source持ちの並びで連続を見る)"""
        display, run_src, last_idx = set(), None, None
        for i, b in enumerate(tab.get("blocks", [])):
            if b.get("type") == "heading":
                run_src, last_idx = None, None
                continue
            s = b.get("source")
            if s is None:
                continue
            if s == run_src:                 # ランの継続:末尾をこのブロックに更新
                display.discard(last_idx)
                display.add(i)
                last_idx = i
            else:                            # 新しいラン開始
                run_src, last_idx = s, i
                display.add(i)
        return display

    def render(self):
        d, m = self.d, self.d["meta"]
        tabs_html, panels_html = [], []
        for ti, tab in enumerate(d["tabs"]):
            sel = "true" if ti == 0 else "false"
            tabs_html.append(
                f'<button class="tab" role="tab" data-tab="{esc(tab["id"])}" '
                f'aria-selected="{sel}"><span class="tn num">{ti + 1:02d}</span>'
                f'{esc(tab["label"])}</button>')
            self.toc_entries = self._collect_toc(tab)  # b_toc が参照する目次エントリ
            self.src_display = self._src_display_indices(tab)  # 出典を表示するindex集合
            blocks = "".join(self.block(b, tab["id"], bi)
                             for bi, b in enumerate(tab["blocks"]))
            active = " active" if ti == 0 else ""
            panels_html.append(f'<section class="panel{active}" role="tabpanel" '
                               f'data-panel="{esc(tab["id"])}">{blocks}</section>')

        period = m["period"]
        metarow = (f'<span>計画期間 <b class="num">{esc(period["start"])}〜{esc(period["end"])}年度</b>'
                   + (f'（{esc(m["period_note"])}）' if m.get("period_note") else "") + "</span>"
                   f'<span>所管 <b>{esc(m["dept"])}</b></span>')
        if m.get("parent_plan"):
            metarow += f'<span>上位計画 <b>{esc(m["parent_plan"])}</b></span>'
        if m.get("posted"):
            metarow += f'<span>公表 <b>{esc(m["posted"])}</b></span>'

        vision = ""
        if m.get("vision"):
            vision = (f'<div class="vision-banner"><span class="lbl">基本理念</span>'
                      f'<span>{esc(m["vision"])}</span></div>')

        sources = "".join(
            f'<li>{esc(s["label"])}'
            + (f'（<a href="{esc(s["url"])}" target="_blank" rel="noopener">リンク</a>）'
               if s.get("url") else "") + "</li>"
            for s in d["sources"])

        contact = f'お問い合わせ：{esc(m["dept"])}'
        if m.get("tel"):
            contact += f'（<span class="num">{esc(m["tel"])}</span>）'

        theme_vars = "".join(f"--{k}:{v};" for k, v in self.theme.items())

        tokens = load_tokens(TPL / "tokens.css")
        shell_css = apply_tokens(
            (TPL / "shell.css").read_text(encoding="utf-8"), tokens, "shell.css")

        page = (TPL / "shell.html").read_text(encoding="utf-8")
        for k, v in {
            "{{TITLE}}": esc(m.get("name_short", m["name"])),
            "{{DESCRIPTION}}": esc(m["lead"]),
            "{{CSS}}": shell_css,
            "{{THEME_VARS}}": theme_vars,
            "{{EYEBROW}}": (f'<div class="eyebrow">{esc(m["eyebrow"])}</div>'
                            if m.get("eyebrow") else ""),
            "{{NAME}}": esc(m["name"]),
            "{{METAROW}}": metarow,
            "{{VISION_BANNER}}": vision,
            "{{LEAD}}": esc(m["lead"]),
            "{{TABS}}": "".join(tabs_html),
            "{{PANELS}}": "".join(panels_html),
            "{{SOURCES}}": sources,
            "{{CONTACT}}": contact,
            "{{URL}}": esc(m["url"]),
            "{{RUNTIME}}": (TPL / "runtime.js").read_text(encoding="utf-8"),
        }.items():
            page = page.replace(k, v)
        return page


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    pid = argv[1]
    src = ROOT / "data" / "plans" / f"{pid}.json"
    d = json.loads(src.read_text(encoding="utf-8"), parse_float=Num)

    r = Renderer(d)
    page = r.render()

    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"{pid}.html"
    out.write_text(page, encoding="utf-8")
    print(f"OK  {out}  ({len(page.encode('utf-8')):,} bytes)")

    if "--no-audit" not in argv:
        AUDIT.mkdir(parents=True, exist_ok=True)
        tsv = AUDIT / f"{pid}.tsv"
        with tsv.open("w", encoding="utf-8") as f:
            f.write("tab\tblock\tlabel\tvalue\tunit\tsource\n")
            for row in r.audit_rows:
                f.write("\t".join(str(x) for x in row) + "\n")
        print(f"OK  {tsv}  ({len(r.audit_rows)} rows) — 原典PDFと突合してください")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
