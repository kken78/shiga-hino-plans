#!/usr/bin/env python3
"""検収の機械照合(DESIGN.md ADR-7 補遺 L)。標準ライブラリ + pdftotext / pdftoppm(poppler-utils)。

原典 PDF は sources_raw/<id>/<出典id>.pdf に置く(git 管理外)。印刷ページから PDF のページへの換算は
data/plans/<id>.json の sources[].pdf_page_offset を使う。

使い方:
  python3 tools/audit_autocheck.py <id>                  # 段1の結果を表示する(台帳は変えない)
  python3 tools/audit_autocheck.py <id> --selftest       # 誤りを入れたコピーで段1の素通りを測る(2%超で失敗)
  python3 tools/audit_autocheck.py <id> --apply          # 段1で一致した未検収の行を台帳に auto:pdftext で記録
  python3 tools/audit_autocheck.py <id> --ai-items       # 段2(AI 2人)に渡す設問を build/audit/<id>.ai_items.json に書く
  python3 tools/audit_autocheck.py <id> --ai-record A.json B.json
                                                          # 段2の結果を取り込む。2人とも「一致・確信度 高」は auto:ai、
                                                          # それ以外は note に「要人確認:H1」を付ける
  python3 tools/audit_autocheck.py <id> --select         # H2・H4 の行の note に「要人確認:H2」「要人確認:H4」を付ける
  python3 tools/audit_autocheck.py <id> --status         # 完了条件を満たしているかを表示する(未完了なら終了コード 1)

人が見る行は、台帳の note が「要人確認:」で始まる行。ワークシートは
  python3 tools/audit_worksheet.py <id> --focus
で、その行だけを表示する。人が確かめると reviewer が人のユーザー名に変わる。

段1の規則(すべて満たすとき一致):
  1. 値が出典ページの文字データにある(全角半角・桁区切り・空白・▲と負号をそろえて比べる)
  2. 項目名が同じページにある(年度は R4・令和4・2022 も可。年度はすべて、ほかは1つ以上)
  3. 行(系列)の値が、原典の文字データで切れ目なく同じ順に並んでいる
  4. 紙面の配置どおりの文字データで、値の真上の列見出しが照合表の列名と同じ
"""
import json
import random
import re
import subprocess
import sys
import unicodedata
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit_worksheet as aw  # noqa: E402  read_audit / read_ledger / write_ledger / row_key を共用

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "sources_raw"
AUDIT_DIR = ROOT / "build" / "audit"
CACHE = AUDIT_DIR / ".cache"
NUM_RE = re.compile(r"[▲△\-−]?\d[\d,]*(?:\.\d+)?")
NUMBER = re.compile(r"-?\d+(\.\d+)?")
FOCUS = "要人確認:"
ESCAPE_LIMIT = 0.02


class Stop(Exception):
    pass


# ---------------------------------------------------------------- 文字のそろえ方
def nfkc(s):
    return unicodedata.normalize("NFKC", s)


def canon(tok):
    t = nfkc(tok).replace(",", "")
    return t.replace("▲", "-").replace("△", "-").replace("−", "-")


def nums(text):
    return [canon(m.group(0)) for m in NUM_RE.finditer(nfkc(text))]


def is_num(v):
    return NUMBER.fullmatch(v or "") is not None


def parts(label):
    return [x for x in label.split("/") if x]


ERA = {"R": ("令和", 2018), "H": ("平成", 1988), "S": ("昭和", 1925)}


