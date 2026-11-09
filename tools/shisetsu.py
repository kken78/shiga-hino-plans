#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""shisetsu.py — 施設概要の表 data/shisetsu/<id>.json → build/audit/<id>.tsv(PLAN_SCHEMA §12・DESIGN.md ADR-17)

施設概要の表は計画の一部だが、計画の JSON とは別のファイルに持ち、manifest に載せず、ページを作らない。
照合表だけを計画と同じ8列(DESIGN.md ADR-7 補遺 E)で出す。計画ページとハブの照合の件数(PLAN_SCHEMA §11)には数えない。
検収の道具(audit_worksheet・audit_autocheck・audit_review・audit_index)は、id が施設概要の表のとき、
ここで表を計画の JSON と同じ形(名前・出典の一覧)にして読む。
段1(audit_autocheck.py)は、施設概要の表の行をここの stage1 で照らす(DESIGN.md ADR-7 補遺 L「施設概要の表の段1の規則」)。

使い方:
  python3 tools/shisetsu.py <id>                      # 検算(§12.5)を通してから照合表 build/audit/<id>.tsv を書く
  python3 tools/shisetsu.py <id> --fill               # 読み替えの列(§12.3)を原典の字の列から作り、JSON を書き直す
  python3 tools/shisetsu.py <id> --draft <plan> <file> <出力.json>
                                                      # 起票の補助。原典の文字(data/text_raw・data/text)から下書きを作る
  python3 tools/shisetsu.py <id> --check-text         # 段1の行の並びと施設の数を、原典の文字 data/text_raw で照らす
                                                      #   (原典の PDF が無いときの確かめ。列の位置は照らさない。台帳は変えない)
  python3 tools/shisetsu.py --selftest                # 道具の検査(読み替え・検証・検算・照合表・段1。原典は使わない)
  先に python3 tools/validate_schema.py data/shisetsu/<id>.json で検証する(validate_schema.py はここの validate を呼ぶ)。

照合表の行(PLAN_SCHEMA §12.4):
  表ごと   … 施設ごとに、原典の表にある列すべて(建物 7行・上下水道 5行)、表の最後に 施設の数
  検算の目標 … totals の各行(block_no 0)
  出典の欄は「<表の id> p.<PDF のページ>」。原典のファイルは JSON の source から引く(sources_raw/<source.id>/<source.file>)。
