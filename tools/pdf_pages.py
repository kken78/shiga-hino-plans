"""pdf_pages.py — 出典のページの読み取りと、印刷ページ → PDF のページの換算(PLAN_SCHEMA §4)。

audit_autocheck.py(段1・段2が原典 PDF のページを開く。audit_review.py も同じものを使う)と
render_plan.py(asset を省いた figure の #page=。PLAN_SCHEMA §6-B figure)が共有する。
"""
import re


def page_list(source):
    """source("honpen p.8-9" など)の印刷ページの一覧。ページがなければ空。"""
    m = re.search(r"p\.(\d+)(?:-(\d+))?", source or "")
    if not m:
        return []
    a = int(m.group(1)); b = int(m.group(2) or a)
    return list(range(a, b + 1))


def pdf_page(printed, offset=0, offsets=None):
    """印刷ページ → PDF のページ。pdf_page_offsets があれば範囲ごとの差を使い、どの範囲にも入らなければ None。"""
    if offsets:
        for e in offsets:
            if printed >= e["from"] and (e.get("to") is None or printed <= e["to"]):
                return printed + e["offset"]
        return None
    return printed + offset
