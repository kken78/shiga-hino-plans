#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fuhyo.py — 当初予算の附表 data/fuhyo/<id>.json → build/audit/<id>.tsv(PLAN_SCHEMA §9)

附表は計画ではないのでページを作らず、照合表だけを計画と同じ8列(DESIGN.md ADR-7 補遺 E)で出す。
検収の道具(audit_worksheet・audit_autocheck・audit_review・audit_index)は、id が附表のとき、
ここで附表を計画の JSON と同じ形(名前・出典の一覧)にして読む。
段1(audit_autocheck.py)は、附表の行をここの stage1 で原典の表の列と行に照らす(DESIGN.md ADR-7 補遺 L「附表の段1の規則」)。

使い方: python3 tools/fuhyo.py <id>
  先に python3 tools/validate_schema.py data/fuhyo/<id>.json で検証する。

照合表の行(PLAN_SCHEMA §9「検収」):
  柱ごと   … 事業ごとに 予算額・款・担当課・区分(印があるときだけ)、柱の最後に 事業数
  附表全体 … 区分の印が付いた事業数
  事業名は項目名に入れ、附表の中で同じ名前の事業には「(同じ名前のN件目)」を付ける。
  出典の欄は「<原典のファイル名(.pdf を除く)> p.<ページ>」。原典は sources_raw/<id>/<ファイル名> に置く。
