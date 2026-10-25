#!/usr/bin/env python3
"""原典の文字(PLAN_SCHEMA §10。DESIGN.md ADR-10)を作る。

台帳 data/sources_index.tsv の PDF の行ごとに、sources_raw/<id>/<file> の sha256 が台帳と同じかを確かめてから、
  data/text/<id>/<名前>.txt      pdftotext -enc UTF-8(既定の取り出し方)
  data/text_raw/<id>/<名前>.txt  pdftotext -raw -enc UTF-8(書かれた順)
を取り出したまま書き、台帳の text_chars・text_method・text_date を書く(列が無ければ note の前に足す)。

使い方: python3 tools/extract_text.py youho                 # youho の PDF の行すべて
        python3 tools/extract_text.py youho/honpen.pdf      # 1行だけ(id/file)
        python3 tools/extract_text.py --all                 # 台帳の PDF の行すべて
原典が無い・sha256 が違う・改ページで区切ったページの数が台帳の pages と合わない行が1つでもあれば、
どのファイルも台帳も書かずに NG を表示して終了コード 1。
"""
import datetime
import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "data" / "sources_index.tsv"
RAW = ROOT / "sources_raw"
TEXT = ROOT / "data" / "text"
TEXT_RAW = ROOT / "data" / "text_raw"
METHOD = "pdftotext"   # 台帳の text_method(data/text/ の取り出し方。PLAN_SCHEMA §10.2)
NEW_COLS = ["text_method", "text_date"]   # note の前に足す列(PLAN_SCHEMA §10.2)


def read_index():
    """台帳の列名と行(列名 → 値の dict)の一覧。"""
    lines = INDEX.read_text(encoding="utf-8").splitlines()
    head = lines[0].split("\t")
    rows = []
    for n, ln in enumerate(lines[1:], 2):
        f = ln.split("\t")
        if len(f) != len(head):
            sys.exit(f"NG  {INDEX}: {n}行目の列の数が {len(f)}({len(head)} のはず)")
        rows.append(dict(zip(head, f)))
    return head, rows


def write_index(head, rows):
    out = ["\t".join(head)] + ["\t".join(r.get(c, "") for c in head) for r in rows]
    INDEX.write_text("\n".join(out) + "\n", encoding="utf-8")


def is_pdf(r):
    return r["file"].lower().endswith(".pdf")


def stem(r):
    return r["file"][:-len(".pdf")]


def text_path(r):
    return TEXT / r["id"] / f"{stem(r)}.txt"


def text_raw_path(r):
    return TEXT_RAW / r["id"] / f"{stem(r)}.txt"


def count_chars(s):
    """空白以外の字の数(台帳の text_chars。改ページ \\f や全角の空白も空白に数える)。"""
    return sum(1 for c in s if not c.isspace())


def count_pages(s):
    """改ページで区切ったページの数。pdftotext はページごとに末尾へ \\f を出すので、\\f の数がページの数。"""
    return s.count("\f")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fp:
        for b in iter(lambda: fp.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def pdftotext(pdf, raw):
    cmd = ["pdftotext"] + (["-raw"] if raw else []) + ["-enc", "UTF-8", str(pdf), "-"]
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} が終了コード {p.returncode}: {p.stderr.decode('utf-8', 'replace').strip()}")
    return p.stdout.decode("utf-8")


def extract(r):
    """1行の原典の文字(既定・-raw)を取り出す。止める理由があれば (None, 理由)。"""
    pdf = RAW / r["id"] / r["file"]
    if not pdf.exists():
        return None, f"原典が無い: {pdf}"
    got = sha256(pdf)
    if got != r["sha256"]:
        return None, f"sha256 が台帳と違う: {got}(台帳 {r['sha256']})"
    try:
        text, raw = pdftotext(pdf, False), pdftotext(pdf, True)
    except RuntimeError as e:
        return None, str(e)
    for name, s in (("既定", text), ("-raw", raw)):
        if str(count_pages(s)) != r["pages"]:
            return None, f"{name}の取り出しのページの数が {count_pages(s)}(台帳の pages は {r['pages']})"
    return (text, raw), None


def select(rows, args):
    """引数(id か id/file、または --all)に当たる PDF の行。当たらない引数があれば止める。"""
    pdf_rows = [r for r in rows if is_pdf(r)]
    if args == ["--all"]:
        return pdf_rows
    picked = []
    for a in args:
        rid, _, f = a.partition("/")
        hit = [r for r in pdf_rows if r["id"] == rid and (not f or r["file"] == f)]
        if not hit:
            sys.exit(f"NG  台帳に PDF の行が無い: {a}")
        picked += [r for r in hit if r not in picked]
    return picked


def main():
    args = sys.argv[1:]
    if not args or ("--all" in args and len(args) > 1):
        sys.exit(__doc__)
    head, rows = read_index()
    targets = select(rows, args)

    # 1) すべての行を取り出して確かめる。1行でも止める理由があれば、何も書かない
    got, bad = [], 0
    for r in targets:
        res, why = extract(r)
        if why:
            print(f"NG  {r['id']}/{r['file']}: {why}")
            bad += 1
        else:
            got.append((r, res))
    if bad:
        print(f"{bad} 行で止めた。ファイルも台帳も書いていない")
        sys.exit(1)

    # 2) 書く
    if any(c not in head for c in NEW_COLS):
        i = head.index("note")
        head = head[:i] + [c for c in NEW_COLS if c not in head] + head[i:]
    today = datetime.date.today().isoformat()
    for r, (text, raw) in got:
        for path, s in ((text_path(r), text), (text_raw_path(r), raw)):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(s, encoding="utf-8")
        old = r["text_chars"]
        r["text_chars"] = str(count_chars(text))
        r["text_method"] = METHOD
        r["text_date"] = today
        note = "" if old == r["text_chars"] else f"(text_chars {old or '空'} → {r['text_chars']})"
        print(f"OK  {r['id']}/{r['file']}: {r['pages']} ページ、{r['text_chars']} 字 {note}".rstrip())
    write_index(head, rows)
    print(f"{len(got)} 行を書いた: {TEXT.relative_to(ROOT)}/・{TEXT_RAW.relative_to(ROOT)}/・{INDEX.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
