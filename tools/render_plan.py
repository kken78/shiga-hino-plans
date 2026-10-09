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
  チャートの描き方(DESIGN.md ADR-8 デザインシステム):
            ・文字はすべて HTML で描き、SVG の中には置かない(スマホで縮小されるため)
            ・横棒(rank_bars/pair_bars/stacked_100)は HTML の grid。狭い画面では
              名前を棒の上の行に置く(shell.css の 560px 以下の規則)
            ・縦棒(bars/stacked_bars)は HTML の列。幅360pxの本文(NARROW_W)に列が
              収まらない場合は、狭い画面用の横棒も出して CSS で出し分ける
            ・折れ線(line)は線と目盛り線だけ SVG(伸縮・線幅固定)、数値とラベルは HTML
            ・stack100 のラベルはセグメント幅≥9%のみ、文字色は txtOn 相当
              (WCAG輝度>0.30で暗)。rank_bars の色は hino→accent2 / 1→accent /
              0→accent淡 / avg→灰(元 .hpshi render の写像を継承)
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
# チャート部品の寸法(DESIGN.md ADR-8)。CSS のトークン(templates/shell.css)と同じ値にする
FS_CHART = 13       # --fs-chart(px)
COL_GAP = 6         # 縦棒の列の間隔(px)。.vb の column-gap
NARROW_W = 292      # 幅360pxの端末でのチャートの中身の幅(px)
WIDE_W = 852        # 最大幅(920px)でのチャートの中身の幅(px)
FOLD_MIN = 21       # 横棒がこの件数以上なら初期表示を畳む(ADR-8 判断の優先順位 3・4)
INLINE_LABEL_W = 110  # 狭い画面でも名前を棒と同じ行に置ける名前の最大幅(px)。和文8字まで
TEXT_COL_MIN = 20  # 表の文章列の判定: 最長の文字列がこの字数を超える(PLAN_SCHEMA §6-B)


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


def text_px(s, fs):
    """文字列の表示幅の見積り(px)。和文=1字、半角=0.62字(太字の数字でも収まる安全側)。"""
    w = 0.0
    for c in str(s):
        o = ord(c)
        w += fs if (o >= 0x2e80 or 0xff01 <= o <= 0xff60) else fs * 0.62
    return w


def tick_steps(top):
    """目盛りの分割数。4〜7分割のうち、1目盛りが切りのよい値(1・2・2.5・5×10^n)に
    なる最小の分割数。該当がなければ4。"""
    for steps in range(4, 8):
        step = top / steps
        if step <= 0:
            break
        m = step / 10 ** math.floor(math.log10(step))
        if any(abs(m - c) < 1e-9 for c in (1, 2, 2.5, 5, 10)):
            return steps
    return 4


