#!/usr/bin/env python3
"""原典の文字(PLAN_SCHEMA §10。DESIGN.md ADR-10)を作る。

台帳 data/sources_index.tsv の PDF の行ごとに、sources_raw/<id>/<file> の sha256 が台帳と同じかを確かめてから、
  data/text/<id>/<名前>.txt      pdftotext -enc UTF-8(既定の取り出し方)に、行の終わりで消された「-」を戻したもの(PLAN_SCHEMA §10.1)
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
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "data" / "sources_index.tsv"
RAW = ROOT / "sources_raw"
TEXT = ROOT / "data" / "text"
TEXT_RAW = ROOT / "data" / "text_raw"
METHOD = "pdftotext+hyphen"   # 台帳の text_method(data/text/ の取り出し方。PLAN_SCHEMA §10.1・§10.2)
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


# ---- 行の終わりの「-」を戻す(PLAN_SCHEMA §10.1。hino-town-docs DOCS_SCHEMA.md §5.4 と同じ手順) ----

NS_X = "{http://www.w3.org/1999/xhtml}"
BAD_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")
HYPHEN_IGNORE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff\u202a-\u202e\u2066-\u2069"
                           "\u0590-\u08ff\ufb1d-\ufdff\ufe70-\ufefe]")   # 突き合わせで数えない字
WSPACE = re.compile(r"\s")


def bbox_lines(pdf):
    """-bbox-layout のページごとの [(行の文字, 既定の取り出しで「-」が消える行か)]。"""
    p = subprocess.run(["pdftotext", "-enc", "UTF-8", "-bbox-layout", str(pdf), "-"], capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(f"pdftotext -bbox-layout が終了コード {p.returncode}")
    out = BAD_XML.sub("", re.sub(r"<!DOCTYPE[^>]*>", "", p.stdout.decode("utf-8")))
    try:
        root = ET.fromstring(out)
    except ET.ParseError as e:
        raise RuntimeError(f"-bbox-layout の出力を読めない({e})")
    pages = []
    for page in root.iter(NS_X + "page"):
        lines = []
        for flow in page.findall(NS_X + "flow"):
            blocks = flow.findall(NS_X + "block")
            for j, block in enumerate(blocks):
                ls = block.findall(NS_X + "line")
                for k, line in enumerate(ls):
                    t = "".join((w.text or "") for w in line.findall(NS_X + "word"))
                    lines.append((t, t.endswith("-") and (k + 1 < len(ls) or j + 1 < len(blocks))))
        pages.append(lines)
    return pages


def restore_page(d, lines):
    """1ページの既定の取り出し d に、消された「-」と改行を戻す。突き合わせが合わなければ None。"""
    seq, cuts = [], []
    for t, cut in lines:
        seq.append(WSPACE.sub("", HYPHEN_IGNORE.sub("", t[:-1] if cut else t)))
        if cut:
            cuts.append(sum(len(x) for x in seq))
    if not cuts:
        return d
    if WSPACE.sub("", HYPHEN_IGNORE.sub("", d)) != "".join(seq):
        return None
    out, n, ci = [], 0, 0
    for ch in d:
        if not (WSPACE.match(ch) or HYPHEN_IGNORE.match(ch)):
            while ci < len(cuts) and cuts[ci] == n:
                out.append("-\n")
                ci += 1
            n += 1
        out.append(ch)
    return "".join(out) if ci == len(cuts) else None


def restore_hyphens(pdf, text):
    """(戻した後のテキスト、戻した「-」の数)。確かめが合わなければ RuntimeError。"""
    dp = text.split("\f")
    bp = bbox_lines(pdf)
    if len(dp) - 1 != len(bp):
        raise RuntimeError(f"既定の取り出し({len(dp) - 1}ページ)と -bbox-layout({len(bp)}ページ)のページの数が違う")
    res = []
    for k, lines in enumerate(bp):
        r = restore_page(dp[k], lines)
        if r is None:
            raise RuntimeError(f"{k + 1}ページで、既定の取り出しと -bbox-layout の字の並びが合わない")
        res.append(r)
    res.append(dp[-1])
    out = "\f".join(res)
    want = sum(t.count("-") for lines in bp for t, _ in lines)
    if out.count("-") != want:
        raise RuntimeError(f"戻した後の「-」({out.count('-')})が -bbox-layout の「-」({want})と合わない")
    if re.sub("-\n", "", out) != re.sub("-\n", "", text):
        raise RuntimeError("戻した「-」と改行を除いても、既定の取り出しに戻らない")
    return out, out.count("-") - text.count("-")


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
        text, back = restore_hyphens(pdf, text)
    except RuntimeError as e:
        return None, str(e)
    r["_back"] = back
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
        back = r.pop("_back", 0)
        if back:
            note = f"行の終わりの「-」を {back} 個戻した {note}".strip()
        print(f"OK  {r['id']}/{r['file']}: {r['pages']} ページ、{r['text_chars']} 字 {note}".rstrip())
    write_index(head, rows)
    print(f"{len(got)} 行を書いた: {TEXT.relative_to(ROOT)}/・{TEXT_RAW.relative_to(ROOT)}/・{INDEX.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