def year_variants(part):
    """R4・令和4年・2022 などの言い換えの集合。年度でなければ None。"""
    p = nfkc(part)
    m = re.fullmatch(r"([RHS])(\d+|元)(年度|年)?(\(推計\))?", p)
    m2 = re.fullmatch(r"(令和|平成|昭和)(\d+|元)(年度|年)?", p)
    m3 = re.fullmatch(r"(\d{4})(年度|年)?", p)
    if m3:
        y = int(m3.group(1)); out = {str(y)}
        for k, (name, base) in ERA.items():
            n = y - base
            if 1 <= n <= 64:
                out |= {f"{name}{n}", f"{k}{n}"} | ({f"{name}元"} if n == 1 else set()) \
                    | ({f"{k}{n:02d}"} if n < 10 else set())
        return out
    if m:
        name, base = ERA[m.group(1)]
    elif m2:
        name = m2.group(1); base = {v[0]: v[1] for v in ERA.values()}[name]
    else:
        return None
    g = (m or m2).group(2)
    n = 1 if g == "元" else int(g)
    letter = [k for k, v in ERA.items() if v[0] == name][0]
    # R04 のような0付きの書き方(交付金の様式など)も同じ年度とみなす
    return {f"{name}{n}", f"{letter}{n}", str(base + n)} | ({f"{name}元"} if n == 1 else set()) \
        | ({f"{letter}{n:02d}"} if n < 10 else set())


def lcs_len(a, b):
    best = 0
    for i in range(len(a)):
        lo, hi = best + 1, len(a) - i
        while lo <= hi:
            mid = (lo + hi) // 2
            if a[i:i + mid] in b:
                best = mid; lo = mid + 1
            else:
                hi = mid - 1
    return best


def part_match(part, compact):
    """1 = 一致、0.5 = 一部一致、0 = 見つからない。"""
    yv = year_variants(part)
    if yv is not None:
        return 1 if any(re.search(re.escape(v) + r"(?!\d)", compact) for v in yv) else 0
    p = re.sub(r"\s+", "", nfkc(part))
    core = re.sub(r"\([^)]*\)$", "", p)
    if not core or p in compact or core in compact:
        return 1
    pieces = [x for x in re.split(r"[()・、/]", core) if len(x) >= 2]
    if pieces and all(x in compact for x in pieces):
        return 1
    n = lcs_len(core, compact)
    return 0.5 if n >= 3 and n >= 0.6 * len(core) else 0


# ---------------------------------------------------------------- 原典の文字データ
class Source:
    def __init__(self, pid, sid, offset):
        self.pdf = RAW / pid / f"{sid}.pdf"
        if not self.pdf.exists():
            raise Stop(f"原典 PDF がありません: {self.pdf}(sources_raw/<id>/<出典id>.pdf に置く)")
        self.offset = offset
        self.dir = CACHE / pid / sid
        self.dir.mkdir(parents=True, exist_ok=True)
        self._info = {}
        self.npages = None

    def has_page(self, printed):
        """印刷ページ printed が PDF の範囲にあるか(pdfinfo のページ数で判定)。"""
        if self.npages is None:
            try:
                out = subprocess.run(["pdfinfo", str(self.pdf)], check=True, capture_output=True, text=True).stdout
            except FileNotFoundError:
                raise Stop("pdfinfo がありません。poppler-utils を入れてください(例: sudo apt install poppler-utils)")
            m = re.search(r"^Pages:\s+(\d+)", out, re.M)
            self.npages = int(m.group(1)) if m else 0
        return 1 <= printed + self.offset <= self.npages

    def _text(self, pdfp, mode):
        f = self.dir / f"{mode}{pdfp:03d}.txt"
        if not f.exists():
            try:
                subprocess.run(["pdftotext", f"-{mode}", "-f", str(pdfp), "-l", str(pdfp), str(self.pdf), str(f)],
                               check=True, capture_output=True)
            except FileNotFoundError:
                raise Stop("pdftotext がありません。poppler-utils を入れてください(例: sudo apt install poppler-utils)")
        return f.read_text(encoding="utf-8", errors="replace")

    def image(self, pdfp):
        f = self.dir / f"p{pdfp:03d}.png"
        if not f.exists():
            subprocess.run(["pdftoppm", "-r", "110", "-png", "-singlefile", "-f", str(pdfp), "-l", str(pdfp),
                            str(self.pdf), str(f)[:-4]], check=True, capture_output=True)
        return f

    def info(self, printed):
        pdfp = printed + self.offset
        if pdfp in self._info:
            return self._info[pdfp]
        lay, raw = self._text(pdfp, "layout"), self._text(pdfp, "raw")
        if not lay.strip() and not raw.strip():
            self._info[pdfp] = None
            return None
        lines = lay.splitlines()
        info = {"pdfp": pdfp, "lines": lines, "line_nums": [nums(l) for l in lines], "stream": nums(raw),
                "compact": re.sub(r"\s+", "", nfkc(lay + "\n" + raw))}
        info["tokens"] = set(info["stream"]) | {t for l in info["line_nums"] for t in l}
        self._info[pdfp] = info
        return info


