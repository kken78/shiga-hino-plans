#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""portal.py — 日野町データポータルの共通の部品を、ページに写す(DESIGN.md ADR-12)。

共通の部品(見出し・分野のナビ・ページの一覧・フッター)の正本は hino-portal の shared/ にある。
templates/portal/ はその写しで、手で変えない(正本を変えてから写し直す)。
ここでは、写しをそのまま使い、ページごとに変えてよいところ(いまの分野・ページの印、
フッターの {{…}} の中身)だけを入れる。正本と同じかは CI で hino-portal の shared/check.py が確かめる。

render_plan.py(計画のページ)と build.py(ハブ)が使う。
"""
import html
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORTAL = ROOT / "templates" / "portal"

AREA = "計画と予算"          # このリポジトリのページの分野(hino-portal DESIGN.md 2.1)
LIBRARY = "計画ライブラリ"    # siblings-plan.html の中の、このリポジトリのページの題


def _read(name):
    return (PORTAL / name).read_text(encoding="utf-8")


def css_block():
    """<head> に置く css ブロック。"""
    return ("<!-- portal-shared:css start --><style>\n" + _read("shell.css").rstrip("\n")
            + "\n</style><!-- portal-shared:css end -->")


def header_block():
    """<body> の最初に置く header ブロック。分野「計画と予算」のリンクに aria-current を付ける。"""
    src = _read("header.html").rstrip("\n")
    anchor = f">{AREA}</a>"
    if src.count(anchor) != 1:
        raise ValueError(f"templates/portal/header.html: 分野のリンク「{AREA}」が {src.count(anchor)} 件(期待1)")
    i = src.index(anchor)
    j = src.rindex("<a ", 0, i)
    src = src[:j] + src[j:i].replace("<a ", '<a aria-current="page" ', 1) + src[i:]
    return "<!-- portal-shared:header start -->\n" + src + "\n<!-- portal-shared:header end -->"


def siblings_block(current=None):
    """ページの一覧。current がその題のページなら <span aria-current="page"> にする(None なら全部リンク)。"""
    src = _read("siblings-plan.html").rstrip("\n")
    if current is not None:
        pat = re.compile(r'<a href="[^"]+">' + re.escape(current) + r"</a>")
        src, n = pat.subn(f'<span aria-current="page">{current}</span>', src, count=1)
        if n != 1:
            raise ValueError(f"templates/portal/siblings-plan.html: ページ「{current}」が見つからない")
    return "<!-- portal-shared:siblings start -->" + src + "<!-- portal-shared:siblings end -->"


def page_head(title, lead, asof, current=None):
    """ページの見出し(hino-portal DESIGN.md 2.2 の 2・3)。title・lead・asof は HTML(エスケープ済み)。"""
    parts = ['<div class="portal-page"><div class="portal-wrap">',
             f'<p class="portal-area">{AREA}</p>',
             f'<h1 class="portal-title">{title}</h1>',
             f'<p class="portal-lead">{lead}</p>']
    if asof:
        parts.append(f'<p class="portal-asof">{asof}</p>')
    parts.append(siblings_block(current))
    parts.append("</div></div>")
    return "\n".join(parts)


def footer_block(items):
    """footer.html の型に中身を入れる。

    items は dt の名前 → (置き換え, 足すもの)。置き換えは footer.html のその行の {{…}} に順に入れる
    HTML のリスト、足すものはその行の </dd> の直前に足す HTML(「注意」の固定の文のあとなど)。
    items に無い項目の行は飛ばす。項目の順と固定の文は footer.html のまま。"""
    out = []
    used = set()
    for line in _read("footer.html").rstrip("\n").split("\n"):
        m = re.match(r"<dt>(.*?)</dt>", line)
        if not m:
            out.append(line)
            continue
        key = m.group(1)
        if key not in items:
            continue
        reps, extra = items[key]
        holes = re.findall(r"\{\{[^}]*\}\}", line)
        if len(holes) != len(reps):
            raise ValueError(f"footer.html の「{key}」: 置き換えが {len(reps)} 個(穴は {len(holes)} 個)")
        for h, r in zip(holes, reps):
            line = line.replace(h, r, 1)
        if extra:
            k = line.rindex("</dd>")
            line = line[:k] + extra + line[k:]
        out.append(line)
        used.add(key)
    unknown = sorted(set(items) - used)
    if unknown:
        raise ValueError(f"footer.html に無い項目: {unknown}")
    body = "\n".join(out)
    if "{{" in body:
        raise ValueError("フッターに置き換えていない {{…}} が残っている")
    return "<!-- portal-shared:footer start -->\n" + body + "\n<!-- portal-shared:footer end -->"


REPO_URL = "https://github.com/kken78/shiga-hino-plans"
REPO_NAME = "kken78/shiga-hino-plans"
LICENSE_HTML = ("MIT License（プログラム）。データの構造は CC BY 4.0、原典から転記した文章・数値・図は"
                f'このリポジトリのライセンスの対象外です（<a href="{REPO_URL}#ライセンス">README の「ライセンス」</a>）。')


def esc(s):
    return html.escape(str(s), quote=True)
