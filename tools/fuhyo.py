#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fuhyo.py — 当初予算の附表 data/fuhyo/<id>.json → build/audit/<id>.tsv(PLAN_SCHEMA §9)

附表は計画ではないのでページを作らず、照合表だけを計画と同じ8列(DESIGN.md ADR-7 補遺 E)で出す。
検収の道具(audit_worksheet・audit_autocheck・audit_review・audit_index)は、id が附表のとき、
ここで附表を計画の JSON と同じ形(名前・出典の一覧)にして読む。

使い方: python3 tools/fuhyo.py <id>
  先に python3 tools/validate_schema.py data/fuhyo/<id>.json で検証する。

照合表の行(PLAN_SCHEMA §9「検収」):
  柱ごと   … 事業ごとに 予算額・款・担当課・区分(印があるときだけ)、柱の最後に 事業数
  附表全体 … 区分の印が付いた事業数
  事業名は項目名に入れ、附表の中で同じ名前の事業には「(同じ名前のN件目)」を付ける。
  出典の欄は「<原典のファイル名(.pdf を除く)> p.<ページ>」。原典は sources_raw/<id>/<ファイル名> に置く。
"""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FUHYO_DIR = ROOT / "data" / "fuhyo"
PLANS_DIR = ROOT / "data" / "plans"
AUDIT = ROOT / "build" / "audit"
SOURCES_INDEX = ROOT / "data" / "sources_index.tsv"

TAB = "fuhyo"   # 照合表の tab の欄
AUDIT_HEAD = "tab\tblock_no\tbtype\tblock_label\tlabel\tvalue\tunit\tsource"
WHOLE = "附表全体"   # 附表全体の行の block_label
# 文字の行(款・担当課・区分)。段1(数値の照合)は効かないので段2(ページ画像)に回す(audit_autocheck.py)
TEXT_BTYPES = {"fuhyo.kan", "fuhyo.dept", "fuhyo.kubun"}
KIND = {"fuhyo.amount": "予算額", "fuhyo.kan": "款", "fuhyo.dept": "担当課", "fuhyo.kubun": "区分",
        "fuhyo.count": "柱の事業の数", "fuhyo.kubun_count": "区分の印が付いた事業の数"}
# 数の行が数えるもの。段2の設問(audit_autocheck.py --ai-items)と確認ページ(audit_review.py)に添える
COUNT_DEF = {
    "fuhyo.count": "原典のこの柱の見出しから次の柱の見出しの前までにある、表の行(事業)の数。1行が1事業(予算額が1つ)",
    "fuhyo.kubun_count": "原典の附表全体で、表の左の余白に「新規」「拡充」の印がある事業の数。両方の印がある事業も1件と数える",
}


def path(pid):
    return FUHYO_DIR / f"{pid}.json"


def is_fuhyo(pid):
    """id が附表か。計画(data/plans/<id>.json)があれば計画とみなす。"""
    return not (PLANS_DIR / f"{pid}.json").exists() and path(pid).exists()


def load(pid):
    return json.loads(path(pid).read_text(encoding="utf-8"))


def source_id(d):
    """照合表の出典の欄に書く id(原典のファイル名から .pdf を除いたもの)。"""
    return Path(d["source"]["file"]).stem


def title(d):
    return f'当初予算の附表({d["fiscal_year"]["label"]})'


def index_row(d):
    """出典の台帳 data/sources_index.tsv の、この附表の原典の行。無ければ None。"""
    if not SOURCES_INDEX.exists():
        return None
    lines = SOURCES_INDEX.read_text(encoding="utf-8").splitlines()
    head = lines[0].split("\t")
    for ln in lines[1:]:
        r = dict(zip(head, ln.split("\t")))
        if r.get("id") == d["source"]["id"] and r.get("file") == d["source"]["file"]:
            return r
    return None


def plan_view(d):
    """検収の道具が計画の JSON から使う部分(meta.name・sources)を附表から作る。附表そのものは fuhyo に持たせる。"""
    r = index_row(d) or {}
    return {"meta": {"name": title(d)},
            "sources": [{"id": source_id(d), "label": r.get("label") or d["source"]["file"], "url": r.get("url", "")}],
            "fuhyo": d}


def span(pages):
    a, b = min(pages), max(pages)
    return f"p.{a}" if a == b else f"p.{a}-{b}"


def entries(d):
    """照合表の行を [(8列のタプル, 柱, 事業)] で返す。柱・事業の無い行は None。"""
    sid = source_id(d)
    names = Counter(it["name"] for p in d["pillars"] for it in p["items"])
    seen = Counter()
    out, all_pages = [], []
    for p in d["pillars"]:
        bno, bl = str(p["no"]), f'柱{p["no"]} {p["name"]}'
        pages = []
        for it in p["items"]:
            name = it["name"]
            if names[name] > 1:
                # 同じ名前の事業(例: 款の違う「東近江行政組合負担金」)は出現順で区別する(PLAN_SCHEMA §9)
                seen[name] += 1
                name = f"{name}(同じ名前の{seen[name]}件目)"
            src = f'{sid} p.{it["page"]}'
            pages.append(it["page"])

            def row(btype, label, value, unit=""):
                out.append(((TAB, bno, btype, bl, label, str(value), unit, src), p, it))
            row("fuhyo.amount", f"{name}/予算額", it["amount"], "千円")
            row("fuhyo.kan", f"{name}/款", it["kan"])
            row("fuhyo.dept", f"{name}/担当課", "・".join(it["dept"]))
            if it["kubun"]:
                row("fuhyo.kubun", f"{name}/区分", "・".join(it["kubun"]))
        out.append(((TAB, bno, "fuhyo.count", bl, "事業数", str(len(p["items"])), "件", f"{sid} {span(pages)}"), p, None))
        all_pages += pages
    n = sum(1 for p in d["pillars"] for it in p["items"] if it["kubun"])
    out.append(((TAB, "0", "fuhyo.kubun_count", WHOLE, "区分の印が付いた事業数", str(n), "件",
                 f"{sid} {span(all_pages)}"), None, None))
    return out


def context(d, row):
    """確認ページ(audit_review.py)に添える、附表のデータでのその行の事業。照合表と台帳は変えない。"""
    bt = row["btype"]
    lines = []
    if bt in COUNT_DEF:
        if bt == "fuhyo.count":
            p = next(p for p in d["pillars"] if str(p["no"]) == row["block_no"])
            its = p["items"]
        else:
            its = [it for p in d["pillars"] for it in p["items"] if it["kubun"]]
        if str(len(its)) != row["value"]:
            raise ValueError(f'{row["label"]}: 数えた事業 {len(its)} が照合表の値 {row["value"]} と合わない')
        lines = [["数えるもの", COUNT_DEF[bt]], ["数えた事業", "、".join(it["name"] for it in its)]]
    else:
        hit = [(p, it) for r, p, it in entries(d)
               if r[2] == bt and r[3] == row["block_label"] and r[4] == row["label"]]
        if len(hit) == 1:
            p, it = hit[0]
            lines = [["柱", f'{p["no"]} {p["name"]}'], ["事業名", it["name"]], ["款", it["kan"]],
                     ["予算額", f'{it["amount"]:,} 千円'], ["担当課", "・".join(it["dept"])],
                     ["区分", "・".join(it["kubun"]) or "(印なし)"], ["ページ", it["page"]]]
    return {"kind": KIND.get(bt, bt), "tab": "附表", "head": "附表のデータ",
            "lines": [[k, str(v)] for k, v in lines if v not in (None, "")]}


def write_audit(pid):
    d = load(pid)
    rows = [r for r, _, _ in entries(d)]
    AUDIT.mkdir(parents=True, exist_ok=True)
    tsv = AUDIT / f"{pid}.tsv"
    tsv.write_text(AUDIT_HEAD + "\n" + "".join("\t".join(r) + "\n" for r in rows), encoding="utf-8")
    return tsv, len(rows)


def main(argv):
    if len(argv) != 2 or argv[1].startswith("-"):
        print(__doc__)
        return 2
    pid = argv[1]
    if not path(pid).exists():
        print(f"NG  附表がありません: {path(pid)}", file=sys.stderr)
        return 1
    if (PLANS_DIR / f"{pid}.json").exists():
        print(f"NG  同じ id の計画があります: {PLANS_DIR / f'{pid}.json'}(附表と計画の id は分ける)", file=sys.stderr)
        return 1
    tsv, n = write_audit(pid)
    print(f"OK  {tsv}  ({n} rows) — 原典PDFと突合してください")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