IMAGE_PAGE_CHARS = 50   # 1ページの文字データがこれより少なければ、画像だけのページとみなす(sources_index の「OCRが必要」と同じ目安)


def page_list(source):
    m = re.search(r"p\.(\d+)(?:-(\d+))?", source or "")
    if not m:
        return []
    a = int(m.group(1)); b = int(m.group(2) or a)
    return list(range(a, b + 1))


# ---------------------------------------------------------------- 段1
def _w(ch):
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def _cols(line):
    pos, x = [], 0
    for ch in line:
        pos.append(x); x += _w(ch)
    return pos


def _spans(line):
    pos = _cols(line)
    out = []
    for m in NUM_RE.finditer(line):
        a, b = m.start(), m.end() - 1
        out.append((canon(m.group(0)), (pos[a] + pos[b] + _w(line[b])) / 2))
    return out


def _variants(name):
    yv = year_variants(name)
    if yv is not None:
        return yv
    p = nfkc(name).replace(" ", "")
    out = {p, re.sub(r"\([^)]*\)$", "", p)} | {x for x in re.split(r"[()]", p) if len(x) >= 2}
    return {x for x in out if x}


def _centers(line, variants):
    # 位置は元の行の表示幅で測る(全角の括弧などは NFKC で半角になり幅が変わるため)。
    # NFKC で文字数が変わる行だけは、そろえた後の行で測る
    t = nfkc(line)
    pos = _cols(line) if len(t) == len(line) else _cols(t)
    res = []
    for v in variants:
        for m in re.finditer(re.escape(v) + r"(?!\d)", t):
            if m.start() > 0 and t[m.start() - 1].isdigit():
                continue
            b = m.end() - 1
            if b < len(pos):
                res.append((pos[m.start()] + pos[b] + 1) / 2)
    return res


def has_run(run, seq):
    n = len(run)
    return n >= 2 and any(seq[i:i + n] == run for i in range(len(seq) - n + 1))