"""
import html
import json
import random
import re
import subprocess
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHISETSU_DIR = ROOT / "data" / "shisetsu"
PLANS_DIR = ROOT / "data" / "plans"
FUHYO_DIR = ROOT / "data" / "fuhyo"
AUDIT = ROOT / "build" / "audit"
SOURCES_INDEX = ROOT / "data" / "sources_index.tsv"
ISSUES_DIR = ROOT / "data" / "source_issues"
TEXT_RAW = ROOT / "data" / "text_raw"
TEXT = ROOT / "data" / "text"

TAB = "shisetsu"   # 照合表の tab の欄
AUDIT_HEAD = "tab\tblock_no\tbtype\tblock_label\tlabel\tvalue\tunit\tsource"
TOTAL_BLOCK = "検算の目標"
KINDS = ("tatemono", "suido")
# 原典の表の列(左から。小分類・施設名を除く)。(項目, 照合表の btype, 列の見出し)。運営方法は建物の表だけ
COLS = [("chiku", "shisetsu.chiku", "地区"), ("tou", "shisetsu.tou", "棟数"), ("menseki", "shisetsu.menseki", "延床面積"),
        ("nendo", "shisetsu.nendo", "代表棟の建築年度"), ("kozo", "shisetsu.kozo", "代表棟の構造"),
        ("taishin", "shisetsu.taishin", "代表棟の耐震補強"), ("unei", "shisetsu.unei", "運営方法")]
KIND_COLS = {"tatemono": [c for c in COLS], "suido": COLS[:5]}
UNIT = {"tou": "棟", "menseki": "㎡"}
KIND = {"shisetsu.chiku": "地区", "shisetsu.tou": "棟数", "shisetsu.menseki": "延床面積", "shisetsu.nendo": "代表棟の建築年度",
        "shisetsu.kozo": "代表棟の構造", "shisetsu.taishin": "代表棟の耐震補強", "shisetsu.unei": "運営方法",
        "shisetsu.count": "表ごとの施設の数", "shisetsu.total": "検算の目標"}
COUNT_DEF = {"shisetsu.count": "原典のこの表の行(施設)の数。表の下の本文「〜施設は N 施設」の N と比べる"}
# 文字の行(段1は行の並びと列の位置で照らす。誤りを入れた写しは text_mutants で作る)
TEXT_BTYPES = {"shisetsu.chiku", "shisetsu.nendo", "shisetsu.kozo", "shisetsu.taishin", "shisetsu.unei"}
NUM_BTYPES = {"shisetsu.tou", "shisetsu.menseki"}

ID_RE = re.compile(r"^_?[a-z][a-z0-9_-]*$")
KEY_RE = re.compile(r"^s[0-9]{3,}$")
YM_RE = re.compile(r"^[0-9]{6}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
MENSEKI_RE = re.compile(r"^\d{1,3}(?:,\d{3})*$")
NENDO_RE = re.compile(r"^(?:([SHR])(\d+)|(\d{4}))$")
ERA_BASE = {"S": 1925, "H": 1988, "R": 2018}
SAME_NAME = re.compile(r"\(同じ名前の(\d+)件目\)$")

TOP = ["id", "plan", "edition", "source", "totals", "tables"]
TABLE = ["no", "dai", "chu", "kind", "page", "count", "count_page", "notes", "rows"]
ROW = ["key", "name", "chiku", "tou", "menseki", "nendo", "kozo", "taishin", "unei", "menseki_su", "nendo_seireki"]
TOTAL = ["what", "label", "value", "page"]
TOTAL_OPT = ["dai", "sa", "issue"]


class Stop(Exception):
    pass


def path(pid):
    return SHISETSU_DIR / f"{pid}.json"


def is_shisetsu(pid):
    """id が施設概要の表か。計画(data/plans/<id>.json)があれば計画とみなす。"""
    return not (PLANS_DIR / f"{pid}.json").exists() and path(pid).exists()


def load(pid):
    return json.loads(path(pid).read_text(encoding="utf-8"))


def _squash(s):
    return re.sub(r"\s+", "", s)


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


# ---------------------------------------------------------------- 読み替えの列(§12.3)
def menseki_su(s):
    """延床面積の字 → 数。「-」は None。形に合わなければ ValueError。"""
    if s == "-":
        return None
    if not isinstance(s, str) or not MENSEKI_RE.match(s):
        raise ValueError(f"延床面積の字の形が §12.3 の決まりに無い: {s!r}")
    return int(s.replace(",", ""))


def nendo_seireki(s):
    """代表棟の建築年度の字 → 西暦の年の数。「-」は None。形に合わなければ ValueError。"""
    if s == "-":
        return None
    m = NENDO_RE.match(s) if isinstance(s, str) else None
    if not m:
        raise ValueError(f"建築年度の字の形が §12.3 の決まりに無い: {s!r}")
    if m.group(3):
        return int(m.group(3))
    return ERA_BASE[m.group(1)] + int(m.group(2))


def fill(d):
    """読み替えの列を原典の字の列から作る(手で書かない。§12.3)。d を書き換えて返す。"""
    for t in d["tables"]:
        for r in t["rows"]:
            r["menseki_su"] = menseki_su(r["menseki"])
            r["nendo_seireki"] = nendo_seireki(r["nendo"])
    return d


# ---------------------------------------------------------------- JSON の書き出し(施設1行を1行に)
def dumps(d):
    j = lambda v: json.dumps(v, ensure_ascii=False)  # noqa: E731
    out = ["{"]
    for k in ("id", "plan", "edition", "source"):
        out.append(f'  {j(k)}: {j(d[k])},')
    out.append('  "totals": [')
    out.append(",\n".join(f"    {j(t)}" for t in d["totals"]))
    out.append("  ],")
    out.append('  "tables": [')
    tabs = []
    for t in d["tables"]:
        head = ", ".join(f"{j(k)}: {j(t[k])}" for k in TABLE if k != "rows")
        rows = ",\n".join("      {" + ", ".join(f"{j(k)}: {j(r[k])}" for k in ROW) + "}" for r in t["rows"])
        tabs.append(f"    {{ {head},\n      \"rows\": [\n{rows}\n      ] }}")
    out.append(",\n".join(tabs))
    out.append("  ]")
    out.append("}")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- 検証(§12.2・§12.3。validate_schema.py が呼ぶ)
def _fields(errs, where, obj, keys, optional=()):
    if not isinstance(obj, dict):
        errs.append((where, f"{{…}} の形で書く: {obj!r}"[:120]))
        return False
    for k in keys:
        if k not in obj:
            errs.append((where, f"必須フィールドがありません: {k}"))
    for k in obj:
        if k not in keys and k not in optional:
            errs.append((where, f"PLAN_SCHEMA §12 に無い項目: {k}(足すときは先に PLAN_SCHEMA を直す)"))
    return all(k in obj for k in keys)


def _text(errs, where, v, empty_ok=False):
    if not isinstance(v, str) or (not empty_ok and not v.strip()):
        errs.append((where, ("文字列" if empty_ok else "空でない文字列") + f"で書く: {v!r}"[:120]))
    elif re.search(r"[\t\r\n]", v):
        errs.append((where, f"改行やタブを入れない(折り返しはつなぐ。PLAN_SCHEMA §12.2): {v[:40]!r}"))


def validate(d, file_path):
    """施設概要の表の JSON の検証。[(場所, 内容)] を返す。検算(§12.5)は check_totals で別に行う。"""
    errs = []
    if not _fields(errs, "$", d, TOP):
        return errs
    stem = Path(file_path).stem
    sid = d["id"]
    if not isinstance(sid, str) or not ID_RE.match(sid):
        errs.append(("$.id", f"idは英小文字始まり[a-z0-9_-](検査用の見本だけ先頭に _): {sid!r}"))
    elif sid != stem:
        errs.append(("$.id", f"id はファイル名と同じにする: id {sid!r}・ファイル {stem!r}"))
    data_dir = Path(file_path).resolve().parent.parent
    if isinstance(sid, str):
        for sub, what in (("plans", "計画"), ("fuhyo", "附表")):
            if (data_dir / sub / f"{sid}.json").exists():
                errs.append(("$.id", f"同じ id の{what}があります(data/{sub}/{sid}.json)。施設概要の表の id と分ける"))
    plan = d["plan"]
    if not isinstance(plan, str) or not (data_dir / "plans" / f"{plan}.json").exists():
        errs.append(("$.plan", f"計画の id(data/plans/<id>.json があるもの): {plan!r}"))
    ed = d["edition"]
    if _fields(errs, "$.edition", ed, ["label", "ym"]):
        _text(errs, "$.edition.label", ed["label"])
        if not isinstance(ed["ym"], str) or not YM_RE.match(ed["ym"]):
            errs.append(("$.edition.ym", f"版の年月6桁の文字列: {ed['ym']!r}"))
        elif isinstance(sid, str) and isinstance(plan, str) and not sid.startswith("_") and sid != f"{plan}-{ed['ym']}":
            errs.append(("$.id", f"id は <計画の id>-<版の年月6桁>: {sid!r}(plan {plan!r}・ym {ed['ym']!r})"))
    src = d["source"]
    if _fields(errs, "$.source", src, ["id", "file", "sha256"]):
        _text(errs, "$.source.id", src["id"])
        _text(errs, "$.source.file", src["file"])
        if not isinstance(src["sha256"], str) or not SHA_RE.match(src["sha256"]):
            errs.append(("$.source.sha256", f"sha256 は16進の64字: {src['sha256']!r}"))
    totals = d["totals"]
    if not isinstance(totals, list) or not totals:
        errs.append(("$.totals", "検算の目標を1つ以上、一覧で書く"))
        totals = []
    whole = Counter()
    dais = set()
    for i, t in enumerate(totals):
        w = f"$.totals[{i}]"
        if not _fields(errs, w, t, TOTAL, TOTAL_OPT):
            continue
        if t["what"] not in ("tou", "menseki"):
            errs.append((f"{w}.what", f"tou(棟数)か menseki(延床面積): {t['what']!r}"))
            continue
        _text(errs, f"{w}.label", t["label"])
        if not _is_int(t["page"]) or t["page"] < 1:
            errs.append((f"{w}.page", f"PDF のページは1以上の整数: {t['page']!r}"))
        if t["what"] == "tou":
            if not _is_int(t["value"]):
                errs.append((f"{w}.value", f"棟数は整数(数値型)で書く: {t['value']!r}"))
        elif not isinstance(t["value"], str) or not MENSEKI_RE.match(t["value"]):
            errs.append((f"{w}.value", f"延床面積は原典の字(カンマ付きの文字列)で書く: {t['value']!r}"))
        if "dai" in t:
            _text(errs, f"{w}.dai", t["dai"])
            if t["what"] != "menseki":
                errs.append((f"{w}.dai", "大分類の行は延床面積(what menseki)だけ(大分類ごとの棟数の合計は原典に無い。§12.5)"))
            if t["dai"] in dais:
                errs.append((f"{w}.dai", f"大分類の行が重複: {t['dai']!r}"))
            dais.add(t["dai"])
        else:
            whole[t["what"]] += 1
            if "sa" in t or "issue" in t:
                errs.append((w, "sa・issue は大分類の行だけに書く(§12.5)"))
        if "sa" in t:
            if not _is_int(t["sa"]) or t["sa"] == 0:
                errs.append((f"{w}.sa", f"sa は0でない整数(差が無ければ書かない): {t['sa']!r}"))
            if not _is_int(t.get("issue")):
                errs.append((f"{w}.issue", "sa を書いた行には、段3の記録の no(整数)を issue に書く"))
        elif "issue" in t:
            errs.append((f"{w}.issue", "issue は sa を書いた行だけに書く"))
    for what in ("tou", "menseki"):
        if whole[what] != 1:
            errs.append(("$.totals", f"全体の{'棟数' if what == 'tou' else '延床面積の合計'}の行(dai の無い what {what})を1つ書く(いま {whole[what]})"))
    tables = d["tables"]
    if not isinstance(tables, list) or not tables:
        errs.append(("$.tables", "表を1つ以上、一覧で書く"))
        return errs
    keys = {}
    for ti, t in enumerate(tables):
        w = f"$.tables[{ti}]"
        if not _fields(errs, w, t, TABLE):
            continue
        if t["no"] != ti + 1:
            errs.append((f"{w}.no", f"表の no は1からの通し番号(ここは {ti + 1}): {t['no']!r}"))
        _text(errs, f"{w}.dai", t["dai"])
        kind = t["kind"]
        if kind not in KINDS:
            errs.append((f"{w}.kind", f"kind は {list(KINDS)} のどれか: {kind!r}"))
            continue
        _text(errs, f"{w}.chu", t["chu"], empty_ok=(kind == "suido"))
        if kind == "suido" and t["chu"] != "":
            errs.append((f"{w}.chu", f"上下水道の表(kind suido)の chu は \"\": {t['chu']!r}"))
        for k in ("page", "count", "count_page"):
            if not _is_int(t[k]) or t[k] < 1:
                errs.append((f"{w}.{k}", f"1以上の整数: {t[k]!r}"))
        if not isinstance(t["notes"], list):
            errs.append((f"{w}.notes", "一覧で書く(無ければ [])"))
        else:
            for i, x in enumerate(t["notes"]):
                _text(errs, f"{w}.notes[{i}]", x)
        rows = t["rows"]
        if not isinstance(rows, list) or not rows:
            errs.append((f"{w}.rows", "施設を1つ以上、一覧で書く"))
            continue
        for ri, r in enumerate(rows):
            rw = f"{w}.rows[{ri}]"
            if not _fields(errs, rw, r, ROW):
                continue
            k = r["key"]
            if not isinstance(k, str) or not KEY_RE.match(k):
                errs.append((f"{rw}.key", f"key は s と3桁以上の通し番号(^s[0-9]{{3,}}$): {k!r}"))
            elif k in keys:
                errs.append((f"{rw}.key", f"施設の key が表の id の中で重複: {k}(先に {keys[k]})"))
            else:
                keys[k] = rw
            for f in ("name", "chiku", "kozo"):
                _text(errs, f"{rw}.{f}", r[f])
            if not _is_int(r["tou"]) or r["tou"] < 0:
                errs.append((f"{rw}.tou", f"棟数は整数(数値型)で書く: {r['tou']!r}"))
            for f in ("taishin", "unei"):
                if kind == "suido":
                    if r[f] != "":
                        errs.append((f"{rw}.{f}", f"上下水道の表には列が無いので \"\": {r[f]!r}"))
                else:
                    _text(errs, f"{rw}.{f}", r[f])
            for f, fn in (("menseki", menseki_su), ("nendo", nendo_seireki)):
                try:
                    want = fn(r[f])
                except ValueError as e:
                    errs.append((f"{rw}.{f}", str(e)))
                    continue
                rf = "menseki_su" if f == "menseki" else "nendo_seireki"
                got = r[rf]
                if got is not None and not _is_int(got):
                    errs.append((f"{rw}.{rf}", f"読み替えの列は整数か null: {got!r}"))
                elif got != want:
                    errs.append((f"{rw}.{rf}", f"読み替えの列が原典の字の列から作った値と違う(§12.3。{want!r}。"
                                               f"python3 tools/shisetsu.py <id> --fill で作り直す): {got!r}"))
    return errs


# ---------------------------------------------------------------- 検算(§12.5)
def _index_row(src):
    if not SOURCES_INDEX.exists():
        return None
    lines = SOURCES_INDEX.read_text(encoding="utf-8").splitlines()
    head = lines[0].split("\t")
    for ln in lines[1:]:
        r = dict(zip(head, ln.split("\t")))
        if r.get("id") == src["id"] and r.get("file") == src["file"]:
            return r
    return None


def _issues(plan):
    f = ISSUES_DIR / f"{plan}.tsv"
    if not f.exists():
        return {}
    lines = f.read_text(encoding="utf-8").splitlines()
    head = lines[0].split("\t") if lines else []
    out = {}
    for ln in lines[1:]:
        r = dict(zip(head, ln.split("\t")))
        if r.get("no", "").isdigit():
            out[int(r["no"])] = r
    return out


def check_totals(d, index_row=None, issues=None):
    """検算(§12.5 の1〜6)。合わない箇所の一覧を返す(空なら合う)。index_row・issues は検査のときに差し替える。"""
    bad = []
    tat = [t for t in d["tables"] if t["kind"] == "tatemono"]
    rows = [r for t in tat for r in t["rows"]]
    whole = {t["what"]: t for t in d["totals"] if "dai" not in t}
    # 1. 建物の棟数の計
    n = sum(r["tou"] for r in rows)
    if n != whole["tou"]["value"]:
        bad.append(f"1. 建物の表の棟数の計 {n} が totals の棟数 {whole['tou']['value']} と違う")
    # 2. 建物の延床面積の計
    s = sum(r["menseki_su"] or 0 for r in rows)
    if s != menseki_su(whole["menseki"]["value"]):
        bad.append(f"2. 建物の表の延床面積の計 {s:,} が totals の延床面積の合計 {whole['menseki']['value']} と違う")
    # 3. 表ごとの施設の数
    for t in d["tables"]:
        if len(t["rows"]) != t["count"]:
            bad.append(f"3. 表{t['no']}({t['dai']} {t['chu']})の行の数 {len(t['rows'])} が count {t['count']} と違う")
    # 4. 大分類ごとの延床面積の差
    by_dai = defaultdict(int)
    for t in tat:
        by_dai[t["dai"]] += sum(r["menseki_su"] or 0 for r in t["rows"])
    named = {t["dai"]: t for t in d["totals"] if "dai" in t}
    for dai in by_dai:
        if dai not in named:
            bad.append(f"4. 大分類「{dai}」の行が totals に無い")
    if issues is None:
        issues = _issues(d["plan"])
    for dai, t in named.items():
        if dai not in by_dai:
            bad.append(f"4. totals の大分類「{dai}」の建物の表が無い")
            continue
        diff = by_dai[dai] - menseki_su(t["value"])
        sa = t.get("sa", 0)
        if diff != sa:
            bad.append(f"4. 大分類「{dai}」の計 {by_dai[dai]:,} − 原典 {t['value']} = {diff} が sa {sa} と違う")
        if sa:
            r = issues.get(t.get("issue"))
            if r is None:
                bad.append(f"4. 大分類「{dai}」の issue {t.get('issue')} が data/source_issues/{d['plan']}.tsv に無い")
            elif r.get("kind") != "原典の食い違い":
                bad.append(f"4. 大分類「{dai}」の issue {t['issue']} の kind が「原典の食い違い」でない: {r.get('kind')!r}")
            else:
                ev = r.get("evidence", "")
                for v in (f"{by_dai[dai]:,}", t["value"]):
                    if v not in ev:
                        bad.append(f"4. 大分類「{dai}」の issue {t['issue']} の evidence に「{v}」が無い")
    # 5. 原典の sha256
    ir = _index_row(d["source"]) if index_row is None else index_row
    if ir is None:
        bad.append(f"5. 台帳 data/sources_index.tsv に id {d['source']['id']}・file {d['source']['file']} の行が無い")
    elif ir.get("sha256") != d["source"]["sha256"]:
        bad.append(f"5. source の sha256 が台帳の行と違う(台帳 {ir.get('sha256')})")
    # 6. key の形と重なり
    keys = [r["key"] for t in d["tables"] for r in t["rows"]]
    for k, c in Counter(keys).items():
        if c > 1:
            bad.append(f"6. key {k} が {c} 回")
    for k in keys:
        if not KEY_RE.match(k):
            bad.append(f"6. key {k} の形が ^s[0-9]{{3,}}$ でない")
    return bad


# ---------------------------------------------------------------- 照合表(§12.4)
def table_label(t):
    return f"{t['dai']} {t['chu']}" if t["chu"] else t["dai"]


def title(d):
    plan = d["plan"]
    try:
        name = json.loads((PLANS_DIR / f"{plan}.json").read_text(encoding="utf-8"))["meta"]["name"]
    except (OSError, KeyError, ValueError):
        name = plan
    return f"{name}の施設概要の表({d['edition']['label']})"


def plan_view(d):
    """検収の道具が計画の JSON から使う部分(meta.name・sources)を作る。sources の pdf は sources_raw の中の原典の場所。"""
    r = _index_row(d["source"]) or {}
    return {"meta": {"name": title(d)},
            "sources": [{"id": d["id"], "label": r.get("label") or d["source"]["file"], "url": r.get("url", ""),
                         "pdf": f"{d['source']['id']}/{d['source']['file']}"}],
            "shisetsu": d}


def entries(d):
    """照合表の行を [(8列のタプル, 表, 施設)] で返す。表・施設の無い行は None。"""
    sid = d["id"]
    names = Counter(r["name"] for t in d["tables"] for r in t["rows"])
    seen = Counter()
    out = []
    for t in d["tables"]:
        bno, bl = str(t["no"]), table_label(t)
        for r in t["rows"]:
            name = r["name"]
            if names[name] > 1:
                seen[name] += 1
                name = f"{name}(同じ名前の{seen[name]}件目)"
            src = f"{sid} p.{t['page']}"
            for f, bt, head in KIND_COLS[t["kind"]]:
                out.append(((TAB, bno, bt, bl, f"{name}/{head}", str(r[f]), UNIT.get(f, ""), src), t, r))
        out.append(((TAB, bno, "shisetsu.count", bl, "施設の数", str(t["count"]), "", f"{sid} p.{t['count_page']}"), t, None))
    for x in d["totals"]:
        label = f"{x['label']}の延床面積" if "dai" in x else x["label"]
        out.append(((TAB, "0", "shisetsu.total", TOTAL_BLOCK, label, str(x["value"]), UNIT[x["what"]],
                     f"{sid} p.{x['page']}"), None, None))
    return out


def context(d, row):
    """確認ページ(audit_review.py)に添える、表のデータでのその行の施設。照合表と台帳は変えない。"""
    bt = row["btype"]
    lines = []
    if bt == "shisetsu.count":
        t = next(t for t in d["tables"] if str(t["no"]) == row["block_no"])
        lines = [["数えるもの", COUNT_DEF[bt]], ["表", table_label(t)], ["表のページ", t["page"]],
                 ["表の施設", "、".join(r["name"] for r in t["rows"])]]
    elif bt == "shisetsu.total":
        lines = [["説明", "検算の目標。原典の合計の値(§12.5)。施設概要の表の計と比べる"]]
    else:
        hit = [(t, r) for x, t, r in entries(d) if x[2] == bt and x[3] == row["block_label"] and x[4] == row["label"]]
        if len(hit) == 1:
            t, r = hit[0]
            lines = [["表", table_label(t)], ["施設名", r["name"]], ["地区", r["chiku"]], ["棟数", r["tou"]],
                     ["延床面積", r["menseki"]], ["代表棟の建築年度", r["nendo"]], ["代表棟の構造", r["kozo"]],
                     ["代表棟の耐震補強", r["taishin"]], ["運営方法", r["unei"]]]
    return {"kind": KIND.get(bt, bt), "tab": "施設概要の表", "head": "施設概要の表のデータ",
            "lines": [[k, str(v)] for k, v in lines if v not in (None, "")]}


def write_audit(pid, d=None):
    d = load(pid) if d is None else d
    errs = validate(d, path(pid))
    if errs:
        raise Stop("検証で誤りがある(validate_schema.py で確かめる):\n  " + "\n  ".join(f"{w}: {m}" for w, m in errs[:20]))
    bad = check_totals(d)
    if bad:
        raise Stop(f"検算が合わない({len(bad)} 件。照合表は書いていない):\n  " + "\n  ".join(bad))
    rows = [r for r, _, _ in entries(d)]
    AUDIT.mkdir(parents=True, exist_ok=True)
    tsv = AUDIT / f"{pid}.tsv"
    tsv.write_text(AUDIT_HEAD + "\n" + "".join("\t".join(r) + "\n" for r in rows), encoding="utf-8")
    return tsv, len(rows)


# ---------------------------------------------------------------- 段1(DESIGN.md ADR-7 補遺 L「施設概要の表の段1の規則」)
WORD_RE = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>')
HEAD_WORDS = ["施設名", "地区", "棟数", "延床面積", "建築年度", "構造", "耐震補強"]   # 列の見出しの語(左から)
HEAD_OF = {"chiku": "地区", "tou": "棟数", "menseki": "延床面積", "nendo": "建築年度", "kozo": "構造", "taishin": "耐震補強"}
SEQ_COLS = {"tatemono": ["chiku", "tou", "menseki", "nendo", "kozo", "taishin"],
            "suido": ["chiku", "tou", "menseki", "nendo", "kozo"]}
FIELD_OF = {bt: f for f, bt, _ in COLS}
HEAD_TO_FIELD = {head: f for f, _, head in COLS}
COUNT_RE = re.compile(r"施設は\s*(\d+)\s*施設")
CHU_HEAD = re.compile(r"^[①-⑳](.+)$")
DAI_HEAD = re.compile(r"^（[0-9０-９]+）(.+)$")


def page_words(bbox_html):
    """pdftotext -bbox の1ページ分 → [(x0, y0, x1, y1, 語)](文字データの順)。"""
    return [(float(a), float(b), float(c), float(d), html.unescape(w)) for a, b, c, d, w in WORD_RE.findall(bbox_html)]


def column_ranges(words, y):
    """高さ y の行の上にある表の列の見出しから、列ごとの横の範囲 {見出しの語: (左, 右)} を作る。

    見出しの語(施設名・地区・棟数・延床面積・建築年度・構造・耐震補強)ごとに、y より上で最も下にあるものを取る
    (1ページに表が2つあるときは、その行の表の見出し)。見出しの語の中心を左から並べ、隣り合う中心の中点を列の境目とする。
    施設名は地区の列の左の境目を決めるためだけに使う。最も右の列の右の境目は、その列の中心から左の境目までと同じ幅だけ右。
    見出しの語がそろわなければ None。"""
    found = {}
    for h in HEAD_WORDS:
        cands = [w for w in words if w[4] == h and w[3] <= y]
        if not cands:
            if h == "耐震補強":
                continue   # 上下水道の表には耐震補強の列が無い
            return None
        found[h] = max(cands, key=lambda w: w[3])
    cs = sorted(((w[0] + w[2]) / 2, h) for h, w in found.items())
    if [h for _, h in cs] != [h for h in HEAD_WORDS if h in found]:
        return None   # 見出しの左右の順が原典の列の順と違う
    out = {}
    for i, (c, h) in enumerate(cs):
        if h == "施設名":
            continue
        left = (cs[i - 1][0] + c) / 2
        right = (c + cs[i + 1][0]) / 2 if i + 1 < len(cs) else c + (c - left)
        out[h] = (left, right)
    return out


def _parse_label(label):
    """照合表の項目名 → (施設の部分(「(同じ名前のN件目)」を含む), 原典の施設名, 列の見出し)。"""
    item, _, head = label.rpartition("/")
    return item, SAME_NAME.sub("", item), head


def _groups(rows):
    """施設概要の表の施設の列の行を施設ごとにまとめる。[(表の block_label, 施設の部分, 出典, {項目: 行})](照合表の順)。"""
    order, g = [], {}
    for r in rows:
        if not r["btype"].startswith("shisetsu.") or r["btype"] in ("shisetsu.count", "shisetsu.total"):
            continue
        item, _, head = _parse_label(r["label"])
        k = (r["block_label"], item, r["source"])
        if k not in g:
            g[k] = defaultdict(list)
            order.append(k)
        g[k][HEAD_TO_FIELD.get(head, head)].append(r)
    return [(k[0], k[1], k[2], g[k]) for k in order]


def _seq_find(lines, seq):
    """行の並び seq が、行の終わりに現れる行の番号。ページの中に1回だけ現れるときだけ。(番号, 理由)"""
    text = "\n".join(lines)
    n = text.count(seq)
    if n != 1:
        return None, ("行の並びが出典ページに無い" if n == 0 else f"行の並びが出典ページに {n} 回ある")
    hits = [i for i, ln in enumerate(lines) if ln.rstrip().endswith(seq)]
    if len(hits) != 1:
        return None, "行の並びが行の終わりにない"
    return hits[0], ""


def stage1(rows, srcs, page_list, text_only=False):
    """照合表の施設概要の表の行(btype が shisetsu.)の段1の結果。{id(行): {"pass", "why", "h2", "seq"}}。
    srcs は出典id → 原典(audit_autocheck.Source。raw(pdfp) で -raw の文字、bbox(pdfp) で -bbox の文字を返す)。
    text_only は、列の位置を照らさない(原典の PDF が無いときの --check-text。台帳には使わない)。"""
    out = {}
    sz = [r for r in rows if r["btype"].startswith("shisetsu.")]
    for r in sz:
        out[id(r)] = {"pass": False, "why": "", "h2": False, "seq": False}

    def page_ok(r):
        sid = r["source"].split(" ", 1)[0]
        pages = page_list(r["source"])
        src = srcs.get(sid)
        if src is None or not pages or not all(src.has_page(p) for p in pages):
            out[id(r)].update(why="出典の PDF やページがない", h2=True)
            return None
        if len(pages) != 1:
            out[id(r)]["why"] = "出典が1ページでない"
            return None
        return src, pages[0]

    # 施設の列の行: 行の並びと列の位置
    prev_line = {}   # (block_label, 出典) → 前の施設の行の番号
    for bl, item, source, cols in _groups(sz):
        name = SAME_NAME.sub("", item)
        members = [r for lst in cols.values() for r in lst]
        got = [page_ok(r) for r in members]
        if any(g is None for g in got):
            for r, g in zip(members, got):
                if g is not None:
                    out[id(r)]["why"] = "同じ施設の行の出典の PDF やページがない"
            prev_line.pop((bl, source), None)
            continue
        src, p = got[0]

        def fail(why):
            for r in members:
                if not out[id(r)]["why"]:
                    out[id(r)]["why"] = why

        kind = "tatemono" if "taishin" in cols else "suido"
        seqf = SEQ_COLS[kind]
        if any(len(cols.get(f, [])) != 1 for f in seqf) or set(cols) - set(seqf) - {"unei"}:
            fail("施設の列の行がそろっていない(同じ施設の同じ列の行が2つある、または無い)")
            prev_line.pop((bl, source), None)
            continue
        seq = " ".join(cols[f][0]["value"] for f in seqf)
        lines = src.raw(p).split("\n")
        li, why = _seq_find(lines, seq)
        if li is None:
            fail(why)
            prev_line.pop((bl, source), None)
            continue
        pre = _squash(lines[li].rstrip()[:-len(seq)])
        ok_name = True
        if pre:
            if not pre.endswith(_squash(name)):
                ok_name = False
        else:
            start = prev_line.get((bl, source))
            if start is None or start >= li:
                heads = [i for i in range(li) if "■施設概要■" in lines[i]]
                start = heads[-1] if heads else None
            if start is None or _squash("".join(lines[start + 1:li])) != _squash(name):
                ok_name = False
        prev_line[(bl, source)] = li
        if not ok_name:
            fail("行の並びより前の字が施設名と合わない")
            continue
        for f in seqf:
            out[id(cols[f][0])]["seq"] = True
        if "unei" in cols:
            for r in cols["unei"]:
                out[id(r)]["why"] = "運営方法(段1では一致としない。段2に回す)"
        if text_only:
            for f in seqf:
                out[id(cols[f][0])]["why"] = "列の位置は照らしていない(原典の文字だけ)"
            continue
        # 列の位置(pdftotext -bbox)
        words = page_words(src.bbox(p))
        toks = seq.split(" ")
        n = len(toks)
        hits = [i for i in range(len(words) - n + 1) if [w[4] for w in words[i:i + n]] == toks]
        if len(hits) != 1:
            fail("座標つきの文字データで行の並びが1回だけ現れない")
            continue
        ws = words[hits[0]:hits[0] + n]
        top, bot = min(w[1] for w in ws), max(w[3] for w in ws)
        if any(not (w[1] < bot and w[3] > top) or (w[1] + w[3]) / 2 > bot or (w[1] + w[3]) / 2 < top for w in ws) \
                or max((w[1] + w[3]) / 2 for w in ws) - min((w[1] + w[3]) / 2 for w in ws) > (bot - top):
            fail("座標つきの文字データで行の並びの語が同じ高さにない")
            continue
        rng_ = column_ranges(words, top)
        if rng_ is None or (kind == "tatemono") != ("耐震補強" in rng_):
            fail("この表の列の見出しの語がそろわない")
            continue
        for f, w in zip(seqf, ws):
            lo, hi = rng_[HEAD_OF[f]]
            c = (w[0] + w[2]) / 2
            ok = lo <= c < hi
            out[id(cols[f][0])].update(**{"pass": ok}, why="" if ok else f"値の語が「{HEAD_OF[f]}」の列の範囲にない")
    # 表ごとの施設の数・検算の目標
    for r in sz:
        bt = r["btype"]
        if bt == "shisetsu.total":
            g = page_ok(r)
            if g is not None:
                out[id(r)]["why"] = "検算の目標(段1では一致としない。段2に回す)"
            continue
        if bt != "shisetsu.count":
            continue
        g = page_ok(r)
        if g is None:
            continue
        src, p = g
        lines = src.raw(p).split("\n")
        # block_label は「<dai> <chu>」(chu が空なら dai だけ)。中分類の見出し、上下水道は大分類の見出しのあとを見る
        if " " in r["block_label"]:
            dai, chu = r["block_label"].split(" ", 1)
            hi = [i for i, ln in enumerate(lines) if (m := CHU_HEAD.match(ln.strip())) and _squash(m.group(1)) == _squash(chu)]
        else:
            dai = r["block_label"]
            hi = [i for i, ln in enumerate(lines) if (m := DAI_HEAD.match(ln.strip())) and _squash(m.group(1)) == _squash(dai)]
        if len(hi) != 1:
            out[id(r)]["why"] = "出典ページに表の見出しが1回だけ現れない"
            continue
        m = COUNT_RE.search("\n".join(lines[hi[0] + 1:]))
        if not m:
            out[id(r)]["why"] = "見出しのあとに「施設は N 施設」が無い"
            continue
        ok = m.group(1) == r["value"]
        out[id(r)].update(**{"pass": ok, "seq": ok}, why="" if ok else f"本文の施設の数が違う(原典 {m.group(1)})")
        if text_only and ok:
            out[id(r)]["pass"] = False
            out[id(r)]["why"] = "原典の文字だけで照らした"
    return out


# ---------------------------------------------------------------- 誤りを入れた写し(audit_autocheck.py --selftest)
def _neighbors(r, rows):
    """同じ表(block_label・出典)の、表の中で隣の施設の、同じ列の行。"""
    item = _parse_label(r["label"])[0]
    gs = [g for g in _groups(rows) if g[0] == r["block_label"]]
    idx = next((i for i, g in enumerate(gs) if g[1] == item), None)
    if idx is None:
        return []
    f = FIELD_OF[r["btype"]]
    out = []
    for j in (idx - 1, idx + 1):
        if 0 <= j < len(gs) and gs[j][3].get(f):
            out.append(gs[j][3][f][0])
    return out


def _page_shift(r, rng, c):
    m = re.search(r"p\.(\d+)$", r["source"])
    if m:
        n2 = int(m.group(1)) + (1 if rng.random() < .5 else -1)
        if n2 >= 1:
            c["T7出典ページずれ"] = ("source", r["source"][:m.start(1)] + str(n2))


def _name_swap(r, rows, rng, c):
    nb = [x for x in _neighbors(r, rows) if x["value"] != r["value"]]
    if nb:
        c["T13隣の行との施設名の入れ替え"] = ("swap", rng.choice(nb))


def _fmt(n, like):
    return f"{n:,}" if "," in like or n >= 1000 and like != str(n) else str(n)


def mutants(r, rows, srcs, rng):
    """施設概要の表の行の誤りを入れた写し。(行, 種類, (kind, 値)) を返す。kind は value・source・swap。"""
    bt, v = r["btype"], r["value"]
    c = {}
    if bt in ("shisetsu.count", "shisetsu.total"):
        if v.replace(",", "").isdigit():
            n = int(v.replace(",", ""))
            c["T1打ち間違い"] = ("value", _fmt(n + (1 if rng.random() < .5 or n == 0 else -1), v))
        _page_shift(r, rng, c)
    elif bt in NUM_BTYPES and v != "-":
        n = int(v.replace(",", ""))
        s = str(n)
        i = rng.randrange(len(s))
        ns = s[:i] + rng.choice([x for x in "0123456789" if x != s[i]]) + s[i + 1:]
        if not (len(ns) > 1 and ns[0] == "0"):
            c["T1打ち間違い"] = ("value", _fmt(int(ns), v))
        pr = [k for k in range(len(s) - 1) if s[k] != s[k + 1]]
        if pr:
            k = rng.choice(pr)
            ns = s[:k] + s[k + 1] + s[k] + s[k + 2:]
            if ns[0] != "0":
                c["T2桁の入れ替え"] = ("value", _fmt(int(ns), v))
        if n:
            if rng.random() < .5 or n % 10:
                c["T3桁ずれ"] = ("value", _fmt(n * 10, v))
            else:
                c["T3桁ずれ"] = ("value", _fmt(n // 10, v))
        nb = [x["value"] for x in _neighbors(r, rows) if x["value"] != v]
        if nb:
            c["T5隣の行の値"] = ("value", rng.choice(nb))
        item = _parse_label(r["label"])[0]
        other = "shisetsu.menseki" if bt == "shisetsu.tou" else "shisetsu.tou"
        sib = [x["value"] for x in rows if x["btype"] == other and x["block_label"] == r["block_label"]
               and _parse_label(x["label"])[0] == item and x["value"] not in (v, "-")]
        if sib:
            c["T4隣の列の値"] = ("value", rng.choice(sib))
        sid = r["source"].split(" ", 1)[0]
        src = srcs.get(sid)
        m = re.search(r"p\.(\d+)$", r["source"])
        if src is not None and m:
            toks = [t for t in re.findall(r"\d[\d,]*", src.raw(int(m.group(1)))) if t != v and len(t.replace(",", "")) >= 2]
            if toks:
                c["T6同じページの別の数字"] = ("value", rng.choice(toks))
        _page_shift(r, rng, c)
        same = [x for x in rows if x is not r and x["btype"] == bt and x["block_label"] == r["block_label"]
                and x["value"] != v]
        if same:
            c["T9項目名の入れ替え"] = ("swap", rng.choice(same))
        _name_swap(r, rows, rng, c)
    else:
        yield from text_mutants(r, rows, rng)
        return
    for t, cc in c.items():
        yield r, t, cc


def text_mutants(r, rows, rng):
    """文字の行(地区・建築年度・構造・耐震補強・運営方法、延床面積の「-」)の誤りを入れた写し。"""
    v = r["value"]
    c = {}
    nb = [x["value"] for x in _neighbors(r, rows) if x["value"] != v]
    if nb:
        c["T10隣の行の値"] = ("value", rng.choice(nb))
    same = sorted({x["value"] for x in rows if x["btype"] == r["btype"] and x["value"] != v})
    if same:
        c["T14同じ列の別の字"] = ("value", rng.choice(same))
    if v:
        i = rng.randrange(len(v))
        pool = sorted({ch for x in rows if x["btype"] == r["btype"] for ch in x["value"]} - {v[i]})
        if pool:
            c["T12一文字の置き換え"] = ("value", v[:i] + rng.choice(pool) + v[i + 1:])
    _page_shift(r, rng, c)
    _name_swap(r, rows, rng, c)
    for t, cc in c.items():
        yield r, t, cc


# ---------------------------------------------------------------- 起票の補助(下書き)
DIST = ["日野", "東桜谷", "西桜谷", "西大路", "鎌掛", "南比都佐", "必佐"]   # 原典 p.53「地区」の区分
OPS = ["直営", "指定管理", "貸付"]                                          # 原典 p.53「運営方法」(遊休施設は上下水道だけ)
ROW_RE = re.compile(r"^(.*?)\s*(" + "|".join(DIST) + r") (\d+) (\d{1,3}(?:,\d{3})*|-) ([SHR]\d+|\d{4}|-) (\S+造|-)(?: (\S+))?$")


def _pages(base, plan, stem):
    return (base / plan / f"{stem}.txt").read_text(encoding="utf-8").split("\f")


def draft(pid, plan, file):
    """原典の文字から下書きを作る。読み違いを見つけたら Stop。人が原典の字を確かめる前提の補助で、検収の代わりにはならない。"""
    stem = Path(file).stem
    raw, dflt = _pages(TEXT_RAW, plan, stem), _pages(TEXT, plan, stem)
    tables, problems = [], []
    dai = chu = None
    for pi, page in enumerate(raw):
        lines = page.split("\n")
        heads = [i for i, ln in enumerate(lines) if "■施設概要■" in ln]
        bounds = heads + [len(lines)]
        # 見出しは ■施設概要■ の前まで
        for k, h in enumerate(heads):
            for ln in lines[(bounds[k - 1] if k else 0):h]:
                s = ln.strip()
                if (m := DAI_HEAD.match(s)):
                    dai, chu = m.group(1), None
                elif (m := CHU_HEAD.match(s)):
                    chu = m.group(1)
            seg = lines[h + 1:bounds[k + 1]]
            rows, last = [], h
            for j, ln in enumerate(seg):
                m = ROW_RE.match(ln.strip())
                if not m:
                    continue
                pre = m.group(1).strip()
                idx = h + 1 + j
                if pre:
                    name = pre.split(" ")[-1]
                else:
                    name = _squash("".join(lines[last + 1:idx]))
                last = idx
                rows.append({"name": name, "chiku": m.group(2), "tou": int(m.group(3)), "menseki": m.group(4),
                             "nendo": m.group(5), "kozo": m.group(6), "taishin": m.group(7) or "", "unei": ""})
            if not rows:
                problems.append(f"PDF p.{pi + 1}: ■施設概要■ の下に施設の行が無い")
                continue
            kinds = {"tatemono" if r["taishin"] else "suido" for r in rows}
            if len(kinds) != 1:
                problems.append(f"PDF p.{pi + 1}: 耐震補強の列のある行と無い行がまざる")
                continue
            kind = kinds.pop()
            notes = [ln.strip() for ln in seg if ln.strip().startswith("※")]
            # 施設の数: 表のあとで最初の「施設は N 施設」(上水道は次のページの本文)
            cnt = None
            for q in range(pi, len(raw)):
                qs = raw[q].split("\n")
                lo = h + 1 if q == pi else 0
                hi = bounds[k + 1] if q == pi else len(qs)
                if q > pi and any("■施設概要■" in x for x in qs):
                    hi = min(i for i, x in enumerate(qs) if "■施設概要■" in x)
                m = COUNT_RE.search("\n".join(qs[lo:hi]))
                if m:
                    cnt = (int(m.group(1)), q + 1)
                    break
                if q > pi + 1:
                    break
            if cnt is None:
                problems.append(f"PDF p.{pi + 1}: 施設の数の本文が見つからない")
                cnt = (0, pi + 1)
            tables.append({"no": len(tables) + 1, "dai": dai, "chu": (chu or "") if kind == "tatemono" else "",
                           "kind": kind, "page": pi + 1, "count": cnt[0], "count_page": cnt[1], "notes": notes,
                           "rows": rows, "_seg": (pi, k)})
    # 施設名の確かめと運営方法(既定の取り出し方の文字で、運営方法の字と施設名の並びの組を読む)
    for t in tables:
        pi, k = t["_seg"]
        dseg = dflt[pi].split("■施設概要■")[k + 1].split("■現況と維持管理方針■")[0]
        dsq = _squash(dseg)
        for r in t["rows"]:
            if _squash(r["name"]) not in dsq:
                problems.append(f"PDF p.{pi + 1}: 施設名「{r['name']}」が既定の取り出し方の文字に無い")
        if t["kind"] != "tatemono":
            continue
        after = dseg.split("運営方法", 1)
        if len(after) != 2:
            problems.append(f"PDF p.{pi + 1}: 運営方法の欄が見つからない")
            continue
        groups, cur, loose = [], None, []
        for ln in [x.strip() for x in after[1].split("\n") if x.strip()]:
            if ln in OPS:
                cur = [ln, []]
                groups.append(cur)
            elif cur is not None:
                cur[1] += [x for x in ln.split("、") if x]
            else:
                loose += [x for x in ln.split("、") if x]
        if loose:
            if len(groups) == 1 and not groups[0][1]:
                groups[0][1] = loose   # 施設名の並びが運営方法の字より前に取り出されたページ(1組だけのとき)
            else:
                problems.append(f"PDF p.{pi + 1}: 運営方法の組が決まらない")
                continue
        mp = {}
        for op, names in groups:
            for nm in names:
                mp.setdefault(_squash(nm), []).append(op)
        for r in t["rows"]:
            ops = mp.get(_squash(r["name"]), [])
            if len(ops) != 1:
                problems.append(f"PDF p.{pi + 1}: 「{r['name']}」の運営方法が1つに決まらない({ops})")
            else:
                r["unei"] = ops[0]
        listed = {n for n in mp}
        if listed != {_squash(r["name"]) for r in t["rows"]}:
            problems.append(f"PDF p.{pi + 1}: 運営方法の施設名の並びが表の施設名と合わない")
        # -raw でも、運営方法の字の順と施設名の並びの順が同じか
        rseg = raw[pi].split("■施設概要■")[k + 1]
        rops = [x.strip() for x in rseg.split("\n") if x.strip() in OPS]
        if rops != [g[0] for g in groups]:
            problems.append(f"PDF p.{pi + 1}: 運営方法の字の順が2つの取り出し方で違う({rops} / {[g[0] for g in groups]})")
        elif len(groups) > 1:
            rl = rseg.split("\n")
            first = min(i for i, x in enumerate(rl) if x.strip() in OPS)
            body = _squash("\n".join(rl[first:]))   # 運営方法の最初の字より後(表の行の施設名を数えない)
            pos = [body.find(_squash("、".join(g[1]))) for g in groups]
            if min(pos) < 0 or pos != sorted(pos):
                problems.append(f"PDF p.{pi + 1}: -raw の文字で、施設名の並びの順が運営方法の字の順と違う")
    if problems:
        raise Stop(f"下書きで決まらない箇所が {len(problems)} 件:\n  " + "\n  ".join(problems))
    n = 0
    for t in tables:
        del t["_seg"]
        for r in t["rows"]:
            n += 1
            r["key"] = f"s{n:03d}"
    ir = _index_row({"id": plan, "file": file}) or {}
    ym = pid.rsplit("-", 1)[-1]
    d = {"id": pid, "plan": plan, "edition": {"label": "", "ym": ym},
         "source": {"id": plan, "file": file, "sha256": ir.get("sha256", "")}, "totals": [],
         "tables": [{**{k: t[k] for k in TABLE if k != "rows"},
                     "rows": [{k: r.get(k) for k in ROW} for r in t["rows"]]} for t in tables]}
    return fill(d)


# ---------------------------------------------------------------- 原典の文字での確かめ(原典の PDF が無いとき)
class TextSource:
    """原典の文字 data/text_raw の1ページを、段1の raw(pdfp) として返す(-raw で1ページずつ取り出した文字の代わり)。"""

    def __init__(self, plan, file):
        self.pages = _pages(TEXT_RAW, plan, Path(file).stem)

    def has_page(self, p):
        return 1 <= p <= len(self.pages)

    def raw(self, p):
        return self.pages[p - 1]

    def bbox(self, p):
        raise Stop("原典の文字には座標が無い")


def _page_list(source):
    m = re.search(r"p\.(\d+)(?:-(\d+))?$", source or "")
    if not m:
        return []
    a = int(m.group(1))
    b = int(m.group(2) or a)
    return list(range(a, b + 1))


def read_audit_rows(pid):
    lines = (AUDIT / f"{pid}.tsv").read_text(encoding="utf-8").splitlines()
    head = lines[0].split("\t")
    return [dict(zip(head, ln.split("\t"))) for ln in lines[1:]]


def cmd_check_text(pid):
    d = load(pid)
    rows = read_audit_rows(pid)
    src = TextSource(d["source"]["id"], d["source"]["file"])
    res = stage1(rows, {d["id"]: src}, _page_list, text_only=True)
    by = defaultdict(lambda: [0, 0])
    for r in rows:
        by[r["btype"]][0] += 1
        by[r["btype"]][1] += res[id(r)]["seq"]
    print(f"{pid}: 照合表 {len(rows)} 行。原典の文字(data/text_raw)で、行の並び(施設の列)・本文の施設の数が合った行")
    for bt in sorted(by):
        print(f"  {KIND.get(bt, bt)}({bt}): {by[bt][1]} / {by[bt][0]}")
    miss = [r for r in rows if r["btype"] in {b for f, b, _ in COLS[:6]} | {"shisetsu.count"} and not res[id(r)]["seq"]]
    for r in miss[:30]:
        print(f"  合わない: {r['label']} = {r['value']}  ({res[id(r)]['why']})")
    print("  列の位置(pdftotext -bbox)は原典の PDF で照らす(audit_autocheck.py)。この結果は台帳に書かない")
    return 0 if not miss else 1


# ---------------------------------------------------------------- 道具の検査(--selftest)
def _fake_bbox(lines_words):
    """[(y, [(x, 語)])] → pdftotext -bbox の形の1ページ。語の幅は字数×10。"""
    out = ['<page width="600" height="800">']
    for y, ws in lines_words:
        for x, w in ws:
            out.append(f'<word xMin="{x:.1f}" yMin="{y:.1f}" xMax="{x + 10 * len(w):.1f}" yMax="{y + 10:.1f}">{html.escape(w)}</word>')
    out.append("</page>")
    return "\n".join(out)


class _FakeSource:
    def __init__(self, raw, bbox):
        self._raw, self._bbox = raw, bbox

    def has_page(self, p):
        return p in self._raw

    def raw(self, p):
        return self._raw[p]

    def bbox(self, p):
        return self._bbox[p]


def selftest():
    fails = []

    def ok(cond, what):
        if not cond:
            fails.append(what)

    # 読み替え
    ok(nendo_seireki("S62") == 1987 and nendo_seireki("H8") == 1996 and nendo_seireki("R3") == 2021, "年度の読み替え")
    ok(nendo_seireki("1860") == 1860 and nendo_seireki("-") is None, "4桁と「-」の読み替え")
    ok(menseki_su("1,278") == 1278 and menseki_su("898") == 898 and menseki_su("-") is None, "延床面積の読み替え")
    for bad, fn in (("H", nendo_seireki), ("平成8", nendo_seireki), ("1,27", menseki_su), ("1278.5", menseki_su)):
        try:
            fn(bad)
            fails.append(f"形に合わない字を読み替えた: {bad}")
        except ValueError:
            pass
    # 見本の検証・検算・照合表
    sample = SHISETSU_DIR / "_shisetsutest.json"
    d = json.loads(sample.read_text(encoding="utf-8"))
    ok(not validate(d, sample), f"見本の検証: {validate(d, sample)[:3]}")
    ok(not check_totals(d), f"見本の検算: {check_totals(d)}")
    ok(dumps(d) == sample.read_text(encoding="utf-8"), "見本の JSON が dumps の形と同じ")
    e = [x for x, _, _ in entries(d)]
    nt = sum(len(t["rows"]) * len(KIND_COLS[t["kind"]]) for t in d["tables"])
    ok(len(e) == nt + len(d["tables"]) + len(d["totals"]), "照合表の行の数")
    ok(len({(x[2], x[3], x[4], x[5], x[6], x[7]) for x in e}) == len(e), "照合表の行が重ならない")
    ok(any("(同じ名前の2件目)" in x[4] for x in e), "同じ名前の施設に「(同じ名前のN件目)」が付く")
    # 検証が止める誤り
    import copy
    for what, mut in (("読み替えの列の手書き", lambda x: x["tables"][0]["rows"][0].update(menseki_su=1)),
                      ("key の重なり", lambda x: x["tables"][0]["rows"][1].update(key=x["tables"][0]["rows"][0]["key"])),
                      ("上下水道の耐震補強", lambda x: [t for t in x["tables"] if t["kind"] == "suido"][0]["rows"][0].update(taishin="新基準")),
                      ("§12 に無い項目", lambda x: x["tables"][0]["rows"][0].update(sho="公民館")),
                      ("棟数の文字列", lambda x: x["tables"][0]["rows"][0].update(tou="3"))):
        x = copy.deepcopy(d)
        mut(x)
        ok(bool(validate(x, sample)), f"検証が止めない: {what}")
    # 検算が止める誤り
    for what, mut in (("棟数の計", lambda x: x["tables"][0]["rows"][0].update(tou=x["tables"][0]["rows"][0]["tou"] + 1)),
                      ("表の施設の数", lambda x: x["tables"][0].update(count=x["tables"][0]["count"] + 1)),
                      ("sha256", lambda x: x["source"].update(sha256="0" * 64)),
                      ("大分類の差", lambda x: [t for t in x["totals"] if "dai" in t][0].update(sa=5, issue=1))):
        x = fill(copy.deepcopy(d))
        mut(x)
        ok(bool(check_totals(x)), f"検算が止めない: {what}")
    # 大分類の差と段3の記録(メモリ上)
    x = copy.deepcopy(d)
    t0 = [t for t in x["tables"] if t["kind"] == "tatemono"][0]
    tot = next(t for t in x["totals"] if t.get("dai") == t0["dai"])
    s = sum(r["menseki_su"] or 0 for r in t0["rows"] if True) + sum(
        r["menseki_su"] or 0 for t in x["tables"] if t is not t0 and t["dai"] == t0["dai"] for r in t["rows"])
    tot.update(value=f"{s - 1:,}", sa=1, issue=99)
    whole = next(t for t in x["totals"] if t["what"] == "menseki" and "dai" not in t)
    iss = {99: {"kind": "原典の食い違い", "evidence": f"計 {s:,} と原典 {s - 1:,}"}}
    ok(not [b for b in check_totals(x, issues=iss) if b.startswith("4.")], "sa と段3の記録が合うときは通る")
    ok(bool(check_totals(x, issues={99: {"kind": "原典の誤字", "evidence": f"{s:,} {s - 1:,}"}})), "kind が違えば止まる")
    ok(bool(check_totals(x, issues={99: {"kind": "原典の食い違い", "evidence": f"{s:,}"}})), "evidence に原典の値が無ければ止まる")
    ok(bool(check_totals(x, issues={})), "issue が無ければ止まる")
    del whole
    # 段1(作った原典のページで)
    rows = [dict(zip(["tab", "block_no", "btype", "block_label", "label", "value", "unit", "source"], x)) for x in e]
    t1 = d["tables"][0]
    fac = t1["rows"]
    raw_lines = ["27", f"（１）{t1['dai']}", f"①{t1['chu']}", "■施設概要■"]
    for i, r in enumerate(fac):
        vals = " ".join(str(r[f]) for f in SEQ_COLS["tatemono"])
        raw_lines.append(("小分類 " if i == 0 else "") + f"{r['name']} {vals}")
    raw_lines += ["直営", "小分類 施設名 地区 棟数", f"●{t1['chu']}に分類される施設は {len(fac)} 施設です。"]
    xs = {"施設名": 100, "地区": 200, "棟数": 250, "延床面積": 290, "建築年度": 350, "構造": 410, "耐震補強": 460}
    bl = [(60, [(xs[h], h) for h in ["施設名", "地区", "棟数", "延床面積"]] + [(350, "代表棟の"), (410, "代表棟の"), (460, "代表棟の")]),
          (72, [(xs["建築年度"], "建築年度"), (xs["構造"], "構造"), (xs["耐震補強"], "耐震補強")])]
    for i, r in enumerate(fac):
        y = 100 + 20 * i
        bl.append((y, [(20, "小分類")] * (i == 0) + [(100, r["name"])] +
                   [(xs[HEAD_OF[f]], str(r[f])) for f in SEQ_COLS["tatemono"]]))
    page = t1["page"]
    src = _FakeSource({page: "\n".join(raw_lines), t1["count_page"]: "\n".join(raw_lines)},
                      {page: _fake_bbox(bl)})
    mine = [r for r in rows if r["block_no"] == str(t1["no"])]
    res = stage1(mine, {d["id"]: src}, _page_list)
    want_pass = [r for r in mine if r["btype"] not in ("shisetsu.unei",)]
    ok(all(res[id(r)]["pass"] for r in want_pass), "段1: 作ったページで施設の列と施設の数が一致: "
       + str([(r["label"], res[id(r)]["why"]) for r in want_pass if not res[id(r)]["pass"]][:3]))
    ok(all(not res[id(r)]["pass"] for r in mine if r["btype"] == "shisetsu.unei"), "段1: 運営方法は一致としない")
    # 誤りを入れた写しは素通りしない
    rng = random.Random(1)
    esc = tot_n = 0
    for r in mine:
        for _, typ, (kind, val) in mutants(r, mine, {d["id"]: src}, rng):
            rs = [dict(x) for x in mine]
            m = rs[mine.index(r)]
            only = {id(m)}
            if kind == "value":
                m["value"] = val
            elif kind == "source":
                m["source"] = val
            else:
                o = rs[mine.index(val)]
                m["label"], o["label"] = o["label"], m["label"]
                only.add(id(o))
            rr = stage1(rs, {d["id"]: src}, _page_list)
            tot_n += 1
            if all(rr[i]["pass"] for i in only) and r["btype"] != "shisetsu.unei":
                esc += 1
                fails.append(f"素通り: {r['label']} {typ} {val if kind != 'swap' else val['label']}")
    ok(tot_n > 0, "誤りを入れた写しを作った")
    # 上下水道の表の施設の数(大分類の見出しのあと)。出典ページに見出しが無ければ一致としない
    t3 = next(t for t in d["tables"] if t["kind"] == "suido")
    cr = [r for r in rows if r["btype"] == "shisetsu.count" and r["block_no"] == str(t3["no"])]
    for raw_page, want in ((f"（１）{t3['dai']}\n■施設概要■\n●施設は {t3['count']} 施設となっています。", True),
                           (f"●施設は {t3['count']} 施設となっています。", False),
                           (f"（１）{t3['dai']}\n●施設は {t3['count'] + 1} 施設となっています。", False)):
        rc = stage1(cr, {d["id"]: _FakeSource({t3["count_page"]: raw_page}, {})}, _page_list)
        ok(rc[id(cr[0])]["pass"] == want, f"段1: 上下水道の表の施設の数({want})")
    # 列の位置がずれた語は一致としない
    bl2 = [(y, [(x + (60 if w == str(fac[0]["tou"]) and y == 100 else 0), w) for x, w in ws]) for y, ws in bl]
    src2 = _FakeSource(src._raw, {page: _fake_bbox(bl2)})
    res2 = stage1(mine, {d["id"]: src2}, _page_list)
    r_tou = next(r for r in mine if r["btype"] == "shisetsu.tou" and r["label"].startswith(fac[0]["name"] + "/"))
    ok(not res2[id(r_tou)]["pass"], "段1: 列の範囲の外の語は一致としない")
    if fails:
        print(f"NG  道具の検査 {len(fails)} 件")
        for f in fails:
            print(f"  {f}")
        return 1
    print(f"OK  道具の検査(誤りを入れた写し {tot_n} 件で素通り {esc} 件)")
    return 0


def main(argv):
    if len(argv) >= 2 and argv[1] == "--selftest":
        return selftest()
    if len(argv) < 2 or argv[1].startswith("-"):
        print(__doc__)
        return 2
    pid = argv[1]
    try:
        if "--draft" in argv:
            i = argv.index("--draft")
            if len(argv) < i + 4:
                raise Stop("--draft の後に 計画の id・原典のファイル名・出力のファイル を指定する")
            plan, file, out = argv[i + 1:i + 4]
            d = draft(pid, plan, file)
            Path(out).write_text(dumps(d), encoding="utf-8")
            print(f"OK  {out}  (表 {len(d['tables'])}・施設 {sum(len(t['rows']) for t in d['tables'])})。"
                  "edition・totals は原典を見て書く")
            return 0
        if not path(pid).exists():
            raise Stop(f"施設概要の表がありません: {path(pid)}")
        if (PLANS_DIR / f"{pid}.json").exists() or (FUHYO_DIR / f"{pid}.json").exists():
            raise Stop(f"同じ id の計画か附表があります({pid})。施設概要の表の id と分ける")
        if "--fill" in argv:
            d = fill(load(pid))
            path(pid).write_text(dumps(d), encoding="utf-8")
            print(f"OK  {path(pid)}  読み替えの列を作り直した")
            return 0
        if "--check-text" in argv:
            return cmd_check_text(pid)
        tsv, n = write_audit(pid)
        print(f"OK  {tsv}  ({n} rows) — 検算は合った。原典PDFと突合してください")
        return 0
    except (Stop, ValueError, KeyError) as e:
        print(f"NG  {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
