#!/usr/bin/env python3
"""audit_review.py — 段4(人による確認)をスマホでするための確認ページのデータを作り、結果を台帳に取り込む
(DESIGN.md ADR-7 補遺 M)。標準ライブラリ + pdftoppm / pdftotext(poppler-utils)+ tesseract(画像のページだけ)。

使い方:
  python3 tools/audit_review.py [<id> ...]
      → 人が確認する行(台帳の note が「要人確認:」の行。人の印がまだない行が残る計画と、すでに確認ページに
        載っている計画)について、出典ページの画像と、
        ページの中で値・項目名が書かれている位置を調べ、確認ページ一式を build/audit/review/ に書く
          build/audit/review/index.html          確認ページ(templates/audit_review.html に計画の一覧を入れたもの)
          build/audit/review/plans/<id>.json     計画ごとの行とページ画像(JPEG を data URI で持つ)
        id を省くと、それらの全計画。claude.ai の非公開のページとして公開し、スマホで開く(公開しない
        GitHub Pages には置かない)。
  python3 tools/audit_review.py --import <結果.json>
      → 確認ページの結果(「書き出し」で保存したファイル、または Claude がページのデータベースから読み出したもの)を
        台帳 data/audit_log/<id>.tsv に取り込む。
          一致      … checked=✓・reviewer=確認者・date を入れる(note は「要人確認:…」のまま。コメントがあれば後ろに足す)
          違う      … 印を外す(未検収に戻す)。note に「要人確認:… / 不一致: 正しい値 <値> <コメント>」を書く
                      → data/plans/<id>.json を直して再描画し、audit_worksheet.py <id> --reconcile で台帳を合わせる
          保留      … 機械の印は変えず(人の印があれば外し)、note に「保留: <コメント>」を足す
        人が確認済みの行への「一致」は飛ばし、「違う」「保留」は取り込む(点検表で見つけた確認間違いの訂正)
        取り込んだあと audit_autocheck.py <id> --status で完了を確かめ、台帳をコミットする。

結果ファイルの形: [{"plan": id, "key": 照合キー, "verdict": "ok"|"ng"|"hold", "value": 正しい値(ng のとき),
                   "comment": …, "reviewer": GitHub のユーザー名, "date": "YYYY-MM-DD"}, …]

位置の調べ方: ページの文字データ(pdftotext -bbox)で値と同じ数字の語を探す。見つからないページ(画像だけのページ、
画像で貼られた表・グラフ)は、300dpi の画像を tesseract で読んで探す。値が何か所にもあるときは、項目名(年度・
系列名など)の語に最も近いものを「ここ」として示す。位置は目安で、照合そのものは人が画像で行う。
"""
import base64
import html
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit_autocheck as ac  # noqa: E402  Source・nums・canon・year_variants・page_list を共用
import audit_worksheet as aw  # noqa: E402

ROOT = aw.ROOT
OUT = aw.AUDIT_DIR / "review"
TEMPLATE = ROOT / "templates" / "audit_review.html"
DPI = 140            # 画面に出すページ画像の解像度(A4 で幅 1157px)
MAX_SIDE = 2400      # A3 などの大きなページでも長い辺をこれ以下にする
JPEG_Q = 60
BUDGET = 10_000_000  # 計画1本のデータの目安(公開する1ファイルの上限は16MB。スマホで読み込む量も抑える)。超えたら解像度を下げる
OCR_DPI = 300
WORD_RE = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>')
PAGE_RE = re.compile(r'<page width="([\d.]+)" height="([\d.]+)">')
REASONS = {"H1": "AI 2人の判定が分かれた・確信が低い", "H2": "数値でない・数えたり計算したりした値",
           "H4": "AI の照合の抜き取り確認"}


def run(cmd):
    try:
        return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout
    except FileNotFoundError:
        raise ac.Stop(f"{cmd[0]} がありません(poppler-utils・tesseract-ocr・tesseract-ocr-jpn を入れてください)")