def column_ok(run, idx, names, infos):
    variants = [_variants(n) for n in names]
    for info in infos:
        lines = info["lines"]
        for li, line in enumerate(lines):
            sp = _spans(line)
            toks = [s[0] for s in sp]
            for st in range(len(toks) - len(run) + 1):
                if toks[st:st + len(run)] != run:
                    continue
                x = sp[st + idx][1]
                for hj in range(li - 1, max(-1, li - 12), -1):
                    cands = [(abs(c - x), j) for j, v in enumerate(variants) for c in _centers(lines[hj], v)]
                    if len({j for _, j in cands}) >= max(2, (len(run) + 1) // 2):
                        return min(cands)[1] == idx
    return None


def groups_of(rows):
    g = defaultdict(list)
    for r in rows:
        if not is_num(r["value"]):
            continue
        ps = parts(r["label"])
        k = (r["tab"], r["block_no"])
        if len(ps) >= 2:
            g[k + ("A", ps[0])].append(r)
            g[k + ("B", "/".join(ps[1:]))].append(r)
        else:
            g[k + ("S", "")].append(r)
    by = defaultdict(list)
    for k, lst in g.items():
        for i, r in enumerate(lst):
            by[id(r)].append((k, i, lst))
    return by


def stage1(rows, srcs, only=None):
    """各行の段1の結果。{id(row): {...}}"""
    by = groups_of(rows)
    out = {}
    for r in rows:
        if only is not None and id(r) not in only:
            continue
        res = {"pass": False, "why": "", "h2": False, "score": 0}
        sid = (r["source"] or "").split(" ", 1)[0]
        pages = page_list(r["source"])
        src = srcs.get(sid)
        if not is_num(r["value"]):
            res.update(why="数値でない", h2=True); out[id(r)] = res; continue
        if len(pages) > 3:
            res.update(why="出典が4ページ以上(集計した値)", h2=True); out[id(r)] = res; continue
        if src is None:
            res.update(why="出典の PDF がない", h2=True); out[id(r)] = res; continue
        if not pages or not all(src.has_page(p) for p in pages):
            res.update(why="出典ページがない、または PDF の範囲にない", h2=True); out[id(r)] = res; continue
        infos = [i for i in (src.info(p) for p in pages) if i]
        if sum(len(i["compact"]) for i in infos) < IMAGE_PAGE_CHARS * max(1, len(pages)):
            # 原典が画像だけのページ(文字データがない・ほとんどない)。段1は効かないので段2(ページ画像)に回す
            res.update(why="原典が画像(文字データがない)"); out[id(r)] = res; continue
        v = canon(r["value"])
        if v not in set().union(*(i["tokens"] for i in infos)):
            res.update(why="値が文字データにない", h2=True); out[id(r)] = res; continue
        compact = "".join(i["compact"] for i in infos)
        ps = parts(r["label"])
        yr = [part_match(p, compact) for p in ps if year_variants(p) is not None]
        ny = [part_match(p, compact) for p in ps if year_variants(p) is None]
        lab = 0 if (yr and min(yr) < 1) else (1 if not ny else max(ny))
        seqs = [i["stream"] for i in infos] + [l for i in infos for l in i["line_nums"] if len(l) >= 2]
        run_ok = False; col = None
        for (k, idx, lst) in by[id(r)]:
            if len(lst) < 2:
                continue
            run = [canon(x["value"]) for x in lst]
            if k[2] in ("A", "S") and any(has_run(run, s) for s in seqs):
                run_ok = True
            if k[2] in ("A", "B"):
                names = (["/".join(parts(x["label"])[1:]) for x in lst] if k[2] == "A"
                         else [parts(x["label"])[0] for x in lst])
                c = column_ok(run, idx, names, infos)
                if c is not None:
                    col = c if col is None else (col and c)
        mult = sum(i["stream"].count(v) for i in infos)
        score = (0 if run_ok else 3) + (2 if lab < 1 else 0) + (2 if mult >= 3 else 0) \
            + (1 if r["btype"].startswith("chart") else 0) + (1 if len(v.lstrip("-").split(".")[0]) <= 1 else 0)
        res["score"] = score
        if lab == 1 and run_ok and col is True:
            res["pass"] = True
        else:
            why = []
            if lab < 1: why.append("項目名")
            if not run_ok: why.append("行の並び")
            if col is not True: why.append("列見出しの位置")
            res["why"] = "・".join(why) + " が確かめられない"
        out[id(r)] = res
    return out


def load(pid):
    plan_path = ROOT / "data" / "plans" / f"{pid}.json"
    if not plan_path.exists():
        raise Stop(f"計画がありません: {plan_path}")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    rows = aw.read_audit(pid)
    srcs = {}
    for s in plan.get("sources", []):
        if (RAW / pid / f"{s['id']}.pdf").exists():
            srcs[s["id"]] = Source(pid, s["id"], s.get("pdf_page_offset", 0))
    if not srcs:
        raise Stop(f"原典 PDF が1つもありません: {RAW / pid}/<出典id>.pdf")
    return plan, rows, srcs


# ---------------------------------------------------------------- 誤りを入れたコピー(自己検査)
def mutants(rows, srcs, rng):
    by = groups_of(rows)
    for r in rows:
        v = r["value"]
        if not is_num(v):
            continue
        c = {}
        d = [i for i, ch in enumerate(v) if ch.isdigit()]
        i = rng.choice(d); nv = v[:i] + rng.choice([x for x in "0123456789" if x != v[i]]) + v[i + 1:]
        if not re.match(r"-?0\d", nv):
            c["T1打ち間違い"] = ("value", nv)
        pr = [(a, b) for a, b in zip(d, d[1:]) if b == a + 1 and v[a] != v[b]]
        if pr:
            a, b = rng.choice(pr); nv = v[:a] + v[b] + v[a] + v[b + 1:]
            if not re.match(r"-?0\d", nv):
                c["T2桁の入れ替え"] = ("value", nv)
        f = float(v); dec = len(v.split(".")[1]) if "." in v else 0
        if f:
            nv = f"{f * 10:.{dec}f}" if rng.random() < .5 else f"{f / 10:.{dec}f}"
            if nv != v and float(nv):
                c["T3桁ずれ"] = ("value", nv)
        for (k, idx, lst) in by[id(r)]:
            nb = [lst[j]["value"] for j in (idx - 1, idx + 1) if 0 <= j < len(lst) and lst[j]["value"] != v]
            if nb:
                c.setdefault("T4隣の列の値" if k[2] in ("A", "S") else "T5隣の行の値", ("value", rng.choice(nb)))
        sid = (r["source"] or "").split(" ", 1)[0]
        if sid in srcs:
            toks = [t for p in page_list(r["source"]) for i in [srcs[sid].info(p)] if i
                    for t in i["stream"] if t != canon(v) and is_num(t) and len(t) >= 2]
            if toks:
                c["T6同じページの別の数字"] = ("value", rng.choice(toks))
        m = re.search(r"p\.(\d+)(-\d+)?", r["source"] or "")
        if m and not m.group(2):
            n2 = int(m.group(1)) + (1 if rng.random() < .5 else -1)
            if n2 >= 1:
                c["T7出典ページずれ"] = ("source", r["source"][:m.start(1)] + str(n2) + r["source"][m.end(1):])
        if dec:
            nv = f"{f:.{dec - 1}f}" if dec > 1 else str(round(f))
            if nv != v:
                c["T8丸め"] = ("value", nv)
        same = [x for x in rows if x is not r and x["tab"] == r["tab"] and x["block_no"] == r["block_no"]
                and x["value"] != v and is_num(x["value"])]
        if same:
            c["T9項目名の入れ替え"] = ("swap", rng.choice(same))
        for t, cc in c.items():
            yield r, t, cc


def cmd_selftest(pid):
    plan, rows, srcs = load(pid)
    base = stage1(rows, srcs)
    passed = {id(r) for r in rows if base[id(r)]["pass"]}
    tot = defaultdict(int); esc = defaultdict(int)
    rng = random.Random(20261010)
    for r, t, (kind, val) in mutants(rows, srcs, rng):
        if id(r) not in passed:
            continue
        rs = [dict(x) for x in rows]
        mi = rows.index(r); m = rs[mi]
        only = {id(m)}
        if kind == "value":
            m["value"] = val
        elif kind == "source":
            m["source"] = val
        else:
            o = rs[rows.index(val)]
            m["label"], o["label"] = o["label"], m["label"]; only.add(id(o))
        res = stage1(rs, srcs, only=only)
        tot[t] += 1
        esc[t] += all(res[i]["pass"] for i in only)
    T, E = sum(tot.values()), sum(esc.values())
    rate = E / T if T else 0.0
    print(f"段1で通る行 {len(passed)} / {len(rows)}。誤りを入れたコピー {T} 件のうち素通り {E} 件({rate:.1%})")
    for t in sorted(tot):
        print(f"  {t}: 素通り {esc[t]} / {tot[t]}")
    if rate > ESCAPE_LIMIT:
        print(f"NG  素通りが {ESCAPE_LIMIT:.0%} を超えた。この計画では段1を使わず、段2に回す(ADR-7 補遺 L)")
        return 1
    print("OK")
    return 0


# ---------------------------------------------------------------- 台帳
def ledger_by_key(pid):
    return {r["key"]: r for r in aw.read_ledger(pid)}


def save_ledger(pid, rows, led):
    aw.write_ledger(pid, [led[r["key"]] for r in rows])


def cmd_report(pid, apply=False):
    plan, rows, srcs = load(pid)
    res = stage1(rows, srcs)
    n = len(rows); p = sum(res[id(r)]["pass"] for r in rows); h2 = sum(res[id(r)]["h2"] for r in rows)
    print(f"{pid}: 照合表 {n} 行 / 段1で一致 {p} / 段2(AI)へ {n - p - h2} / H2(人) {h2}")
    if not apply:
        return 0
    led = ledger_by_key(pid)
    today = date.today().isoformat(); k = 0
    for r in rows:
        L = led[r["key"]]
        if res[id(r)]["pass"] and L["checked"] != "✓":
            L.update(checked="✓", reviewer="auto:pdftext", date=today); k += 1
    save_ledger(pid, rows, led)
    print(f"OK  台帳に auto:pdftext を {k} 行記録した")
    return 0


def cmd_ai_items(pid):
    plan, rows, srcs = load(pid)
    res = stage1(rows, srcs)
    led = ledger_by_key(pid)
    blocks = defaultdict(list)
    for r in rows:
        x = res[id(r)]
        if x["pass"] or x["h2"] or led[r["key"]]["checked"] == "✓":
            continue
        blocks[(r["tab"], r["block_no"])].append(r)
    items = []
    for (tab, bno), lst in blocks.items():
        sid = lst[0]["source"].split(" ", 1)[0]
        pages = sorted({p for r in lst for p in page_list(r["source"])})
        imgs = [str(srcs[sid].image(p + srcs[sid].offset)) for p in pages] if sid in srcs else []
        items.append({"ブロック": lst[0]["block_label"], "タブ": tab, "種類": lst[0]["btype"], "出典": lst[0]["source"],
                      "ページ画像": imgs,
                      "行": [{"key": r["key"], "項目": r["label"], "値": r["value"], "単位": r["unit"]} for r in lst]})
    out = AUDIT_DIR / f"{pid}.ai_items.json"
    out.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"OK  {out}  (ブロック {len(items)}・行 {sum(len(i['行']) for i in items)})")
    print("    AI 2人に別々に判定させ、[{\"key\":…,\"判定\":\"一致|不一致|判断できない\",\"確信度\":\"高|中|低\",\"原典の値\":…,\"理由\":…}] で返させる")
    return 0


