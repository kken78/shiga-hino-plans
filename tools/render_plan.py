#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""render_plan.py — data/plans/<id>.json → docs/plans/<id>.html + build/audit/<id>.tsv

設計原則(DESIGN.md / PLAN_SCHEMA.md v1):
- 図表の座標・バー幅・達成率は必ずここで数値から計算する(手置き禁止)
- 出力は自己完結HTML(CSS/JS/画像すべてinline、file://可)
- 色は日野町データポータルの共通の色だけ(--portal-*。DESIGN.md ADR-13・ADR-14)。計画ごとの色は持たない

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
            ・stack100 のラベルはセグメント幅≥9%のみ。文字は白(区別の色の5色とも白と 4.5:1 以上)
            ・系列の色は系列の順に区別の色 --portal-cat1〜5(DESIGN.md ADR-14)。6つめ以降は
              1つめからの色に斜めの縞を付ける。rank_bars は hino→cat2 / avg→cat5 / ほか→cat1
            ・第6次総合計画(sogo6)と実施計画(jisshi)の政策の柱は柱の色 --portal-pillar1〜5(ADR-14)
"""
import base64
import html
import json
from collections import Counter
import math
import mimetypes
import sys
from pathlib import Path
from urllib.parse import urlsplit
from pdf_pages import page_list, pdf_page
from tokens import load_tokens, apply_tokens
import audit_display  # 原典との照合の件数(PLAN_SCHEMA §11・ADR-11)。build.py と共用
import portal  # 日野町データポータルの共通の部品(DESIGN.md ADR-12)。build.py と共用
import csv
import re
# tools/ が sys.path[0] に入る前提(python3 tools/render_plan.py 起動に依存)。DESIGN.md ADR-6

ROOT = Path(__file__).resolve().parent.parent
TPL = ROOT / "templates"
OUT = ROOT / "docs" / "plans"
AUDIT = ROOT / "build" / "audit"
ASSETS = ROOT / "data" / "assets"

# 色(DESIGN.md ADR-13・ADR-14。値は hino-portal の shared/shell.css を写した templates/portal/shell.css の :root)
N_CAT = 5           # 区別の色の数(hino-portal DESIGN.md 3.4)
AVG_CAT = 5         # rank_bars の平均の行の色(Solid Gray)
HINO_CAT = 2        # rank_bars の日野町の行の色
# 第6次総合計画の5つの政策の柱(hino-portal DESIGN.md 3.6 の表と同じ名前。政策1から順)
PILLARS = ("未来を担うひとづくり", "暮らしを支えるしごとづくり", "安心、助け合いのくらしづくり",
           "住みたくなる都市基盤づくり", "みんなではぐくむ地域づくり")
PILLAR_PLANS = {"sogo6", "jisshi"}   # 柱の色を当てる計画
PILLAR_TAB = re.compile(r"^政策の柱([1-5])\s*(.+)$")   # 実施計画の柱ごとのタブの最初の見出し
PORTAL_CSS = TPL / "portal" / "shell.css"
# チャート部品の寸法(DESIGN.md ADR-8)。CSS のトークン(templates/shell.css)と同じ値にする
FS_CHART = 13       # --fs-chart(px)
COL_GAP = 6         # 縦棒の列の間隔(px)。.vb の column-gap
NARROW_W = 292      # 幅360pxの端末でのチャートの中身の幅(px)
WIDE_W = 852        # 最大幅(920px)でのチャートの中身の幅(px)
FOLD_MIN = 21       # 横棒がこの件数以上なら初期表示を畳む(ADR-8 判断の優先順位 3・4)
INLINE_LABEL_W = 110  # 狭い画面でも名前を棒と同じ行に置ける名前の最大幅(px)。和文8字まで
TEXT_COL_MIN = 20  # 表の文章列の判定: 最長の文字列がこの字数を超える(PLAN_SCHEMA §6-B)
FIGURE_LINK_VALUE = "原典へのリンク"  # asset を省いた figure の照合表の値(PLAN_SCHEMA §6-B figure)
SHOGO_ID = "genten-shogo"  # 末尾の節「原典との照合」のページ内リンクの id(PLAN_SCHEMA §11.3)
LEDGER_URL = "https://github.com/kken78/shiga-hino-plans/blob/main/data/audit_log/{}.tsv"
PAGE_URL = "https://kken78.github.io/shiga-hino-plans/plans/{}.html"  # 公開ページの URL(og:url)
NO_TARGET = "この計画には、照合の対象になる数値がありません"
# 照合の方法の説明(PLAN_SCHEMA §11.3 の 4。計画ごとに変えない)
SHOGO_HOW = (
    "このページの数値や名前を、原典と1件ずつ照らしています。",
    "プログラムによる照合：原典の文字データで、値・項目名・値の並び・列見出しの位置がすべて合うものを照合済みとしています。",
    "AI（2回）による照合：同じ種類の AI を、このページを作った経緯を伝えずに2回別々に使い、原典のページの画像で行と列の対応まで"
    "確かめさせ、両方が「一致・確信度 高」としたものだけを照合済みとしています。同じように読み違えるおそれがあるため、"
    "一部は人が抜き取りで確かめています。",
    "人による照合：人が原典と見比べて、一致を確かめたものです。",
    "人の確認で誤りが見つかったものは照合済みから外れるので、件数が減ることがあります。",
)


def esc(s):
    return html.escape(str(s), quote=True)


# フッター(DESIGN.md ADR-12。型は hino-portal の shared/footer.html)
HOW_MADE = ("計画の原典（町のホームページで公開されている PDF など）から、数値と文章を計画ごとの JSON に転記し、"
            "図表はその数値からプログラムで描いています。数値には、原典の資料名とページを添えています。")
DATA_URL = "https://github.com/kken78/shiga-hino-plans/blob/main/data/plans/{}.json"
DATE_IN_LABEL = re.compile(r"(令和|平成)[0-9０-９元]+年[0-9０-９]+月([0-9０-９]+日)?")
SOURCES_INDEX = ROOT / "data" / "sources_index.tsv"
KINDS = {"pdf": "PDF", "xlsx": "Excel", "xls": "Excel", "docx": "Word", "doc": "Word"}


def source_sizes(pid):
    """出典の台帳 data/sources_index.tsv から、この計画の原典の url → 「PDF、8.6MB」。
    台帳に bytes の無い行(掲載ページ(HTML)など)と、拡張子が KINDS に無い行は載せない。"""
    out = {}
    with SOURCES_INDEX.open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            if r["id"] != pid or not r["bytes"] or not r["url"]:
                continue
            kind = KINDS.get(r["file"].rsplit(".", 1)[-1].lower())
            if kind:
                out[r["url"]] = f'{kind}、{int(r["bytes"]) / 1048576:.1f}MB'
    return out


def is_figure_link(b):
    """asset を省いた figure(原典へのリンク。PLAN_SCHEMA §6-B figure)か。"""
    return b.get("type") == "figure" and "asset" not in b


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


def portal_colors():
    """templates/portal/shell.css の :root から --portal-* の色を読む({名前: '#hex'})。"""
    text = PORTAL_CSS.read_text(encoding="utf-8")
    m = re.search(r":root\{([^}]*)\}", text)
    if not m:
        raise ValueError(f"{PORTAL_CSS}: :root が無い")
    return dict(re.findall(r"--(portal-[\w-]+):(#[0-9A-Fa-f]{6})", m.group(1)))


def contrast(hex1, hex2):
    """WCAG 2.x のコントラスト比。"""
    def lum(h):
        def f(c):
            c = c / 255
            return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
        r, g, b = (int(h.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)
    a, b = sorted((lum(hex1), lum(hex2)), reverse=True)
    return (a + 0.05) / (b + 0.05)


def check_portal_colors():
    """区別の色・柱の色のトークンがそろっていること、100%積み上げの帯の中の白い文字が区別の色の5色とも
    4.5:1 以上になることを確かめる(DESIGN.md ADR-14。hino-portal DESIGN.md 3.2)。"""
    cols = portal_colors()
    need = ([f"portal-cat{i}" for i in range(1, N_CAT + 1)]
            + [f"portal-pillar{i}" for i in range(1, 6)] + [f"portal-pillar{i}-ink" for i in range(1, 6)]
            + ["portal-card", "portal-ai", "portal-shu", "portal-bg", "portal-ink"])
    miss = [n for n in need if n not in cols]
    if miss:
        raise ValueError(f"{PORTAL_CSS}: 色のトークンが無い: {miss}")
    for i in range(1, N_CAT + 1):
        r = contrast(cols[f"portal-cat{i}"], cols["portal-card"])
        if r < 4.5:
            raise ValueError(f"--portal-cat{i} と白の比が {r:.2f}(4.5 未満)。帯の中の文字が読めない")


def cat_fill(i):
    """系列 i(0始まり)の塗り(CSS の background の値)。6つめ以降は1つめからの色に斜めの縞を付ける
    (色は5色まで。hino-portal DESIGN.md 3.4「塗りの模様」)。"""
    c = f"var(--portal-cat{i % N_CAT + 1})"
    if i < N_CAT:
        return c
    dark = f"color-mix(in srgb,{c} 55%,var(--portal-ink))"
    return f"repeating-linear-gradient(45deg,{c} 0 5px,{dark} 5px 8px)"


def cat_stroke(i):
    """折れ線の系列 i の線の色と、6つめ以降の点線の印。"""
    return f"var(--portal-cat{i % N_CAT + 1})", i >= N_CAT


class Renderer:
    def __init__(self, d, pid):
        self.d = d
        self.pid = pid  # ファイル名の id(台帳 data/audit_log/<id>.tsv の名前。PLAN_SCHEMA §11)
        if "theme" in d:
            raise ValueError("theme は廃止(DESIGN.md ADR-13)。計画の JSON に色を書かない")
        self.pillar = None  # いま描いているタブの政策の柱の番号(実施計画の柱ごとのタブ。ADR-14)
        self.audit_rows = []  # (tab, block_no, btype, block_label, label, value, unit, source)
        self.last_heading = ""
        self.block_label = ""

    # ---------- 色(DESIGN.md ADR-13・ADR-14) ----------
    @staticmethod
    def series_colors(keys):
        """系列の順に区別の色を当てる。JSON に色は書かない(c は廃止。validate_schema が検出)。"""
        for k in keys:
            if "c" in k:
                raise ValueError("系列の色の指定 c は廃止(DESIGN.md ADR-14)")
        return [cat_fill(i) for i in range(len(keys))]

    def single_fill(self):
        """系列が1つのチャートの色。実施計画の柱ごとのタブでは柱の色、ほかは区別の色の1つめ。"""
        return f"var(--portal-pillar{self.pillar})" if self.pillar else cat_fill(0)

    def pillar_no(self, name):
        """政策の柱の名前なら 1〜5、ほかは None(sogo6・jisshi だけ)。"""
        if self.pid not in PILLAR_PLANS:
            return None
        return PILLARS.index(name) + 1 if name in PILLARS else None

    @staticmethod
    def pillar_vars(n):
        return f"--pl:var(--portal-pillar{n});--pl-ink:var(--portal-pillar{n}-ink)"

    @staticmethod
    def _program_items(b):
        """programs ブロックの事業を文書順に [(節, item)] で返す。"""
        out = []
        for g in b.get("groups", []):
            for it in g.get("items", []) or []:
                out.append((g, it))
            for sg in g.get("subgroups", []) or []:
                for it in sg.get("items", []) or []:
                    out.append((g, it))
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
        # 連続する完全一致 source はラン末尾に1回だけ表示(_src_display_indices)。
        # asset を省いた figure はリンクの文字に文書名とページが出るので、出典表示を出さない
        show_src = (bool(src) and block_no in getattr(self, "src_display", set())
                    and not is_figure_link(b))
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
            pn = self.pillar_no(it["title"])
            attr = f' class="aim is-pillar" style="{self.pillar_vars(pn)}"' if pn else ' class="aim"'
            rows.append(f'<div{attr}>{no}<div><div class="t">{esc(it["title"])}</div>{d}</div></div>')
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
            # 増減は色を付けない矢印と文言(ADR-14。hino-portal DESIGN.md 3.3。PLAN_SCHEMA §6-B kpi_grid)
            tr = it.get("trend")
            if tr == "flat":
                trw = "＝ 同額" if "円" in str(it["unit"]) else "＝ 増減なし"
            else:
                trw = {"up": "▲ 増", "down": "▼ 減"}.get(tr, "")
            trh = f'<span class="tr">{trw}</span>' if tr else ""
            dtx = str(it.get("delta") or "")
            if tr == "down" and dtx.startswith("▲"):
                # 原典の会計の書き方のマイナス。「▲ 増」と読み違えないよう表示だけで外す(JSON と照合表は変えない)
                dtx = dtx[1:]
            delta = f'<div class="kd">{trh}<span>{esc(dtx)}</span></div>' if dtx else ""
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
        return (f'<div class="b-bigstat"><span class="vu"><span class="v num">{fmt(b["value"])}</span>'
                f'<span class="u">{esc(b["unit"])}</span></span>'
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
            # 目標比の長さを藍で示す(ADR-14。色で良し悪しを示さない)
            bar = (f'<svg viewBox="0 0 100 6" preserveAspectRatio="none" aria-hidden="true">'
                   f'<rect x="0" y="0" width="100" height="6" rx="3" style="fill:var(--chart-track)"/>'
                   f'<rect x="0" y="0" width="{pctc:.1f}" height="6" rx="3" style="fill:var(--portal-ai)"/></svg>')
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
                    if cls not in ("", "em"):
                        raise ValueError(f"表のセルの cls は em だけ(neg・pos は廃止。ADR-14): {cls!r}")
                is_num = isinstance(v, (int, float)) and not isinstance(v, bool)
                if is_num:
                    # audit は生の数値(負値も -n のまま)を記録
                    self.audit(tab_id, block_no, "table",
                               f"{raw(row[0])}/{b['head'][ci]}", v, "", b.get("source"))
                    # 表示のみ和文会計表記に変換: 負値は「▲＋絶対値」(色は付けない。ADR-14)
                    if v < 0:
                        v = "▲" + fmt(num_abs(v))
                    else:
                        v = fmt(v)
                classes = " ".join(c for c in (("num" if is_num else ""),
                                               ("txt" if ci in text_cols else ""), cls) if c)
                cls_attr = f' class="{esc(classes)}"' if classes else ""
                tds.append(f"<td{cls_attr}>{esc(v)}</td>")
            trs.append(f"<tr>{''.join(tds)}</tr>")
        note = f'<div class="b-note"><span>{esc(b["note"])}</span></div>' if b.get("note") else ""
        ttl = f'<div class="b-ttl">{esc(b["title"])}</div>' if b.get("title") else ""
        table_html = (f'{ttl}<div class="b-table"><table><thead><tr>{head}</tr></thead>'
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
        if "asset" not in b:
            return self.figure_link(b, tab_id, block_no)
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

    def figure_link(self, b, tab_id, block_no):
        """asset を省いた figure(PLAN_SCHEMA §6-B figure・DESIGN.md ADR-9): 図の位置に、題・説明・
        文書名とページを出し、文書名とページを原典へのリンクにする。#page= は URL が PDF のときだけ付ける。
        #page= が効かない閲覧環境があるため、ページは必ず文字で出す。出典表示は出さない(block)。"""
        src = b["source"]
        s = next(x for x in self.d["sources"] if x["id"] == src.partition(" ")[0])
        ref = esc(self.source_label(src))
        url = s.get("url")
        if url:
            pages = page_list(src)
            if pages and "#" not in url and urlsplit(url).path.lower().endswith(".pdf"):
                pp = pdf_page(pages[0], s.get("pdf_page_offset", 0), s.get("pdf_page_offsets"))
                if pp is not None:
                    url += f"#page={pp}"
            ref = f'<a href="{esc(url)}" target="_blank" rel="noopener">{ref}</a>'
        cap = f"<figcaption>{esc(b['caption'])}</figcaption>" if b.get("caption") else ""
        self.audit(tab_id, block_no, "figure", b.get("caption", ""), FIGURE_LINK_VALUE, "", src)
        return (f'<figure class="b-figure fig-link">{cap}<p>{esc(b["alt"])}</p>'
                f'<p class="fig-ref">この図は原典の該当ページを参照してください：{ref}</p></figure>')

    def b_timeline(self, b, tab_id, block_no):
        rows = []
        for it in b["items"]:
            span = "〜".join(it["span"]) if isinstance(it["span"], list) else it["span"]
            self.audit(tab_id, block_no, "timeline", it["label"], span, "", b.get("source"))
            rows.append(f'<div class="tlrow"><span class="sp num">{esc(span)}</span>'
                        f'<span>{esc(it["label"])}</span></div>')
        return f'<div class="b-timeline">{"".join(rows)}</div>'

    def b_programs(self, b, tab_id, block_no):
        # 実施年度の印は、このブロックの事業に出てくる年度をすべて並べ、行う年度だけを濃く出す
        def fy_no(y):
            return 1 if y == "R元" else int(y[1:])
        all_years = sorted({y for _, it in self._program_items(b) for y in it.get("years", [])}, key=fy_no)

        def money(amount, unit):
            # 数値と単位はひとまとまり(ADR-8 の表「数値と単位が行の途中で切れる」)
            return f'<span class="money"><b class="num">{fmt(amount)}</b>{esc(unit)}</span>'

        def meta(it):
            parts = []
            ys = it.get("years")
            if ys:
                chips = "".join(f'<span class="fy{"" if y in ys else " off"}">{esc(y)}</span>' for y in all_years)
                parts.append(f'<span class="efy" aria-label="実施年度 {esc("・".join(ys))}">{chips}</span>')
            c = it.get("cost")
            if c:
                parts.append(f'<span class="ecost"><span class="ecl">{esc(c.get("label") or "事業費")}</span>'
                             f'{money(c["amount"], c["unit"])}</span>')
            return f'<div class="emeta">{"".join(parts)}</div>' if parts else ""

        def by_year(it):
            c = it.get("cost") or {}
            rows = c.get("by_year") or []
            if not rows:
                return ""
            trs = "".join(f'<tr><th>{esc(e["fy"])}</th><td>{money(e["amount"], c["unit"])}</td></tr>' for e in rows)
            return f'<table class="eby"><tbody>{trs}</tbody></table>'

        def ev(it):
            no = f'<span class="no">{esc(it["no"])}</span>' if it.get("no") else ""
            dept = f'<div class="ed">{esc(it["dept"])}</div>' if it.get("dept") else ""
            desc = it.get("desc")
            if desc or by_year(it):
                # 案2: 事業内容(desc)は折りたたみ。summary=事業名+担当課(常時表示)、
                # 開くと desc を表示。JS不使用(details/summary のネイティブ挙動)。
                paras = "".join(f"<p>{esc(p)}</p>"
                                for p in str(desc or "").split("\n") if p.strip())
                return (f'<details class="ev evd"><summary>{no}'
                        f'<div class="etwrap"><div class="et">{esc(it["name"])}</div>{dept}{meta(it)}</div>'
                        f'<span class="caret" aria-hidden="true"></span></summary>'
                        f'<div class="edesc">{paras}{by_year(it)}</div></details>')
            # desc が無い事業は従来のカード表示にフォールバック
            return (f'<div class="ev">{no}'
                    f'<div class="etwrap"><div class="et">{esc(it["name"])}</div>{dept}{meta(it)}</div></div>')

        # 同じブロックに同じ名前の事業があれば、照合表では出現順で区別する(突合キーの重複を避ける。PLAN_SCHEMA programs)
        name_count = Counter(it["name"] for _, it in self._program_items(b))
        name_seen = Counter()

        def audit_item(it):
            # 照合表: 事業費・実施年度・年度ごとの金額を1行ずつ(PLAN_SCHEMA programs「実施年度と事業費」)
            name, src = it["name"], b.get("source")
            if name_count[name] > 1:
                name_seen[name] += 1
                name = f"{name}(同じ名前の{name_seen[name]}件目)"
            c = it.get("cost")
            if c:
                self.audit(tab_id, block_no, "programs.cost", f'{name}/{c.get("label") or "事業費"}',
                           c["amount"], c["unit"], src)
                for e in c.get("by_year") or []:
                    self.audit(tab_id, block_no, "programs.cost", f'{name}/{e["fy"]}', e["amount"], c["unit"], src)
            if it.get("years"):
                self.audit(tab_id, block_no, "programs.years", f"{name}/実施年度", "・".join(it["years"]), "", src)

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
            pn = self.pillar_no(g["name"])
            pl_attr = f' style="{self.pillar_vars(pn)}"' if pn else ""
            no_html = f'<span class="pno">{esc(g.get("no", ""))}</span>' if pn and g.get("no") else esc(g.get("no", ""))
            head = f'<div class="phead"{gid_attr}{pl_attr}>{no_html} {esc(g["name"])}</div>'
            # 節の計は原典の値をそのまま出す。事業の金額を足し上げない(PLAN_SCHEMA programs)
            tot = g.get("total")
            total = (f'<div class="ptotal"><span class="ecl">{esc(tot.get("label") or "計")}</span>'
                     f'{money(tot["amount"], tot["unit"])}</div>') if tot else ""
            body = ""
            if g.get("subgroups"):
                for sg in g["subgroups"]:
                    # 見出し自体が一番下の項目のとき(同じ no・name の項目1つだけ)は、見出しを出さず
                    # 項目のカードだけを出す(PLAN_SCHEMA programs「項目にするものの定義」)
                    its = sg["items"]
                    solo = (len(its) == 1 and its[0].get("no", "") == sg.get("no", "")
                            and its[0]["name"] == sg["name"])
                    psub = "" if solo else f'<div class="psub">{esc(sg.get("no", ""))} {esc(sg["name"])}</div>'
                    body += (f'{psub}'
                             f'{intro_block(sg)}'
                             f'<div class="pgrid">{"".join(ev(i) for i in sg["items"])}</div>')
                    n_items += len(sg["items"])
                    for i in sg["items"]:
                        audit_item(i)
            else:
                body = f'<div class="pgrid">{"".join(ev(i) for i in g["items"])}</div>'
                n_items += len(g["items"])
                for i in g["items"]:
                    audit_item(i)
            if tot:
                gl = f'{g.get("no", "")} {g["name"]}'.strip()
                self.audit(tab_id, block_no, "programs.total", f'{gl}/{tot.get("label") or "計"}',
                           tot["amount"], tot["unit"], b.get("source"))
            groups.append(f'<div class="pgroup">{head}{total}{intro_block(g)}{body}</div>')
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
            f'<span><span class="sw" style="background:{c}" aria-hidden="true"></span>{esc(k)}</span>'
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
        if "c" in b:
            raise ValueError("系列の色の指定 c は廃止(DESIGN.md ADR-14)")
        color = self.single_fill()
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
            stroke, extra_dash = cat_stroke(li)
            dash = ' stroke-dasharray="6 4"' if (ln.get("dash") or extra_dash) else ""
            parts.append(f'<polyline points="{" ".join(pts)}" fill="none" '
                         f'style="stroke:{stroke}" stroke-width="2.5" '
                         f'stroke-linejoin="round" stroke-linecap="round"{dash} '
                         f'vector-effect="non-scaling-stroke"/>')
        yw_em = max(text_px(t, 1.0) for _, t in ticks) + 0.8
        yw = f"{math.ceil(yw_em * 10) / 10}em"
        ylab = "".join(f'<span class="num" style="bottom:{pct(f)}">{esc(t)}</span>' for f, t in ticks)
        # 横軸ラベルの間引き: 広い画面・狭い画面それぞれで重ならない間隔(ADR-8)
        lab_w = max((text_px(x, FS_CHART) for x in xlabels), default=0) + 10
        yw_px = (yw_em + (text_px(xlabels[-1], 1.0) / 2 if n else 0)) * FS_CHART   # 左の目盛りと右の余白
        k_wide = max(1, math.ceil(n * lab_w / (WIDE_W - yw_px)))
        k_narrow = max(k_wide, math.ceil(n * lab_w / (NARROW_W - yw_px)))
        xlab = []
        # 間引きは右端(最新の年)から数える。最新の年のラベルを必ず残すため(ADR-8 判断の優先順位4)
        for i, xl in enumerate(xlabels):
            r = n - 1 - i
            if r % k_wide:
                continue
            cls = "" if r % k_narrow == 0 else ' class="nw"'
            left = (i / (n - 1)) if n > 1 else 0.5
            xlab.append(f'<span{cls} style="left:{pct(left)}">{esc(xl)}</span>')
        svg = (f'<svg viewBox="0 0 {S} {S}" preserveAspectRatio="none" aria-hidden="true">'
               f'{"".join(parts)}</svg>')
        # 右端のラベル(最新の年)は点の真下に中央そろえで置くので、その半分の幅だけ右に余白をとる
        xr = f"{math.ceil(text_px(xlabels[-1], 1.0) / 2 * 10) / 10}em" if n else "0em"
        body = (f'<div class="ln" style="--ln-yw:{yw};--ln-xr:{xr}"><div class="ln-plot">'
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
                            f'color:var(--portal-card)">{tx}</span>')
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
        rows=[[名称, 値, 区分?], ...]。区分: 0(省略可)/"hino"/"avg"(1 は廃止。ADR-14)。
        avg 行は末尾へ寄せ、それ以外は入力順(=降順で用意される)を保つ(値ソートはしない)。
        幅=max(v/top, 1%)。色: hino→cat2 / avg→cat5 / ほか→cat1(柱ごとのタブでは柱の色)。"""
        src = b["rows"]

        def flag(r):
            return r[2] if len(r) > 2 else 0
        rows = [r for r in src if flag(r) != "avg"] + [r for r in src if flag(r) == "avg"]
        vals = [r[1] for r in rows]
        top = nice_max(max(vals)) if vals else 1
        hino_fill, avg_fill = cat_fill(HINO_CAT - 1), cat_fill(AVG_CAT - 1)
        fill0 = self.single_fill()
        unit = b.get("unit", "")
        has_hino = has_avg = False
        hrows = []
        for r in rows:
            name, v, f = r[0], r[1], flag(r)
            self.audit(tab_id, block_no, "chart.rank", name, v, b.get("unit"), b.get("source"))
            if f == "hino":
                fill, cls = hino_fill, "em"
                has_hino = True
            elif f == "avg":
                fill, cls = avg_fill, "avg"
                has_avg = True
            elif f == 0:
                fill, cls = fill0, ""
            else:
                raise ValueError(f"rank_bars の区分は 0・hino・avg だけ(1 は廃止。ADR-14): {f!r}")
            hrows.append({"label": name, "value": fmt(v) + unit, "cls": cls,
                          "segs": [(max(v / top, 0.01), fill)]})
        body = self._hbar(hrows, self._vw(r["value"] for r in hrows))
        leg_names, leg_cols = [], []
        if has_hino:
            leg_names.append("日野町")
            leg_cols.append(hino_fill)
        if has_avg:
            leg_names.append("平均")
            leg_cols.append(avg_fill)
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
          (連続を切らない=source持ちの並びで連続を見る)
        - asset を省いた figure も連続を区切り、それ自身は表示しない(PLAN_SCHEMA §4・§6-B figure)"""
        display, run_src, last_idx = set(), None, None
        for i, b in enumerate(tab.get("blocks", [])):
            if b.get("type") == "heading" or is_figure_link(b):
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

    def _used_ids(self):
        """タブ id とブロックの anchor(目次で注入したものを含む)。SHOGO_ID と重ならないことを確かめる。"""
        ids = set()

        def walk(o):
            if isinstance(o, dict):
                for k in ("anchor", "_toc_id"):
                    if isinstance(o.get(k), str):
                        ids.add(o[k])
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)
        for tab in self.d["tabs"]:
            ids.add(tab["id"])
            walk(tab.get("blocks", []))
        return ids

    def shogo(self):
        """原典との照合の表示(PLAN_SCHEMA §11.3)。(上部の1行, 末尾の節) を返す。
        件数は台帳から audit_display.count で数える(手で書かない。不変の制約4)。"""
        pid = self.pid
        s = audit_display.count(pid, audit_display.rows_from_renderer(self.audit_rows))
        if s is None:                      # "_" で始まる見本(_charttest)は出さない
            return "", ""
        if SHOGO_ID in self._used_ids():
            raise ValueError(f"タブ id かブロックの anchor が「原典との照合」の id と重なります: {SHOGO_ID}")
        more = f'<a href="#{SHOGO_ID}">くわしく</a>'
        ledger = (f'<p>照合の記録（台帳）：<a href="{esc(LEDGER_URL.format(pid))}" target="_blank" '
                  f'rel="noopener">GitHub で見る</a></p>')
        if s["n"] == 0:
            line = f'<p class="shogo-line">原典との照合：{NO_TARGET}{more}</p>'
            sec = (f'<section class="shogo" id="{SHOGO_ID}"><h2>原典との照合</h2>'
                   f'<p>{NO_TARGET}。</p>{ledger}</section>')
            return line, sec

        def k(v):
            return f'<span class="num">{v:,}</span>件'
        line = (f'<p class="shogo-line">原典との照合：<b>{k(s["n"])}のうち {k(s["m"])}が済み</b>'
                f'{more}</p>')
        parts = [f"{lbl}{k(s[key])}" for lbl, key in
                 (("プログラム ", "a"), ("AI（2回）", "b"), ("人 ", "c")) if s[key]]
        body = [f'<p>照合の対象 <b>{k(s["n"])}</b>のうち、済み <b>{k(s["m"])}</b>'
                + (f'（{"／".join(parts)}）' if parts else "") + "</p>"]
        if s["u"]:
            body.append(f'<p>済んでいないもの <b>{k(s["u"])}</b></p><ul class="shogo-why">'
                        + "".join(f"<li>{audit_display.reason_text(esc(r), k(v))}</li>" for r, v in s["reasons"])
                        + "</ul>")
        else:
            body.append("<p>済んでいないもの：なし</p>")
        if s["f"]:
            body.append("<p>照合済みのうち、AI だけで照合したものの一部は、読み違いがないかを人が抜き取りで"
                        f'確かめています（済み {k(s["x"])}／{k(s["f"])}）。</p>')
        body.append('<h3>照合の方法</h3><ul class="shogo-how">'
                    + "".join(f"<li>{esc(t)}</li>" for t in SHOGO_HOW) + "</ul>")
        body.append(ledger)
        if s["last"]:
            y, mo, dd = (int(x) for x in s["last"].split("-"))
            body.append(f'<p>記録の最終日：<span class="num">{y}年{mo}月{dd}日</span></p>')
        sec = (f'<section class="shogo" id="{SHOGO_ID}"><h2>原典との照合</h2>'
               + "".join(body) + "</section>")
        return line, sec

    def tab_pillar(self, tab):
        """実施計画の柱ごとのタブ(最初の見出しが「政策の柱N 柱の名前」)なら N(ADR-14)。名前が
        hino-portal DESIGN.md 3.6 の表と合わなければ止める。"""
        if self.pid not in PILLAR_PLANS:
            return None
        h = next((b for b in tab.get("blocks", []) if b.get("type") == "heading"), None)
        m = PILLAR_TAB.match(str(h.get("text", ""))) if h else None
        if not m:
            return None
        n = int(m.group(1))
        if m.group(2).strip() != PILLARS[n - 1]:
            raise ValueError(f"タブ {tab['id']}: 政策の柱{n} の名前が合わない: {m.group(2)!r}")
        return n

    def render(self):
        check_portal_colors()
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
            self.pillar = self.tab_pillar(tab)
            blocks = "".join(self.block(b, tab["id"], bi)
                             for bi, b in enumerate(tab["blocks"]))
            active = " active" if ti == 0 else ""
            pl = f' style="{self.pillar_vars(self.pillar)}"' if self.pillar else ""
            panels_html.append(f'<section class="panel{active}" role="tabpanel" '
                               f'data-panel="{esc(tab["id"])}"{pl}>{blocks}</section>')
        self.pillar = None

        period = m["period"]
        ps, pe = str(period["start"]), str(period["end"])
        if ps == pe == "記載なし":
            ptxt = "原典に記載なし"
        elif pe == "定めなし":
            ptxt = f"{ps}年度〜(終わりの定めなし)"
        else:
            ptxt = f"{ps}〜{pe}年度"
        metarow = (f'<span>計画期間 <b class="num">{esc(ptxt)}</b>'
                   + (f'（{esc(m["period_note"])}）' if m.get("period_note") else "") + "</span>"
                   f'<span>所管 <b>{esc(m["dept"])}</b></span>')
        if m.get("parent_plan"):
            metarow += f'<span>上位計画 <b>{esc(m["parent_plan"])}</b></span>'
        asof = self.asof()  # 公表の時期はページの見出しの「資料の時点」に出す(二度出さない。hino-portal DESIGN.md 2.4)

        vision = ""
        if m.get("vision"):
            vision = (f'<div class="vision-banner"><span class="lbl">基本理念</span>'
                      f'<span>{esc(m["vision"])}</span></div>')

        sizes = source_sizes(self.pid)
        sources = "<ul>" + "".join(
            "<li>" + (f'<a href="{esc(s["url"])}">{esc(s["label"])}</a>' if s.get("url") else esc(s["label"]))
            + (f'（{sizes[s["url"]]}）' if s.get("url") in sizes else "") + "</li>"
            for s in d["sources"]) + "</ul>"

        contact = f'お問い合わせ：{esc(m["dept"])}'
        if m.get("tel"):
            contact += f'（<span class="num">{esc(m["tel"])}</span>）'
        foot = {
            "このページについて": ([HOW_MADE], ""),
            "出典": ([sources], ""),
            "資料の時点": ([asof], ""),
            "注意": ([], f'<p><a href="{esc(m["url"])}">この計画の日野町の公式ページ</a></p><p>{contact}</p>'),
            "データ": ([f'<a href="{esc(DATA_URL.format(self.pid))}">data/plans/{esc(self.pid)}.json</a>'
                        "（JSON。GitHub）。原典との照合の記録は、上の「原典との照合」の節から開けます。"], ""),
            "ソースコード": ([portal.REPO_URL, portal.REPO_NAME], ""),
            "ライセンス": ([portal.LICENSE_HTML], ""),
        }
        if not asof:   # 資料の時点が分からないときは項目ごと飛ばす(部品見本 _charttest)
            del foot["資料の時点"]
        footer = portal.footer_block(foot)

        shogo_line, shogo_sec = self.shogo()  # 全タブを描いて照合表の行がそろってから数える

        tokens = load_tokens(TPL / "tokens.css")
        shell_css = apply_tokens(
            (TPL / "shell.css").read_text(encoding="utf-8"), tokens, "shell.css")

        page = (TPL / "shell.html").read_text(encoding="utf-8")
        for k, v in {
            "{{TITLE}}": esc(m.get("name_short", m["name"])),
            "{{PORTAL_CSS}}": portal.css_block(),
            "{{PORTAL_HEADER}}": portal.header_block(),
            "{{PORTAL_PAGE}}": portal.page_head(esc(m["name"]), esc(m["lead"]),
                                                f"資料の時点：{asof}" if asof else ""),
            "{{PORTAL_FOOTER}}": footer,
            "{{DESCRIPTION}}": esc(m["lead"]),
            "{{PAGE_URL}}": PAGE_URL.format(self.pid),
            "{{CSS}}": shell_css,
            "{{EYEBROW}}": (f'<div class="eyebrow">{esc(m["eyebrow"])}</div>'
                            if m.get("eyebrow") else ""),
            "{{METAROW}}": metarow,
            "{{SHOGO_LINE}}": shogo_line,
            "{{SHOGO}}": shogo_sec,
            "{{VISION_BANNER}}": vision,
            "{{TABS}}": "".join(tabs_html),
            "{{PANELS}}": "".join(panels_html),
            "{{RUNTIME}}": (TPL / "runtime.js").read_text(encoding="utf-8"),
        }.items():
            if k not in page:
                raise ValueError(f"templates/shell.html: {k} が無い")
            page = page.replace(k, v)
        return page

    def asof(self):
        """資料の時点(HTML)。meta.posted があればそれ(「公表」)。無ければ出典の資料名に書かれた年月(日)を使い、
        どちらも無ければ空。値を推定で足さない(不変の制約1)。"""
        m = self.d["meta"]
        if m.get("posted"):
            return f'公表 {esc(m["posted"])}'
        for s in self.d["sources"]:
            hit = DATE_IN_LABEL.search(s["label"])
            if hit:
                return f"{esc(hit.group(0))}（出典の資料名による）"
        return ""


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    pid = argv[1]
    src = ROOT / "data" / "plans" / f"{pid}.json"
    d = json.loads(src.read_text(encoding="utf-8"), parse_float=Num)

    r = Renderer(d, pid)
    try:
        page = r.render()
    except audit_display.AuditDisplayError as e:
        print(f"NG  原典との照合の件数を数えられません(PLAN_SCHEMA §11): {e}", file=sys.stderr)
        return 1

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