# ---------------------------------------------------------------- ページの画像と語の位置
class Page:
    """PDF の1ページ。語の位置はページの幅・高さに対する割合(0〜1)で持つ。"""

    def __init__(self, src, pdfp):
        self.src, self.pdfp = src, pdfp
        cache = src.dir / f"bbox{pdfp:03d}.html"
        if not cache.exists():
            run(["pdftotext", "-bbox", "-f", str(pdfp), "-l", str(pdfp), str(src.pdf), str(cache)])
        text = cache.read_text(encoding="utf-8", errors="replace")
        m = PAGE_RE.search(text)
        if not m:
            raise ac.Stop(f"ページの大きさが取れません: {src.pdf} の {pdfp} ページ")
        self.w, self.h = float(m.group(1)), float(m.group(2))
        self.words = [(html.unescape(t), (float(a) / self.w, float(b) / self.h, float(c) / self.w, float(d) / self.h))
                      for a, b, c, d, t in WORD_RE.findall(text)]
        self._ocr = None

    def ocr_words(self):
        """tesseract で読んだ語(画像だけのページや、画像で貼られた表・グラフのため)。結果はキャッシュする。"""
        if self._ocr is None:
            tsv = self.src.dir / f"ocr{self.pdfp:03d}.tsv"
            if not tsv.exists():
                png = self.src.dir / f"ocr{self.pdfp:03d}"
                run(["pdftoppm", "-r", str(OCR_DPI), "-gray", "-png", "-singlefile", "-f", str(self.pdfp),
                     "-l", str(self.pdfp), str(self.src.pdf), str(png)])
                run(["tesseract", str(png) + ".png", str(tsv)[:-4], "-l", "jpn", "--psm", "11", "tsv"])
                Path(str(png) + ".png").unlink()
            W, H = self.w * OCR_DPI / 72, self.h * OCR_DPI / 72
            out = []
            for ln in tsv.read_text(encoding="utf-8", errors="replace").splitlines()[1:]:
                f = ln.split("\t")
                if len(f) == 12 and f[0] == "5" and f[11].strip():
                    x, y, w, h = (int(v) for v in f[6:10])
                    out.append((f[11].strip(), (x / W, y / H, (x + w) / W, (y + h) / H)))
            self._ocr = out
        return self._ocr

    def image(self, dpi=DPI):
        f = self.src.dir / f"view{self.pdfp:03d}-{dpi}.jpg"
        if not f.exists():
            r = min(dpi, MAX_SIDE * 72 / max(self.w, self.h))
            run(["pdftoppm", "-r", f"{r:.1f}", "-jpeg", "-jpegopt", f"quality={JPEG_Q}", "-singlefile",
                 "-f", str(self.pdfp), "-l", str(self.pdfp), str(self.src.pdf), str(f)[:-4]])
        return "data:image/jpeg;base64," + base64.b64encode(f.read_bytes()).decode("ascii")


def value_boxes(words, value):
    v = ac.canon(value)
    return [b for t, b in words if v in ac.nums(t)]


def label_boxes(words, label):
    """項目名の部分(「/」で区切った各部分)ごとに、その部分が書かれている語の位置。"""
    out = []
    for part in ac.parts(label):
        yv = ac.year_variants(part)
        core = re.sub(r"\([^)]*\)$", "", re.sub(r"\s+", "", ac.nfkc(part)))
        hit = []
        for t, b in words:
            c = re.sub(r"\s+", "", ac.nfkc(t))
            if yv is not None:
                ok = any(re.search(re.escape(y) + r"(?!\d)", c) for y in yv)
            else:
                # 原典の語は文の切れ端のことが多いので、項目名を「の」「・」などで切った3文字以上の切れ端でも探す
                pieces = [x for x in re.split(r"[()・、/の]", core) if len(x) >= 3]
                ok = len(c) >= 2 and len(core) >= 2 and (core in c or (c in core and (len(c) >= 3 or c == core))
                                                          or any(x in c for x in pieces))
            if ok:
                hit.append(b)
        if hit:
            out.append(hit)
    return out