def cmd_ai_record(pid, fa, fb):
    plan, rows, srcs = load(pid)
    led = ledger_by_key(pid)
    A = {x["key"]: x for x in json.loads(Path(fa).read_text(encoding="utf-8"))}
    B = {x["key"]: x for x in json.loads(Path(fb).read_text(encoding="utf-8"))}
    items = json.loads((AUDIT_DIR / f"{pid}.ai_items.json").read_text(encoding="utf-8"))
    keys = [x["key"] for it in items for x in it["行"]]
    miss = [k for k in keys if k not in A or k not in B]
    if miss:
        raise Stop(f"AI の結果に無い行が {len(miss)} 行あります(先頭: {miss[:3]})")
    today = date.today().isoformat(); ok = h1 = 0
    for k in keys:
        a, b = A[k], B[k]
        L = led.get(k)
        if L is None or L["checked"] == "✓":
            continue
        if a["判定"] == b["判定"] == "一致" and a["確信度"] == b["確信度"] == "高":
            L.update(checked="✓", reviewer="auto:ai", date=today); ok += 1
            if L["note"].startswith(FOCUS + "H1"):   # やり直しで一致になった行は、前回の H1 の印を外す
                L["note"] = ""
        else:
            why = f"A={a['判定']}/{a['確信度']} B={b['判定']}/{b['確信度']}"
            L["note"] = f"{FOCUS}H1 {why}"; h1 += 1
    save_ledger(pid, rows, led)
    print(f"OK  auto:ai {ok} 行・要人確認(H1) {h1} 行")
    return 0