def pct(f):
    """割合(0-1)を CSS の % 表記に。"""
    return f"{max(f, 0.0) * 100:.2f}%"


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
        self.audit_rows = []  # (tab, block_no, btype, block_label, label, value, unit, source)
        self.last_heading = ""
        self.block_label = ""
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
        """照合表の1行(DESIGN.md ADR-7 補遺 E: 8列)。
        block_label はブロックの意味ラベル(補遺 D)で、block() が設定する。"""
        self.audit_rows.append((tab, block_no, btype, self.block_label, label,
                                value, unit or "", source or ""))

    @staticmethod
    def _block_label(b, last_heading):
        """block_label の導出(ADR-7 補遺 D): fold.label → title → 直近の先行 heading の text。
        fold は文字列の場合 label として扱う。いずれも無ければ空文字(正当な状態)。"""
        fold = b.get("fold")
        if fold:
            lab = fold if isinstance(fold, str) else fold.get("label", "")
            if lab:
                return str(lab)
        if b.get("title"):
            return str(b["title"])
        return last_heading or ""

    # ---------- ブロックディスパッチ ----------
    def block(self, b, tab_id, block_no):
        t = b["type"]
        fn = getattr(self, f"b_{t}", None)
        if fn is None:
            raise NotImplementedError(
                f"ブロック type={t} は未実装。PLAN_SCHEMA.md を確認し、"
                f"render_plan.py に b_{t} を実装すること(実装先行は禁止)。")
        if t == "heading":
            self.last_heading = str(b.get("text", ""))
        self.block_label = self._block_label(b, self.last_heading)
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
                # 折り返しは「現状(基準)」と「→目標」の2群の間だけで起こす。
                # 単位は目標値の直後に置き、単位だけが次の行に落ちないようにする
                f'<div class="tv"><span class="tg"><span class="cur num">{fmt(now["v"])}</span>'
                f'<span class="asof">{cur_label}・{esc(now["asof"])}</span></span>'
                f'<span class="tg"><span class="ar">→</span>'
                f'<span class="tar num">{fmt(target["v"])}</span>'
                f'<span class="tu">{esc(it["unit"])}</span>'
                f'<span class="asof">目標・{esc(target["asof"])}</span></span></div>'
                f'{bar}<div class="pct num">目標比 {pct:.1f}%</div></div>')
        return f'<div class="b-targets">{"".join(rows)}</div>'

    def b_table(self, b, tab_id, block_no):
        # 文章列(PLAN_SCHEMA §6-B): 2列目以降で数値を含まず、最長の文字列が
        # TEXT_COL_MIN 字を超える列。見出しを含めて左寄せ・折り返しで表示する。
        def raw(cell):
            return cell["v"] if isinstance(cell, dict) else cell
        text_cols = set()
        for ci in range(1, len(b["head"])):
            vals = [raw(r[ci]) for r in b["rows"] if ci < len(r)]
            if vals and all(isinstance(v, str) for v in vals) \
                    and max(len(v) for v in vals) > TEXT_COL_MIN:
                text_cols.add(ci)
        head = "".join(f'<th class="txt">{esc(h)}</th>' if ci in text_cols else f"<th>{esc(h)}</th>"
                       for ci, h in enumerate(b["head"]))
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
                classes = " ".join(c for c in (("num" if is_num else ""),
                                               ("txt" if ci in text_cols else ""), cls) if c)
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

    # --- チャート部品(DESIGN.md ADR-8)。文字はすべて HTML で描き、SVG には置かない ---
    @staticmethod
    def _vw(texts):
        """横棒の値の列幅(em)。最長の値の文字幅から決める(列を行間で揃えるため)。"""
        w = max((text_px(t, 1.0) for t in texts), default=2.0)
        return f"{math.ceil((w + 0.4) * 10) / 10}em"

    @staticmethod
    def _inline(labels):
        """狭い画面でも名前を棒と同じ行に置けるか(名前が短い場合)。行数を減らして縦に長くしない。"""
        return max((text_px(x, FS_CHART) for x in labels), default=0) <= INLINE_LABEL_W

    def _hbar(self, rows, vw, extra_cls=""):
        """横棒部品 .hb。rows=[{label, sub?, segs:[(割合0-1, 色)], value, cls?}]。"""
        n = len(rows)
        fold = n >= FOLD_MIN
        keep = set(range(n))
        if fold:
            # 初期表示に残す行: 先頭3件・強調行(日野町)とその前後2件・平均等・末尾1件
            body_idx = [i for i, r in enumerate(rows) if r.get("cls") != "avg"]
            keep = set(body_idx[:3] + body_idx[-1:])
            for i, r in enumerate(rows):
                if r.get("cls") == "em":
                    keep.update(range(max(0, i - 2), min(n, i + 3)))
                elif r.get("cls") == "avg":
                    keep.add(i)
        out = []
        hidden_run = 0
        for i, r in enumerate(rows):
            if i not in keep:
                hidden_run += 1
            elif hidden_run:
                out.append(f'<div class="hb-gap">…（{hidden_run}件）</div>')
                hidden_run = 0
            sub = f'<span class="hb-sub">{esc(r["sub"])}</span>' if r.get("sub") else ""
            segs = "".join(f'<span style="width:{pct(f)};background:{c}"></span>' for f, c in r["segs"])
            cls = (f' {r["cls"]}' if r.get("cls") else "") + ("" if i in keep else " fx")
            out.append(f'<div class="hb-r{cls}"><span class="hb-l">{esc(r["label"])}{sub}</span>'
                       f'<span class="hb-t">{segs}</span>'
                       f'<span class="hb-v num">{esc(r["value"])}</span></div>')
        if self._inline(f'{r["label"]} {r.get("sub") or ""}'.strip() for r in rows):
            extra_cls = (extra_cls + " inline-sm").strip()
        if hidden_run:
            out.append(f'<div class="hb-gap">…（{hidden_run}件）</div>')
        if not fold:
            cls = f" {extra_cls}" if extra_cls else ""
            return f'<div class="hb{cls}" style="--vw:{vw}">{"".join(out)}</div>'
        # 畳む: 全行を出力し、初期表示では keep 以外を CSS で隠す。1回の操作で全件を表示でき、
        # :has() 非対応のブラウザと印刷では全件が表示される(ADR-8 判断の優先順位 3)
        inner = "inline-sm" if "inline-sm" in extra_cls else ""
        outer = " ".join(c for c in extra_cls.split() if c != "inline-sm")
        cls_i = f" {inner}" if inner else ""
        cls_o = f" {outer}" if outer else ""
        return (f'<div class="hbf{cls_o}"><div class="hb{cls_i}" style="--vw:{vw}">{"".join(out)}</div>'
                f'<details class="more"><summary><span class="mo">すべて表示（全{len(body_idx)}件）</span>'
                f'<span class="mc">折りたたむ</span></summary></details></div>')

    def _vbar(self, cols, extra_cls=""):
        """縦棒部品 .vb。cols=[{label, sub?, height(割合0-1), segs:[(積み上げ内の割合, 色)], value}]。"""
        out = []
        for c in cols:
            segs = "".join(f'<span style="height:{pct(f)};background:{col}"></span>' for f, col in c["segs"])
            sub = f'<span class="vb-sub">{esc(c["sub"])}</span>' if c.get("sub") else ""
            out.append(f'<div class="vb-col"><div class="vb-t">'
                       f'<span class="vb-v num">{esc(c["value"])}</span>'
                       f'<span class="vb-s" style="height:{pct(c["height"])}">{segs}</span></div>'
                       f'<div class="vb-l">{esc(c["label"])}{sub}</div></div>')
        cls = f" {extra_cls}" if extra_cls else ""
        return f'<div class="vb{cls}">{"".join(out)}</div>'

    @staticmethod
    def _cols_fit(cols, width):
        """縦棒の全列が幅 width(px)に文字の重なりなく収まるか(ADR-8 画面幅の規則)。"""
        n = len(cols)
        if n == 0:
            return True
        col_w = (width - COL_GAP * (n - 1)) / n
        need = max(max(text_px(c["label"], FS_CHART), text_px(c.get("sub") or "", FS_CHART),
                       text_px(c["value"], FS_CHART)) for c in cols) + 2
        return col_w >= need

    def _vertical(self, cols, hrows, vw):
        """縦棒を描く。狭い画面の本文幅に収まらない場合は、狭い画面用に横棒も出す。"""
        if not self._cols_fit(cols, WIDE_W):
            return self._hbar(hrows, vw)            # 広い画面でも収まらない: 常に横棒
        if self._cols_fit(cols, NARROW_W):
            return self._vbar(cols)                 # どの幅でも縦棒
        return self._vbar(cols, "wide-only") + self._hbar(hrows, vw, "narrow-only")

    def chart_bars(self, b, tab_id, block_no):
        years = b["years"]
        top = nice_max(max(y["v"] for y in years))
        color = self.color(b.get("c"), 0)
        cols, hrows = [], []
        for y in years:
            self.audit(tab_id, block_no, "chart.bars", y["label"], y["v"],
                       b.get("unit"), b.get("source"))
            cols.append({"label": y["label"], "sub": y.get("sub"), "value": fmt(y["v"]),
                         "height": y["v"] / top, "segs": [(1.0, color)]})
            hrows.append({"label": y["label"], "sub": y.get("sub"), "value": fmt(y["v"]),
                          "segs": [(y["v"] / top, color)]})
        return self._vertical(cols, hrows, self._vw(c["value"] for c in cols)), ""

    def chart_stacked_bars(self, b, tab_id, block_no):
        keys, years = b["keys"], b["years"]
        colors = self.series_colors(keys)
        totals = [y.get("total", sum(y["series"])) for y in years]
        top = nice_max(max(totals))
        cols, hrows = [], []
        for i, y in enumerate(years):
            for si, v in enumerate(y["series"]):
                self.audit(tab_id, block_no, "chart.stacked",
                           f'{y["label"]}/{keys[si]["k"]}', v, b.get("unit"), b.get("source"))
            stack = sum(y["series"])
            cols.append({"label": y["label"], "sub": y.get("sub"), "value": fmt(totals[i]),
                         "height": stack / top,
                         "segs": [((v / stack) if stack else 0.0, colors[si])
                                  for si, v in enumerate(y["series"])]})
            hrows.append({"label": y["label"], "sub": y.get("sub"), "value": fmt(totals[i]),
                          "segs": [(v / top, colors[si]) for si, v in enumerate(y["series"])]})
        body = self._vertical(cols, hrows, self._vw(c["value"] for c in cols))
        return body, self._legend([k["k"] for k in keys], colors)

    def chart_line(self, b, tab_id, block_no):
        xlabels, lines = b["xlabels"], b["lines"]
        colors = self.series_colors(lines)
        top = b.get("ymax") or nice_max(max(v for ln in lines for v in ln["vals"]))
        n = len(xlabels)
        S = 1000  # SVG の座標系(縦横比を固定せず伸縮。線の太さは画面上で固定)
        parts = []
        steps = tick_steps(top)
        ticks = []
        for g in range(steps + 1):
            gv = top / steps * g
            gy = S - gv / top * S
            parts.append(f'<line class="ln-g" x1="0" y1="{gy:.1f}" x2="{S}" y2="{gy:.1f}" '
                         f'vector-effect="non-scaling-stroke"/>')
            ticks.append((g / steps, fmt(round(gv, 2))))
        for li, ln in enumerate(lines):
            pts = []
            for i, v in enumerate(ln["vals"]):
                self.audit(tab_id, block_no, "chart.line",
                           f'{ln["k"]}/{xlabels[i]}', v, b.get("unit"), b.get("source"))
                x = S * i / (n - 1) if n > 1 else S / 2
                pts.append(f"{x:.1f},{S - v / top * S:.1f}")
            dash = ' stroke-dasharray="6 4"' if ln.get("dash") else ""
            parts.append(f'<polyline points="{" ".join(pts)}" fill="none" '
                         f'stroke="{colors[li]}" stroke-width="2.5" '
                         f'stroke-linejoin="round" stroke-linecap="round"{dash} '
                         f'vector-effect="non-scaling-stroke"/>')
        yw_em = max(text_px(t, 1.0) for _, t in ticks) + 0.8
        yw = f"{math.ceil(yw_em * 10) / 10}em"
        ylab = "".join(f'<span class="num" style="bottom:{pct(f)}">{esc(t)}</span>' for f, t in ticks)
        # 横軸ラベルの間引き: 広い画面・狭い画面それぞれで重ならない間隔(ADR-8)
        lab_w = max((text_px(x, FS_CHART) for x in xlabels), default=0) + 10
        yw_px = yw_em * FS_CHART
        k_wide = max(1, math.ceil(n * lab_w / (WIDE_W - yw_px)))
        k_narrow = max(k_wide, math.ceil(n * lab_w / (NARROW_W - yw_px)))
        xlab = []
        for i, xl in enumerate(xlabels):
            if i % k_wide:
                continue
            cls = "" if i % k_narrow == 0 else ' class="nw"'
            left = (i / (n - 1)) if n > 1 else 0.5
            xlab.append(f'<span{cls} style="left:{pct(left)}">{esc(xl)}</span>')
        svg = (f'<svg viewBox="0 0 {S} {S}" preserveAspectRatio="none" aria-hidden="true">'
               f'{"".join(parts)}</svg>')
        body = (f'<div class="ln" style="--ln-yw:{yw}"><div class="ln-plot">'
                f'<div class="ln-y">{ylab}</div>{svg}</div>'
                f'<div class="ln-x">{"".join(xlab)}</div></div>')
        return body, self._legend([ln["k"] for ln in lines], colors)

    def chart_stacked_100(self, b, tab_id, block_no):
        """100%横積みの帯。各 year を1行にし、series を割合(v/total)で分割する。
        セグメント幅≥9%のときだけ整数%を中央表示(元 showTx = v>=9 と同じ)。"""
        keys, years = b["keys"], b["years"]
        colors = self.series_colors(keys)
        rows = []
        for y in years:
            total = y.get("total", sum(y["series"]))
            segs = []
            for si, v in enumerate(y["series"]):
                self.audit(tab_id, block_no, "chart.stacked100",
                           f'{y["label"]}/{keys[si]["k"]}', v, b.get("unit"), b.get("source"))
                frac = (v / total) if total else 0.0
                if frac <= 0:
                    continue
                tx = f"{frac * 100:.0f}" if frac * 100 >= 9 else ""
                segs.append(f'<span class="s100-s" style="width:{pct(frac)};background:{colors[si]};'
                            f'color:{self._ink(colors[si])}">{tx}</span>')
            rows.append(f'<div class="s100-r"><span class="s100-l">{esc(y["label"])}</span>'
                        f'<span class="s100-b">{"".join(segs)}</span></div>')
        cls = " inline-sm" if self._inline(y["label"] for y in years) else ""
        return f'<div class="s100{cls}">{"".join(rows)}</div>', self._legend([k["k"] for k in keys], colors)

    def chart_pair_bars(self, b, tab_id, block_no):
        """2系列の横棒比較。各 row に a / b の2本を上下に描く。幅=v/max。
        max 指定が無ければ nice_max。b の値は淡色。"""
        keys, rows = b["keys"], b["rows"]
        colors = self.series_colors(keys)
        has_b = len(keys) >= 2
        allv = [r["a"] for r in rows] + [r["b"] for r in rows if has_b and r.get("b") is not None]
        top = b.get("max") or nice_max(max(allv) if allv else 1)
        unit = b.get("unit", "")
        vals = [fmt(v) + unit for v in allv]
        groups = []
        for r in rows:
            self.audit(tab_id, block_no, "chart.pair",
                       f'{r["label"]}/{keys[0]["k"]}', r["a"], b.get("unit"), b.get("source"))
            lines = [(r["a"], colors[0], "")]
            if has_b and r.get("b") is not None:
                self.audit(tab_id, block_no, "chart.pair",
                           f'{r["label"]}/{keys[1]["k"]}', r["b"], b.get("unit"), b.get("source"))
                lines.append((r["b"], colors[1], " is-b"))
            bars = "".join(
                f'<span class="hb-t"><span style="width:{pct(max(v / top, 0.0))};background:{c}"></span></span>'
                f'<span class="hb-v num{vc}">{esc(fmt(v))}{esc(unit)}</span>' for v, c, vc in lines)
            groups.append(f'<div class="pb-g"><span class="pb-l">{esc(r["label"])}</span>'
                          f'<span class="pb-bars">{bars}</span></div>')
        cls = " inline-sm" if self._inline(r["label"] for r in rows) else ""
        body = f'<div class="pb{cls}" style="--vw:{self._vw(vals)}">{"".join(groups)}</div>'
        return body, self._legend([k["k"] for k in keys], colors)

    def chart_rank_bars(self, b, tab_id, block_no):
        """降順ランキングの横棒(元 .hpshi render を移植)。
        rows=[[名称, 値, 強調], ...]。強調: 0/1/"hino"/"avg"。
        avg 行は末尾へ寄せ、それ以外は入力順(=降順で用意される)を保つ(値ソートはしない)。
        幅=max(v/top, 1%)。色: hino→accent2 / 1→accent / 0→accent淡 / avg→灰。"""
        src = b["rows"]

        def flag(r):
            return r[2] if len(r) > 2 else 0
        rows = [r for r in src if flag(r) != "avg"] + [r for r in src if flag(r) == "avg"]
        vals = [r[1] for r in rows]
        top = nice_max(max(vals)) if vals else 1
        accent, accent2 = self.color("accent"), self.color("accent2")
        fill0 = mix(accent, "#ffffff", 0.45)   # 元 #9db4d8(accent淡)相当
        avg_tone = "#b0b7c3"                    # 元 avg 別トーン
        unit = b.get("unit", "")
        has_hino = has_avg = False
        hrows = []
        for r in rows:
            name, v, f = r[0], r[1], flag(r)
            self.audit(tab_id, block_no, "chart.rank", name, v, b.get("unit"), b.get("source"))
            if f == "hino":
                fill, cls = accent2, "em"
                has_hino = True
            elif f == "avg":
                fill, cls = avg_tone, "avg"
                has_avg = True
            elif f == 1:
                fill, cls = accent, ""
            else:
                fill, cls = fill0, ""
            hrows.append({"label": name, "value": fmt(v) + unit, "cls": cls,
                          "segs": [(max(v / top, 0.01), fill)]})
        body = self._hbar(hrows, self._vw(r["value"] for r in hrows))
        leg_names, leg_cols = [], []
        if has_hino:
            leg_names.append("日野町")
            leg_cols.append(accent2)
        if has_avg:
            leg_names.append("平均")
            leg_cols.append(avg_tone)
        legend = self._legend(leg_names, leg_cols) if leg_names else ""
        return body, legend

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
            self.last_heading = ""  # block_label の導出はタブごとにやり直す(ADR-7 補遺 D)
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
            f.write("tab\tblock_no\tbtype\tblock_label\tlabel\tvalue\tunit\tsource\n")
            for row in r.audit_rows:
                f.write("\t".join(str(x) for x in row) + "\n")
        print(f"OK  {tsv}  ({len(r.audit_rows)} rows) — 原典PDFと突合してください")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
