#!/usr/bin/env python3
"""原典の文字(PLAN_SCHEMA §10.2。DESIGN.md ADR-10)を検証する。原典の PDF は使わない。

台帳 data/sources_index.tsv の PDF の行ごとに、次の4つを確かめる。
  1. data/text/<id>/<名前>.txt があること
  2. 台帳の text_chars が、そのファイルの空白以外の字の数と合うこと
  3. data/text_raw/<id>/<名前>.txt もあること
  4. data/text_raw/ のファイルの、改ページで区切ったページの数が台帳の pages と合うこと

使い方: python3 tools/check_source_text.py            # 台帳の PDF の行すべて
        python3 tools/check_source_text.py youho      # 1計画(附表なら fuhyo-r8)
誤りがあれば NG を表示して終了コード 1。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_text import ROOT, count_chars, count_pages, is_pdf, read_index, text_path, text_raw_path  # noqa: E402


def check(r):
    errs = []
    t, tr = text_path(r), text_raw_path(r)
    if not t.exists():
        errs.append(f"{t.relative_to(ROOT)} が無い")
    else:
        n = count_chars(t.read_text(encoding="utf-8"))
        if str(n) != r["text_chars"]:
            errs.append(f"text_chars が {r['text_chars'] or '空'}({t.relative_to(ROOT)} の空白以外の字は {n})")
    if not tr.exists():
        errs.append(f"{tr.relative_to(ROOT)} が無い")
    else:
        n = count_pages(tr.read_text(encoding="utf-8"))
        if str(n) != r["pages"]:
            errs.append(f"{tr.relative_to(ROOT)} のページの数が {n}(台帳の pages は {r['pages']})")
    return errs


def main():
    ids = sys.argv[1:]
    _, rows = read_index()
    rows = [r for r in rows if is_pdf(r) and (not ids or r["id"] in ids)]
    for i in ids:
        if not any(r["id"] == i for r in rows):
            print(f"NG  台帳に {i} の PDF の行が無い")
            sys.exit(1)
    bad = 0
    for r in rows:
        errs = check(r)
        if errs:
            bad += 1
            print(f"NG  {r['id']}/{r['file']}")
            for e in errs:
                print(f"    {e}")
        else:
            print(f"OK  {r['id']}/{r['file']}  ({r['pages']} ページ、{r['text_chars']} 字)")
    print(f"{len(rows)} 行のうち NG {bad} 行")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