def cmd_select(pid):
    plan, rows, srcs = load(pid)
    res = stage1(rows, srcs)
    led = ledger_by_key(pid)
    h2 = h4 = cleared = 0
    for r in rows:
        L = led[r["key"]]
        human = L["checked"] == "✓" and not L["reviewer"].startswith("auto:")
        if res[id(r)]["h2"] and not L["note"].startswith(FOCUS) and not human:
            L["note"] = f"{FOCUS}H2 {res[id(r)]['why']}"; h2 += 1
        elif not res[id(r)]["h2"] and L["note"].startswith(FOCUS + "H2") and not human:
            # 規則が変わって H2 でなくなった行(例: 画像だけのページ)は印を外し、段2に回す
            L["note"] = ""; cleared += 1
    blocks = defaultdict(list)
    has_h4 = set()   # すでに H4 の行があるブロック(人が確認済みの行も含めて数える)
    for r in rows:
        if led[r["key"]]["note"].startswith(FOCUS + "H4"):
            has_h4.add((r["tab"], r["block_no"]))
        if led[r["key"]]["reviewer"] == "auto:ai":
            blocks[(r["tab"], r["block_no"])].append(r)
    for b, lst in blocks.items():
        if b in has_h4:
            continue
        pick = max(lst, key=lambda x: (res[id(x)]["score"], [-ord(c) for c in x["key"]]))
        L = led[pick["key"]]
        L["note"] = f"{FOCUS}H4 難しさ{res[id(pick)]['score']}点"; h4 += 1
    save_ledger(pid, rows, led)
    print(f"OK  要人確認 H2 {h2} 行・H4 {h4} 行を付けた" + (f"(H2 でなくなった {cleared} 行の印を外した)" if cleared else ""))
    return 0