def near(v, p):
    """値の位置 v と項目名の位置 p の近さ。同じ行(高さが重なる)・同じ列(幅が重なる)なら近いとみなす。"""
    vx, vy = (v[0] + v[2]) / 2, (v[1] + v[3]) / 2
    px, py = (p[0] + p[2]) / 2, (p[1] + p[3]) / 2
    if v[1] < p[3] and p[1] < v[3]:
        return abs(vx - px) * 0.3
    if v[0] < p[2] and p[0] < v[2]:
        return abs(vy - py) * 0.3
    return ((vx - px) ** 2 + (vy - py) ** 2) ** 0.5


def locate(page, row):
    """値と項目名の位置。値が文字データに無ければ OCR で探す。"""
    words, how = page.words, "text"
    vb = value_boxes(words, row["value"]) if ac.is_num(row["value"]) else []
    if ac.is_num(row["value"]) and not vb:
        words, how = page.ocr_words(), "ocr"
        vb = value_boxes(words, row["value"])
    lb = label_boxes(words, row["label"])
    focus = None
    if vb:
        focus = min(range(len(vb)), key=lambda i: sum(min(near(vb[i], p) for p in grp) for grp in lb) if lb else 0)
    rnd = lambda b: [round(x, 4) for x in b]  # noqa: E731
    return {"v": [rnd(b) for b in vb], "l": [rnd(b) for grp in lb for b in grp][:40],
            "focus": focus, "how": how if vb else ""}


# ---------------------------------------------------------------- 確認ページのデータ
def counted(plan, row):
    """「事業数」の行(render_plan.py が programs ブロックの items の数を照合表に出す行。ダッシュボードには出ない)が
    何を数えたか。節・小見出しごとに、数えた項目と、項目の無い(説明だけの)見出しを並べる。
    確認する人が原典のどの見出しの下の何を数えればよいか分かるようにするため(補遺 M)。"""
    if row["btype"] != "programs" or row["label"] != "事業数":
        return None
    tab = next(t for t in plan["tabs"] if t["id"] == row["tab"])
    b = tab["blocks"][int(row["block_no"])]
    name = lambda x: f'{x.get("no", "")} {x["name"]}'.strip()  # noqa: E731
    out = []
    for g in b["groups"]:
        if g.get("subgroups"):
            for sg in g["subgroups"]:
                out.append({"path": [name(g), name(sg)], "items": [name(i) for i in sg["items"]]})
        else:
            out.append({"path": [name(g)], "items": [name(i) for i in g["items"]]})
    n = sum(len(x["items"]) for x in out)
    if str(n) != row["value"]:
        raise ac.Stop(f"{row['tab']} #{row['block_no']}: 数えた項目 {n} が照合表の事業数 {row['value']} と合わない")
    return out


def human_done(r):
    return r["checked"] == "✓" and not r["reviewer"].startswith("auto:")


def focus_rows(pid):
    return [r for r in aw.read_ledger(pid) if r["note"].startswith(aw.FOCUS)]


def pending_rows(pid):
    return [r for r in focus_rows(pid) if not human_done(r)]


