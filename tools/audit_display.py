#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""audit_display.py — 原典との照合の件数を数える(PLAN_SCHEMA §11・DESIGN.md ADR-11)

render_plan.py(計画ページ)と build.py(ハブ)が共用する。数え方は
tools/audit_autocheck.py <id> --status と同じ(§11.2)。台帳 data/audit_log/<id>.tsv を読むだけで、
計画の JSON にも台帳にも書かない。tools/check_audit_display.py が --status との突き合わせと分岐の自己検査をする。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit_worksheet as aw  # noqa: E402  AUDIT_COLS / row_key / read_ledger を共用

FOCUS_H1 = "要人確認:H1"
FOCUS_H2 = "要人確認:H2 "
FOCUS_H4 = "要人確認:H4"
MISMATCH = " / 不一致:"
HOLD = " / 保留:"
NOT_YET = "まだ照合していない"
# §11.3 の理由の対応表。画面に出す順もこの順
REASON_MISMATCH = "人の確認で食い違いが見つかり、直している"
REASON_HOLD = "人が確かめたが、判断を保留している"
REASON_H1 = "AI 2回の照合の結果がそろわない、または確信が高くない"
H2_REASONS = {
    "出典が4ページ以上(集計した値)": "原典の複数のページにわたる値を、数えたり集計したりした値",
    "数値でない": "数値でない項目（名前・年度など）",
    "出典の PDF がない": "原典の PDF が無い資料から引いた値",
    "出典ページがない、または PDF の範囲にない": "原典のページを特定できない値",
    "出典の PDF やページがない": "原典の PDF またはページを特定できない値",
}
REASON_ORDER = [REASON_MISMATCH, REASON_HOLD, REASON_H1, *dict.fromkeys(H2_REASONS.values()), NOT_YET]
# すでに人が見た理由。画面で「人が確かめます」を添えない(§11.3 の表の列)
HUMAN_SEEN = {REASON_MISMATCH, REASON_HOLD}
TO_HUMAN = "（人が確かめます）"


def reason_text(r, n):
    """画面の理由の1行(件数の書式は呼び出し側が渡す)。人がまだ見ていない理由にだけ「人が確かめます」を添える。"""
    return f"{r}：{n}" + ("" if r in HUMAN_SEEN else TO_HUMAN)
MACHINE = {"auto:pdftext": "a", "auto:ai": "b"}


class AuditDisplayError(Exception):
    pass


def rows_from_renderer(audit_rows):
    """render_plan.py の audit_rows(タプル)を、照合表に書くのと同じ str(x) の形の行にして key を付ける。"""
    rows = []
    for t in audit_rows:
        r = dict(zip(aw.AUDIT_COLS, (str(x) for x in t)))
        r["key"] = aw.row_key(r)
        rows.append(r)
    return rows


def reason(note):
    """✓ でない行の理由(§11.3 の対応表)。表に無い note は止める。"""
    mis, hold = MISMATCH in note, HOLD in note
    if mis and hold:
        raise AuditDisplayError(f"「 / 不一致:」と「 / 保留:」の両方を含む note: {note!r}")
    if mis:
        return REASON_MISMATCH
    if hold:
        return REASON_HOLD
    if note.startswith(FOCUS_H1):
        return REASON_H1
    if note.startswith(FOCUS_H2):
        phrase = note[len(FOCUS_H2):].split(" / ")[0]
        if phrase in H2_REASONS:
            return H2_REASONS[phrase]
        raise AuditDisplayError(f"PLAN_SCHEMA §11.3 の表に無い H2 の文言: {phrase!r}")
    if note == "":
        return NOT_YET
    raise AuditDisplayError(f"PLAN_SCHEMA §11.3 の表に無い note: {note!r}")


def count(pid, rows, ledger=None):
    """照合表の行 rows(key 付き)と台帳から §11.2 の件数を数える。
    ledger を渡せばそれを使い、None なら data/audit_log/<id>.tsv を読む。
    id が "_" で始まり台帳が無いときは None(表示しない)。"""
    if ledger is None:
        if not aw.ledger_path(pid).exists():
            if pid.startswith("_"):
                return None
            raise AuditDisplayError(f"台帳がありません: {aw.ledger_path(pid)}")
        ledger = aw.read_ledger(pid)
    led = {L["key"]: L for L in ledger}
    seen = set()
    s = {"n": len(rows), "m": 0, "a": 0, "b": 0, "c": 0, "u": 0, "f": 0, "x": 0}
    reasons = dict.fromkeys(REASON_ORDER, 0)
    dates = []
    for r in rows:
        if r["key"] in seen:
            raise AuditDisplayError(f"照合表の突合キーが重複しています: {r['key']}")
        seen.add(r["key"])
        L = led.get(r["key"])
        if L is None:
            s["u"] += 1
            reasons[NOT_YET] += 1
            continue
        if L["date"]:
            dates.append(L["date"])
        ok = L["checked"] == "✓"
        rv = L["reviewer"]
        if ok:
            s["m"] += 1
            if rv.startswith("auto:"):
                if rv not in MACHINE:
                    raise AuditDisplayError(f"PLAN_SCHEMA §11.2 に無い機械の照合: reviewer={rv!r}")
                s[MACHINE[rv]] += 1
            else:
                s["c"] += 1
        else:
            s["u"] += 1
            reasons[reason(L["note"])] += 1
        if L["note"].startswith(FOCUS_H4):
            s["f"] += 1
            if ok and not rv.startswith("auto:"):
                s["x"] += 1
    s["reasons"] = [(k, v) for k, v in reasons.items() if v]
    s["last"] = max(dates) if dates else ""
    return s
