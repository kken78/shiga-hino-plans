#!/usr/bin/env python3
"""data/source_issues/<id>.tsv(原典の誤りの記録。ADR-7 補遺 L)の形式を検査する。

使い方: python3 tools/check_source_issues.py            # すべてのファイル
        python3 tools/check_source_issues.py kodomo     # 1計画
誤りがあれば NG を表示して終了コード 1。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "data" / "source_issues"
COLS = ["no", "kind", "page", "where", "issue", "evidence", "confidence", "status", "action"]
KINDS = {"原典の食い違い", "原典の誤字", "原典の論理", "ダッシュボード"}
CONF = {"高", "中", "低"}
STATUS = {"未判断", "ダッシュボードで対応", "原典どおり掲載(注記)", "原典どおり掲載", "対象外"}


def check(path):
    errs = []
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].split("\t") != COLS:
        return [f"1行目の列名が {COLS} と違う"]
    if not (ROOT / "data" / "plans" / f"{path.stem}.json").exists():
        errs.append(f"data/plans/{path.stem}.json がない")
    for n, line in enumerate(lines[1:], 2):
        f = line.split("\t")
        if len(f) != len(COLS):
            errs.append(f"{n}行目: 列の数が {len(f)}(9列のはず)")
            continue
        r = dict(zip(COLS, f))
        if r["no"] != str(n - 1):
            errs.append(f"{n}行目: no が {r['no']}({n - 1} のはず)")
        if r["kind"] not in KINDS:
            errs.append(f"{n}行目: kind が不正: {r['kind']}")
        if r["confidence"] not in CONF:
            errs.append(f"{n}行目: confidence が不正: {r['confidence']}")
        if r["status"] not in STATUS:
            errs.append(f"{n}行目: status が不正: {r['status']}")
        if r["status"] not in ("未判断",) and not r["action"]:
            errs.append(f"{n}行目: status が「{r['status']}」なのに action が空")
        if not r["issue"]:
            errs.append(f"{n}行目: issue が空")
    return errs


def main():
    ids = sys.argv[1:]
    paths = [DIR / f"{i}.tsv" for i in ids] if ids else sorted(DIR.glob("*.tsv"))
    bad = 0
    for p in paths:
        if not p.exists():
            print(f"NG  {p}: ファイルがない"); bad += 1; continue
        errs = check(p)
        n = len(p.read_text(encoding="utf-8").splitlines()) - 1
        if errs:
            bad += 1
            print(f"NG  {p}")
            for e in errs:
                print(f"    {e}")
        else:
            print(f"OK  {p}  ({n} 件)")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