def build_plan(pid):
    plan, rows, srcs = ac.load(pid)
    if not focus_rows(pid):
        return None
    # 点検表で計画の全体が見えるよう、人が確認して台帳に記録済みの行も載せる(1行ずつの画面では飛ばす)
    todo = focus_rows(pid)
    pages, items, cache = {}, [], {}
    for r in todo:
        reason, _, memo = r["note"].partition(" / ")
        rule = reason[len(aw.FOCUS):].split(" ", 1)[0]
        sid = r["source"].split(" ", 1)[0]
        refs = []
        src = srcs.get(sid)
        for p in ac.page_list(r["source"]):
            if src is None or not src.has_page(p):
                continue
            pdfp = src.pdf_page(p)
            pg = cache.get((sid, pdfp)) or cache.setdefault((sid, pdfp), Page(src, pdfp))
            pref = f"{sid}:{pdfp}"
            if pref not in pages:
                pages[pref] = {"img": pg, "w": pg.w, "h": pg.h, "printed": p, "pdf": pdfp}
            refs.append({"page": pref, **locate(pg, r)})
        # 値が見つかったページを先に出す(範囲の出典で値が後ろのページにあるとき)
        refs.sort(key=lambda x: x["focus"] is None)
        items.append({"key": r["key"], "tab": r["tab"], "btype": r["btype"], "block": r["block_label"],
                      "label": r["label"], "value": r["value"], "unit": r["unit"], "source": r["source"],
                      "rule": rule, "why": reason[len(aw.FOCUS) + len(rule):].strip(), "memo": memo,
                      "ledger": {"by": r["reviewer"], "date": r["date"]} if human_done(r) else None,
                      "counted": counted(plan, r),
                      "refs": refs})
    # ページ画像。合計が目安を超える計画は、画素数が目安に収まるよう解像度を下げる(JPEG の大きさは画素数にほぼ比例)
    dpi = DPI
    for _ in range(3):
        imgs = {k: v["img"].image(dpi) for k, v in pages.items()}
        total = sum(len(x) for x in imgs.values())
        if total <= BUDGET:
            break
        dpi = int(dpi * (BUDGET / total) ** 0.5 * 0.95)
    if total > BUDGET:
        raise ac.Stop(f"{pid}: ページ画像が {total / 1e6:.1f}MB で目安の {BUDGET / 1e6:.0f}MB に収まらない(解像度 {dpi})")
    for k, v in pages.items():
        v["img"] = imgs[k]
    srcinfo = {s["id"]: {"label": s.get("label", s["id"]), "url": s.get("url", "")} for s in plan.get("sources", [])}
    return {"id": pid, "name": plan.get("meta", {}).get("name", pid), "sources": srcinfo,
            "rows": items, "pages": pages}


def plan_ids(argv):
    ids = [a for a in argv if not a.startswith("-")]
    if ids:
        return ids
    # 人が確認する行が残る計画と、すでに確認ページに載っている計画(全行の確認が済んでも点検表で見られるように残す)
    have = {f.stem for f in (OUT / "plans").glob("*.json")}
    return [f.stem for f in sorted((ROOT / "data" / "audit_log").glob("*.tsv")) if pending_rows(f.stem) or f.stem in have]


def manifest_order():
    """ハブのトップページの並び(manifest の順)。"""
    m = json.loads((ROOT / "data" / "manifest.json").read_text(encoding="utf-8"))
    return {p["id"]: i for i, p in enumerate(m["plans"])}


def entry(data, f):
    """一覧に載せる計画の情報。led は台帳に人の確認が記録済みの行のキー(進み具合の数え方に使う)。"""
    return {"id": data["id"], "name": data["name"], "n": len(data["rows"]), "bytes": f.stat().st_size,
            "led": [r["key"] for r in data["rows"] if r.get("ledger")]}


def cmd_build(argv):
    (OUT / "plans").mkdir(parents=True, exist_ok=True)
    order = manifest_order()
    catalog = []
    for pid in sorted(plan_ids(argv), key=lambda x: order.get(x, 999)):
        data = build_plan(pid)
        if data is None:
            print(f"--  {pid}: 人が確認する行(要人確認)がない")
            continue
        f = OUT / "plans" / f"{pid}.json"
        f.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        found = sum(1 for r in data["rows"] if any(x["focus"] is not None for x in r["refs"]))
        catalog.append(entry(data, f))
        print(f"OK  {f.relative_to(ROOT)}  ({len(data['rows'])} 行・位置が分かった行 {found}・ページ {len(data['pages'])}・"
              f"{f.stat().st_size / 1e6:.1f}MB)")
    # 引数で計画を絞ったときも、一覧には今ある計画のファイルをすべて載せる
    have = {c["id"] for c in catalog}
    for f in (OUT / "plans").glob("*.json"):
        if f.stem not in have and focus_rows(f.stem):
            catalog.append(entry(json.loads(f.read_text(encoding="utf-8")), f))
    catalog.sort(key=lambda c: order.get(c["id"], 999))
    page = TEMPLATE.read_text(encoding="utf-8")
    payload = json.dumps({"plans": catalog, "reasons": REASONS, "built": date.today().isoformat()},
                         ensure_ascii=False).replace("</", "<\\/")
    if "{{CATALOG}}" not in page:
        raise ac.Stop(f"{TEMPLATE} に {{{{CATALOG}}}} がありません")
    (OUT / "index.html").write_text(page.replace("{{CATALOG}}", payload), encoding="utf-8")
    total = sum(c["bytes"] for c in catalog)
    print(f"OK  {(OUT / 'index.html').relative_to(ROOT)}  (計画 {len(catalog)} 本・{sum(c['n'] for c in catalog)} 行・"
          f"データ {total / 1e6:.0f}MB)")
    return 0


