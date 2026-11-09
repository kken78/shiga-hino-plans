#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_audit_display.py — 原典との照合の表示の検査(PLAN_SCHEMA §11・DESIGN.md ADR-11)

使い方:
  python3 tools/check_audit_display.py               # 全計画の突き合わせ(原典 PDF は使わない。CI でも実行)
  python3 tools/check_audit_display.py --selftest    # 作った行(メモリ上)で audit_display.count の分岐を確かめる
  python3 tools/check_audit_display.py --markdown    # 全計画の集計の表(プルリクエストの本文用)

全計画の突き合わせ(id が "_" で始まる見本は除く):
  1. N・U・人(c)を tools/audit_autocheck.py <id> --status の出力と比べる(§11.2)
  2. F・X を、台帳の TSV をこの道具で読み直して §11.2 の定義で数えたものと比べる(audit_display を通さない)
  3. a+b+c = M、理由ごとの件数の合計 = U
  4. 計画ページ docs/plans/<id>.html の上部の1行と、ハブ docs/index.html の PLANS の shogo が同じ件数か
  5. 附表(data/fuhyo/。PLAN_SCHEMA §9)と施設概要の表(data/shisetsu/。PLAN_SCHEMA §12)は件数に数えない。
     ハブの PLANS に無く、計画ページ docs/plans/<id>.html も無いこと