def cmd_status(pid):
    rows = aw.read_audit(pid)
    led = ledger_by_key(pid)
    un = [r for r in rows if led[r["key"]]["checked"] != "✓"]
    focus = [led[r["key"]] for r in rows if led[r["key"]]["note"].startswith(FOCUS)]
    pending = [L for L in focus if L["checked"] != "✓" or L["reviewer"].startswith("auto:")]
    auto = sum(1 for r in rows if led[r["key"]]["reviewer"].startswith("auto:"))
    human = sum(1 for r in rows if led[r["key"]]["checked"] == "✓" and not led[r["key"]]["reviewer"].startswith("auto:"))
    print(f"{pid}: {len(rows)} 行 / 未検収 {len(un)} / 機械 {auto} / 人 {human} / 要人確認 {len(focus)}(うち未了 {len(pending)})")
    issues = ROOT / "data" / "source_issues" / f"{pid}.tsv"
    undecided = 0
    if issues.exists():
        undecided = sum(1 for ln in issues.read_text(encoding="utf-8").splitlines()[1:] if ln.split("\t")[7] == "未判断")
        print(f"  段3の指摘のうち未判断 {undecided} 件(data/source_issues/{pid}.tsv)")
    else:
        print(f"  段3の記録がありません(data/source_issues/{pid}.tsv)")
    done = not un and not pending and issues.exists() and undecided == 0
    print("完了" if done else "未完了")
    return 0 if done else 1


def main(argv):
    if len(argv) < 2 or argv[1].startswith("-"):
        print(__doc__); return 2
    pid = argv[1]
    try:
        if "--selftest" in argv:
            return cmd_selftest(pid)
        if "--apply" in argv:
            return cmd_report(pid, apply=True)
        if "--ai-items" in argv:
            return cmd_ai_items(pid)
        if "--ai-record" in argv:
            i = argv.index("--ai-record")
            if len(argv) < i + 3:
                raise Stop("--ai-record の後に AI 2人分の結果ファイルを指定してください")
            return cmd_ai_record(pid, argv[i + 1], argv[i + 2])
        if "--select" in argv:
            return cmd_select(pid)
        if "--status" in argv:
            return cmd_status(pid)
        return cmd_report(pid)
    except (Stop, aw.AuditError) as e:
        print(f"NG  {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