# ---------------------------------------------------------------- 結果の取り込み
def cmd_import(path):
    res = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(res, list):
        raise ac.Stop("結果ファイルは配列であるべきです")
    by_plan = {}
    for x in res:
        for k in ("plan", "key", "verdict", "reviewer", "date"):
            if not str(x.get(k) or "").strip():
                raise ac.Stop(f"結果に {k} がない行があります: {x}")
        if x["verdict"] not in ("ok", "ng", "hold"):
            raise ac.Stop(f"verdict が不正です: {x}")
        if x["reviewer"].startswith("auto:"):
            raise ac.Stop(f"確認者が機械の名前になっています: {x}")
        if x["verdict"] == "ng" and not str(x.get("value") or "").strip():
            raise ac.Stop(f"「違う」なのに正しい値がありません: {x}")
        by_plan.setdefault(x["plan"], []).append(x)
    clean = lambda s: re.sub(r"[\t\r\n]+", " ", str(s or "")).strip()  # noqa: E731
    for pid, lst in by_plan.items():
        led = aw.read_ledger(pid)
        keyed = {r["key"]: r for r in led}
        miss = [x["key"] for x in lst if x["key"] not in keyed]
        if miss:
            raise ac.Stop(f"{pid}: 台帳に無い行の結果が {len(miss)} 件あります(値を直したあとの古い結果の可能性。"
                          f"先頭: {miss[:3]})。確認ページを作り直して確認し直してください")
        n = {"ok": 0, "ng": 0, "hold": 0, "skip": 0}
        for x in lst:
            L = keyed[x["key"]]
            if not L["note"].startswith(aw.FOCUS):
                raise ac.Stop(f"{pid}: 要人確認でない行の結果です: {x['key']}")
            if human_done(L) and x["verdict"] == "ok":
                n["skip"] += 1   # すでに人が確認した行(前に取り込んだ結果)
                continue
            # 人が確認済みの行でも「違う」「保留」は取り込む(点検表で見つけた確認間違いの訂正)
            reason = L["note"].split(" / ", 1)[0]
            cm = clean(x.get("comment"))
            if x["verdict"] == "ok":
                L.update(checked="✓", reviewer=clean(x["reviewer"]), date=clean(x["date"]),
                         note=reason + (f" / {cm}" if cm else ""))
            elif x["verdict"] == "ng":
                L.update(checked="", reviewer="", date="",
                         note=f"{reason} / 不一致: 正しい値 {clean(x['value'])}" + (f" {cm}" if cm else "")
                         + f"({clean(x['reviewer'])} {clean(x['date'])})")
            else:
                if human_done(L):
                    L.update(checked="", reviewer="", date="")
                L["note"] = f"{reason} / 保留: {cm}({clean(x['reviewer'])} {clean(x['date'])})"
            n[x["verdict"]] += 1
        aw.write_ledger(pid, led)
        print(f"OK  {aw.ledger_path(pid)}  (一致 {n['ok']}・違う {n['ng']}・保留 {n['hold']}・"
              f"取り込み済みで飛ばした {n['skip']})")
        if n["ng"]:
            print(f"    「違う」の行は data/plans/{pid}.json を直して再描画し、audit_worksheet.py {pid} --reconcile")
    return 0


def main(argv):
    try:
        if "--import" in argv:
            i = argv.index("--import")
            if i + 1 >= len(argv):
                raise ac.Stop("--import の後に結果のファイルを指定してください")
            return cmd_import(argv[i + 1])
        return cmd_build(argv[1:])
    except (ac.Stop, aw.AuditError) as e:
        print(f"NG  {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