照合表(build/audit/<id>.tsv)と docs/ は、render_plan.py と build.py で作り直してから検査する。
"""
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit_display as ad  # noqa: E402
import audit_worksheet as aw  # noqa: E402
from render_plan import NO_TARGET  # noqa: E402  照合表が0行の計画の文言

ROOT = Path(__file__).resolve().parent.parent
STATUS_RE = re.compile(r"^(\S+): (\d+) 行 / 未検収 (\d+) / 機械 (\d+) / 人 (\d+) / ")
SHORT = {ad.REASON_MISMATCH: "不一致", ad.REASON_HOLD: "保留", ad.REASON_H1: "H1", ad.NOT_YET: "未照合"}
SHORT.update({v: f"H2 {k}" for k, v in ad.H2_REASONS.items()})


def plan_ids():
    return [p.stem for p in sorted((ROOT / "data" / "plans").glob("*.json")) if not p.stem.startswith("_")]


def status_of(pid):
    out = subprocess.run([sys.executable, str(ROOT / "tools" / "audit_autocheck.py"), pid, "--status"],
                         capture_output=True, text=True, cwd=ROOT)
    m = STATUS_RE.match(out.stdout)
    if not m or m.group(1) != pid:
        raise RuntimeError(f"--status の出力を読めません: {out.stdout[:200]!r} {out.stderr[:200]!r}")
    return {"n": int(m.group(2)), "u": int(m.group(3)), "c": int(m.group(5))}


def h4_by_definition(pid, rows):
    """F・X を §11.2 の定義どおりに数える。台帳は audit_worksheet を通さず、TSV をそのまま読む。"""
    led = {}
    for line in aw.ledger_path(pid).read_text(encoding="utf-8").splitlines()[1:]:
        if line.startswith("#") or not line:
            continue
        f = line.split("\t")
        led[f[0]] = {"checked": f[9], "reviewer": f[10], "note": f[12]}
    F = X = 0
    for r in rows:
        L = led.get(r["key"])
        if L and L["note"].startswith("要人確認:H4"):
            F += 1
            if L["checked"] == "✓" and not L["reviewer"].startswith("auto:"):
                X += 1
    return F, X


def hub_counts():
    page = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    m = re.search(r"const PLANS = (\[.*?\n\]);", page, re.S)
    if not m:
        raise RuntimeError("docs/index.html に PLANS が見つかりません")
    return {p["id"]: p.get("shogo") for p in json.loads(m.group(1))}


def page_line(s):
    if s["n"] == 0:
        return f"原典との照合：{NO_TARGET}"
    return (f'原典との照合：<b><span class="num">{s["n"]:,}</span>件のうち '
            f'<span class="num">{s["m"]:,}</span>件が済み</b>')


def cmd_check():
    errors = []
    hub = hub_counts()
    for pid in plan_ids():
        rows = aw.read_audit(pid)
        s = ad.count(pid, rows)
        st = status_of(pid)
        for k, name in (("n", "N(行)"), ("u", "U(未検収)"), ("c", "c(人)")):
            if s[k] != st[k]:
                errors.append(f"{pid}: {name} が --status と違う(表示 {s[k]} / --status {st[k]})")
        F, X = h4_by_definition(pid, rows)
        if (s["f"], s["x"]) != (F, X):
            errors.append(f"{pid}: F・X が定義と違う(表示 {s['f']}・{s['x']} / 定義 {F}・{X})")
        if s["a"] + s["b"] + s["c"] != s["m"]:
            errors.append(f"{pid}: a+b+c が M と違う")
        if sum(v for _, v in s["reasons"]) != s["u"]:
            errors.append(f"{pid}: 理由の件数の合計が U と違う")
        html = (ROOT / "docs" / "plans" / f"{pid}.html").read_text(encoding="utf-8")
        if html.count(page_line(s)) != 1:
            errors.append(f"{pid}: 計画ページの上部の1行が台帳の件数と違う(render_plan.py で作り直す)")
        for r, v in s["reasons"]:      # 理由の行と「人が確かめます」の添え方(§11.3 の表)
            li = "<li>" + ad.reason_text(r, f'<span class="num">{v:,}</span>件') + "</li>"
            if html.count(li) != 1:
                errors.append(f"{pid}: 計画ページの理由の行が違う: {r}(render_plan.py で作り直す)")
        if pid in hub and hub[pid] != {"m": s["m"], "n": s["n"]}:
            errors.append(f"{pid}: ハブの件数 {hub[pid]} が台帳の件数と違う(build.py で作り直す)")
        print(f"{pid}: N {s['n']} / M {s['m']} (a {s['a']}・b {s['b']}・c {s['c']}) / U {s['u']} / H4 {s['x']}/{s['f']}")
    missing = [pid for pid in plan_ids() if pid not in hub]
    errors += [f"{pid}: ハブに計画がありません" for pid in missing]
    for sub in ("fuhyo", "shisetsu"):   # 計画ページとハブの照合の件数に数えないもの(§11・§12.4)
        for f in sorted((ROOT / "data" / sub).glob("*.json")):
            if f.stem in hub:
                errors.append(f"{f.stem}: data/{sub}/ の表がハブの PLANS にある(照合の件数に数えない)")
            if (ROOT / "docs" / "plans" / f"{f.stem}.html").exists():
                errors.append(f"{f.stem}: data/{sub}/ の表の計画ページがある(ページは作らない)")
    if errors:
        print(f"NG  原典との照合の表示 ({len(errors)} 件)", file=sys.stderr)
        for e in errors:
            print(f"  [ERROR] {e}", file=sys.stderr)
        return 1
    print(f"OK  原典との照合の表示 ({len(plan_ids())} 計画。--status・H4 の定義・計画ページ・ハブと一致)")
    return 0


def cmd_markdown():
    print("| 計画 id | 全行 N | 照合済み M | プログラム | AI(2回) | 人 | 未照合 U | 未照合の理由 | 人の抜き取り X/F |")
    print("|---|--:|--:|--:|--:|--:|--:|---|--:|")
    tot = dict.fromkeys("nmabcuxf", 0)
    for pid in plan_ids():
        s = ad.count(pid, aw.read_audit(pid))
        for k in tot:
            tot[k] += s[k]
        why = "、".join(f"{SHORT[r]} {v}" for r, v in s["reasons"]) or "—"
        hf = f"{s['x']}/{s['f']}" if s["f"] else "—"
        print(f"| {pid} | {s['n']:,} | {s['m']:,} | {s['a']:,} | {s['b']:,} | {s['c']:,} | {s['u']:,} | {why} | {hf} |")
    print(f"| 計 | {tot['n']:,} | {tot['m']:,} | {tot['a']:,} | {tot['b']:,} | {tot['c']:,} | {tot['u']:,} | | "
          f"{tot['x']}/{tot['f']} |")
    return 0


# ---------------------------------------------------------------- 自己検査(メモリ上の行だけを使う)
def _row(i):
    r = dict(zip(aw.AUDIT_COLS, ["t", str(i), "kpi", "b", f"項目{i}", str(i), "人", "honpen p.1"]))
    r["key"] = aw.row_key(r)
    return r


def _led(r, checked="", reviewer="", date="", note=""):
    return {"key": r["key"], "checked": checked, "reviewer": reviewer, "date": date, "note": note}


def cmd_selftest():
    fails = []

    def expect(name, ok):
        print(f"{'OK' if ok else 'NG'}  {name}")
        if not ok:
            fails.append(name)

    def stops(name, rows, ledger):
        try:
            ad.count("selftest", rows, ledger)
        except ad.AuditDisplayError as e:
            expect(f"{name}(止まる: {e})", True)
            return
        expect(f"{name}(止まらなかった)", False)

    def reasons(rows, ledger):
        return dict(ad.count("selftest", rows, ledger)["reasons"])

    R = [_row(i) for i in range(10)]
    # (1) 「 / 不一致:」は先頭の H1・H2 より優先
    led = [_led(R[0], note="要人確認:H1 A=一致/中 B=一致/中 / 不一致: 正しい値 12"),
           _led(R[1], note="要人確認:H2 数値でない / 不一致: 正しい値 13")]
    expect("(1) 「 / 不一致:」が先頭の H1・H2 より優先", reasons(R[:2], led) == {ad.REASON_MISMATCH: 2})
    # (2) 「 / 保留:」も同じく優先
    led = [_led(R[0], note="要人確認:H1 A=一致/中 B=一致/中 / 保留: 何に関する事業数か"),
           _led(R[1], note="要人確認:H2 出典が4ページ以上(集計した値) / 保留: 確かめる")]
    expect("(2) 「 / 保留:」が先頭の H1・H2 より優先", reasons(R[:2], led) == {ad.REASON_HOLD: 2})
    # (3) 両方を含む行で止まる
    stops("(3) 「 / 不一致:」と「 / 保留:」の両方を含む行", R[:1],
          [_led(R[0], note="要人確認:H1 x / 不一致: 1 / 保留: 2")])
    # (4) 先頭が H4 で、印の無い未照合の行で止まる
    stops("(4) 先頭が H4 で印の無い未照合の行", R[:1], [_led(R[0], note="要人確認:H4")])
    # H4 で「違う」「保留」とした行は優先の規則で決まり、F に数える(X には数えない)
    led = [_led(R[0], note="要人確認:H4 / 不一致: 正しい値 5"), _led(R[1], note="要人確認:H4 / 保留: 確かめる")]
    s = ad.count("selftest", R[:2], led)
    expect("(4b) H4 の「違う」「保留」は優先の規則で決まり、F=2・X=0",
           dict(s["reasons"]) == {ad.REASON_MISMATCH: 1, ad.REASON_HOLD: 1} and (s["f"], s["x"]) == (2, 0))
    # (5) 表に無い H2 の文言で止まる
    stops("(5) 表に無い H2 の文言", R[:1], [_led(R[0], note="要人確認:H2 新しい理由")])
    # H2 は「 / 」以降の書き足しを問わない
    led = [_led(R[0], note="要人確認:H2 出典が4ページ以上(集計した値) / コメント")]
    expect("(5b) H2 の「 / 」以降の書き足しは問わない",
           reasons(R[:1], led) == {ad.H2_REASONS["出典が4ページ以上(集計した値)"]: 1})
    # 表に無い note(先頭が何でもない)で止まる
    stops("(5c) 表に無い note", R[:1], [_led(R[0], note="メモ")])
    # (6) auto:pdftext・auto:ai 以外の auto: の reviewer で止まる
    stops("(6) 想定外の機械の照合 auto:ocr", R[:1], [_led(R[0], "✓", "auto:ocr", "2026-10-01")])
    # (7) 台帳に無い行は「まだ照合していない」で U に数える
    s = ad.count("selftest", R[:3], [_led(R[0], "✓", "auto:ai", "2026-10-01")])
    expect("(7) 台帳に無い行は「まだ照合していない」", dict(s["reasons"]) == {ad.NOT_YET: 2} and s["u"] == 2)
    # (8) 記録の最終日は、照合表に無い台帳の行の date を使わない
    extra = _row(99)
    led = [_led(R[0], "✓", "auto:ai", "2026-10-01"), _led(R[1], "✓", "kken78", "2026-10-05"),
           _led(extra, "✓", "kken78", "2026-12-31")]
    expect("(8) 照合表に無い台帳の行の date を使わない", ad.count("selftest", R[:2], led)["last"] == "2026-10-05")
    # 件数の内訳と F・X
    led = [_led(R[0], "✓", "auto:pdftext", "2026-10-01"),
           _led(R[1], "✓", "auto:ai", "2026-10-01", "要人確認:H4"),
           _led(R[2], "✓", "kken78", "2026-10-02", "要人確認:H4"),
           _led(R[3], "✓", "kken78", "2026-10-02"),
           _led(R[4], note="要人確認:H1 A=一致/中 B=一致/中"),
           _led(R[5], note=""),
           _led(R[6], "", "auto:ai", "2026-10-01", "要人確認:H1 A=判断できない/中 B=一致/中")]
    s = ad.count("selftest", R[:7], led)
    got = {k: s[k] for k in "nmabcufx"}
    expect(f"(9) 件数の内訳と F・X {got}",
           got == {"n": 7, "m": 4, "a": 1, "b": 1, "c": 2, "u": 3, "f": 2, "x": 1}
           and dict(s["reasons"]) == {ad.REASON_H1: 2, ad.NOT_YET: 1})
    # 「人が確かめます」は、人がまだ見ていない理由にだけ添える(§11.3 の表)
    expect("(9b) 不一致・保留には「人が確かめます」を添えず、H1・H2・まだ照合していないには添える",
           all(not ad.reason_text(r, "1件").endswith(ad.TO_HUMAN) for r in (ad.REASON_MISMATCH, ad.REASON_HOLD))
           and all(ad.reason_text(r, "1件").endswith(ad.TO_HUMAN)
                   for r in (ad.REASON_H1, ad.NOT_YET, *ad.H2_REASONS.values())))
    # 照合表が0行
    s = ad.count("selftest", [], [])
    expect("(10) 照合表が0行", s["n"] == 0 and s["reasons"] == [] and s["last"] == "")
    # 台帳ファイルが無いとき: "_" 始まりは None、それ以外は止まる
    expect("(11) 「_」で始まり台帳が無い見本は None", ad.count("_selftest-none", R[:1]) is None)
    try:
        ad.count("selftest-none", R[:1])
        expect("(12) 台帳が無い計画で止まる(止まらなかった)", False)
    except ad.AuditDisplayError:
        expect("(12) 台帳が無い計画で止まる", True)
    # 照合表の key の重複で止まる
    stops("(13) 照合表の key の重複", [R[0], R[0]], [_led(R[0], "✓", "auto:ai", "2026-10-01")])

    if fails:
        print(f"NG  自己検査 {len(fails)} 件が想定と違う", file=sys.stderr)
        return 1
    print("OK  自己検査(audit_display.count の分岐)")
    return 0


def main(argv):
    try:
        if "--selftest" in argv:
            return cmd_selftest()
        if "--markdown" in argv:
            return cmd_markdown()
        return cmd_check()
    except (ad.AuditDisplayError, aw.AuditError, RuntimeError) as e:
        print(f"NG  {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