"""
import html
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FUHYO_DIR = ROOT / "data" / "fuhyo"
PLANS_DIR = ROOT / "data" / "plans"
AUDIT = ROOT / "build" / "audit"
SOURCES_INDEX = ROOT / "data" / "sources_index.tsv"

TAB = "fuhyo"   # 照合表の tab の欄
AUDIT_HEAD = "tab\tblock_no\tbtype\tblock_label\tlabel\tvalue\tunit\tsource"
WHOLE = "附表全体"   # 附表全体の行の block_label
# 文字の行(款・担当課・区分)。段1は下の「附表の段1の規則」で照らす。誤りを入れた写し(--selftest)は text_mutants で作る
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


# ---------------------------------------------------------------- 段1(DESIGN.md ADR-7 補遺 L「附表の段1の規則」)
WORD_RE = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>')
PAGE_RE = re.compile(r'<page width="([\d.]+)" height="([\d.]+)">')
HEADS = ["款", "事業名", "事業概要", "予算額", "担当課"]   # 表の見出しの行の語(左から)
AMOUNT_RE = re.compile(r"\d{1,3}(?:,\d{3})*")             # 予算額の列の数字(原典の表記「109,836」)
TOL = 1.5          # 列の端の位置の許し(pt)
SAME_NAME = re.compile(r"\(同じ名前の\d+件目\)$")
ITEM_FIELD = {"fuhyo.kan": "kan", "fuhyo.dept": "dept", "fuhyo.kubun": "kubun"}


def _squash(s):
    return re.sub(r"\s+", "", s)


class Layout:
    """原典の附表の表を、座標つきの文字データ(pdftotext -bbox)から列と行に分けたもの。

    列: 表の見出しの行(款・事業名・事業概要・予算額・担当課)がある最初のページで、見出しの語の中心より左の範囲に
        最も多く現れる語の左端を、その列の文字の左端とする(予算額は、カンマ付きの数字の最も多い右端)。附表は
        全ページで列の位置が同じ(Excel から作った表)とみなす。款の列より左は区分の印の列。
    行: ページの頭の行(文書名)と、表の見出しの行から上は表の外。予算額の列の数字を上から並べ、隣り合う数字の
        高さの中点を行の境目とする。各列の語は、高さの中心が入る行に属し、行の中では上から順につなげる。"""

    def __init__(self, bbox_html):
        self.pages = []   # [(幅, 高さ, [(x0, y0, x1, y1, 語)])]
        for chunk in bbox_html.split("<page ")[1:]:
            m = PAGE_RE.match("<page " + chunk)
            words = [(float(a), float(b), float(c), float(d), html.unescape(w))
                     for a, b, c, d, w in WORD_RE.findall(chunk)]
            self.pages.append((float(m.group(1)), float(m.group(2)), words) if m else (0.0, 0.0, words))
        self.head = [self._head_row(ws) for _, _, ws in self.pages]
        first = next((h for h in self.head if h), None)
        if first is None:
            raise ValueError("表の見出しの行(款・事業名・事業概要・予算額・担当課)が原典に見つからない")
        c = [(first[k][0] + first[k][2]) / 2 for k in HEADS]   # 見出しの語の中心
        allw = [w for _, _, ws in self.pages for w in ws]

        def mode(vals):
            vals = [round(v) for v in vals]
            return max(set(vals), key=lambda v: (vals.count(v), -v)) if vals else None
        self.kan = mode([w[0] for w in allw if w[0] <= c[0]])
        self.name = mode([w[0] for w in allw if c[0] < w[0] <= c[1]])
        self.desc = mode([w[0] for w in allw if c[1] < w[0] <= c[2]])
        self.dept = mode([w[0] for w in allw if c[3] < w[0] <= c[4]])
        self.amount = mode([w[2] for w in allw if c[2] < w[2] <= c[4] and AMOUNT_RE.fullmatch(w[4])])
        self.rows = [self._rows(i) for i in range(len(self.pages))]

    @staticmethod
    def _head_row(words):
        """表の見出しの行。{語: その語} か None。"""
        for w in words:
            if w[4] != "予算額":
                continue
            row = {x[4]: x for x in words if x[4] in HEADS and abs(x[1] - w[1]) < 3}
            if len(row) == len(HEADS):
                return row
        return None

    def col(self, w):
        x0, _, x1, _, s = w
        if AMOUNT_RE.fullmatch(s) and abs(x1 - self.amount) <= TOL:
            return "amount"
        if x1 < self.kan - TOL / 3:
            return "kubun"
        for name, edge in (("dept", self.dept), ("desc", self.desc), ("name", self.name), ("kan", self.kan)):
            if x0 >= edge - TOL:
                return name
        return "kubun"

    def top(self, i):
        """表の上端(これより上は表の外)。見出しの行があればその下端、なければページの頭の行の下端。"""
        words = self.pages[i][2]
        if self.head[i]:
            return max(w[3] for w in self.head[i].values())
        if not words:
            return 0.0
        y = min(w[1] for w in words)
        return max(w[3] for w in words if w[1] < y + 2)

    def heading(self, i):
        """表の見出しの行より上(ページの頭の行を除く)の文字。柱の見出しを探すのに使う。"""
        if not self.head[i]:
            return ""
        words = self.pages[i][2]
        y = min(w[1] for w in words)
        first = max(w[3] for w in words if w[1] < y + 2)
        htop = min(w[1] for w in self.head[i].values())
        return _squash("".join(w[4] for w in sorted(words, key=lambda w: (w[1], w[0])) if first <= w[1] < htop))

    def _rows(self, i):
        top = self.top(i)
        words = [w for w in self.pages[i][2] if w[1] >= top]
        amts = sorted((w for w in words if self.col(w) == "amount"), key=lambda w: (w[1] + w[3]) / 2)
        ys = [(w[1] + w[3]) / 2 for w in amts]
        cuts = [(a + b) / 2 for a, b in zip(ys, ys[1:])]
        rows = [{"amount": w[4].replace(",", ""), "name": [], "kan": [], "dept": [], "kubun": [], "dept_y": set()}
                for w in amts]
        for w in sorted(words, key=lambda w: (w[1], w[0])):
            c = self.col(w)
            if c in ("amount", "desc") or not rows:
                continue
            yc = (w[1] + w[3]) / 2
            k = sum(1 for x in cuts if yc >= x)
            rows[k][c].append(w[4])
            if c == "dept":
                rows[k]["dept_y"].add(round(w[1]))
        for r in rows:
            for c in ("name", "kan", "dept"):
                r[c] = _squash("".join(r[c]))
            # 担当課のセルの行の数(課が複数ある事業と、1つの課名が折り返している事業は2以上)
            r["dept_lines"] = len(r.pop("dept_y"))
        return rows


def layout_of(src):
    """出典(audit_autocheck.Source)の原典の Layout。座標つきの文字データは src.dir に置いて使い回す。"""
    if getattr(src, "_fuhyo_layout", None) is None:
        f = src.dir / "bbox.html"
        if not f.exists():
            subprocess.run(["pdftotext", "-bbox", str(src.pdf), str(f)], check=True, capture_output=True)
        src._fuhyo_layout = Layout(f.read_text(encoding="utf-8", errors="replace"))
    return src._fuhyo_layout


def _item(label):
    """照合表の項目名の事業の部分(「(同じ名前のN件目)」を含む。附表の中で一意)。"""
    return label.rsplit("/", 1)[0]


def _name(label):
    """原典の事業名と比べる名前(「(同じ名前のN件目)」を除く)。"""
    return SAME_NAME.sub("", _item(label))


def stage1(rows, srcs, page_list):
    """照合表の附表の行(btype が fuhyo.)の段1の結果。{id(行): {"pass", "why", "h2"}}。
    page_list は出典の欄から印刷ページの一覧を返す関数(pdf_pages.page_list)。附表のページの差は 0。"""
    out = {}
    fz = [r for r in rows if r["btype"].startswith("fuhyo.")]
    by_page = defaultdict(list)   # (出典id, ページ) → そのページの予算額の行(照合表の順)
    for r in fz:
        if r["btype"] == "fuhyo.amount":
            pg = page_list(r["source"])
            if len(pg) == 1:
                by_page[(r["source"].split(" ", 1)[0], pg[0])].append(r)

    def page_ok(sid, p, lay):
        """そのページの予算額の並びが原典の列の数字の並びと、数も順番も同じか。(合うか, 理由)"""
        mine = by_page.get((sid, p), [])
        src = [x["amount"] for x in lay.rows[p - 1]]
        if len(mine) != len(src):
            return False, f"ページの予算額の数が原典と違う(原典 {len(src)}・照合表 {len(mine)})"
        if [x["value"] for x in mine] != src:
            return False, "ページの予算額の並びが原典と違う"
        items = [_item(x["label"]) for x in mine]
        if len(set(items)) != len(items):
            return False, "ページに同じ事業の予算額の行が2つある"
        return True, ""

    for r in fz:
        res = {"pass": False, "why": "", "h2": False}
        out[id(r)] = res
        sid = r["source"].split(" ", 1)[0]
        pages = page_list(r["source"])
        src = srcs.get(sid)
        if src is None or not pages or not all(src.has_page(p) for p in pages):
            res.update(why="出典の PDF やページがない", h2=True)
            continue
        try:
            lay = layout_of(src)
        except ValueError as e:
            res.update(why=str(e), h2=True)
            continue
        bt = r["btype"]
        if bt == "fuhyo.count":
            a, b = pages[0], pages[-1]
            pname = _squash(r["block_label"].split(" ", 1)[1]) if " " in r["block_label"] else ""
            if not lay.head[a - 1] or not pname or pname not in lay.heading(a - 1):
                res["why"] = "出典の最初のページに、この柱の見出しと表の見出しの行がない"
            elif any(lay.head[q - 1] for q in range(a + 1, b + 1)):
                res["why"] = "出典のページの範囲に、別の柱の始まりがある"
            elif b < len(lay.pages) and not lay.head[b]:
                res["why"] = "柱が出典のページの範囲の次のページに続いている"
            else:
                n = sum(len(lay.rows[q - 1]) for q in range(a, b + 1))
                res.update(**{"pass": str(n) == r["value"]},
                           why="" if str(n) == r["value"] else f"柱の予算額の数が原典と違う(原典 {n})")
            continue
        if bt == "fuhyo.kubun_count":
            if pages[0] != 1 or pages[-1] != len(lay.pages):
                res.update(why="出典が附表の全ページでない", h2=True)
                continue
            n = sum(1 for rs in lay.rows for x in rs if x["kubun"])
            ok = str(n) == r["value"]
            res.update(**{"pass": ok}, why="" if ok else f"区分の印のある行の数が原典と違う(原典 {n})", h2=not ok)
            continue
        if len(pages) != 1:
            res["why"] = "出典が1ページでない"
            continue
        p = pages[0]
        ok, why = page_ok(sid, p, lay)
        if not ok:
            res["why"] = why
            continue
        mine = by_page[(sid, p)]
        item, name = _item(r["label"]), _name(r["label"])
        if bt == "fuhyo.amount":
            k = next(i for i, x in enumerate(mine) if x is r)
        else:
            k = next((i for i, x in enumerate(mine) if _item(x["label"]) == item), None)
            if k is None:
                res["why"] = "その事業の予算額の行が同じページにない"
                continue
        row = lay.rows[p - 1][k]
        if row["name"] != _squash(name):
            res["why"] = "事業名が原典の同じ行の事業名と違う"
            continue
        m = re.search(r"\(同じ名前の(\d+)件目\)$", item)
        if m:
            # 同じ名前の事業は、番号が原典の中でその名前が出る順番(ページ順・行順)と合うこと
            seen = [(q, j) for q, rs in enumerate(lay.rows, 1) for j, x in enumerate(rs) if x["name"] == row["name"]]
            n = int(m.group(1))
            if n > len(seen) or seen[n - 1] != (p, k):
                res["why"] = "同じ名前の事業の番号が、原典でその名前が出る順番と合わない"
                continue
        if bt == "fuhyo.amount":
            res["pass"] = True        # 並びは page_ok で確かめた
        elif bt == "fuhyo.kubun":
            ok = row["kubun"] == r["value"].split("・")
            res.update(**{"pass": ok}, why="" if ok else "区分の印が原典の同じ行と違う")
        elif bt == "fuhyo.dept" and row["dept_lines"] >= 2:
            # 課の区切りとセルの幅での折り返しを見分けられないので、担当課のセルが2行以上の事業は段2に回す
            res["why"] = "担当課のセルが2行以上(課の区切りと折り返しを段1では見分けられない)"
        else:
            ok = row[ITEM_FIELD[bt]] == _squash("".join(r["value"].split("・")))
            res.update(**{"pass": ok}, why="" if ok else f"{KIND[bt]}が原典の同じ行と違う")
    return out


def text_mutants(r, rows, rng):
    """誤りを入れた写し(audit_autocheck.py --selftest)のうち、附表の文字の行(款・担当課・区分)のもの。
    (行, 種類, (kind, 値)) を返す。kind は value・source・swap(audit_autocheck.mutants と同じ)。"""
    v = r["value"]
    c = {}
    same = [x for x in rows if x is not r and x["btype"] == r["btype"] and x["value"] != v]
    near = [x for x in same if x["source"] == r["source"]] or same
    if near:
        c["T10別の事業の値"] = ("value", rng.choice(near)["value"])
    if len(v) >= 2:
        i = rng.randrange(len(v))
        c["T11一文字の脱落"] = ("value", v[:i] + v[i + 1:])
        pool = sorted({ch for x in same for ch in x["value"]} - {v[i]})
        if pool:
            c["T12一文字の置き換え"] = ("value", v[:i] + rng.choice(pool) + v[i + 1:])
    m = re.search(r"p\.(\d+)$", r["source"])
    if m:
        n2 = int(m.group(1)) + (1 if rng.random() < .5 else -1)
        if n2 >= 1:
            c["T7出典ページずれ"] = ("source", r["source"][:m.start(1)] + str(n2))
    sw = [x for x in same if x["tab"] == r["tab"] and x["block_no"] == r["block_no"]]
    if sw:
        c["T9項目名の入れ替え"] = ("swap", rng.choice(sw))
    for t, cc in c.items():
        yield r, t, cc


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
