#!/usr/bin/env python3
"""programs の事業に識別子 key を付ける(PLAN_SCHEMA programs「事業の識別子」)。標準ライブラリのみ。

使い方:
  python3 tools/program_keys.py <id>          # key の無い事業にだけ key を付けて data/plans/<id>.json に書く
  python3 tools/program_keys.py <id> --check  # 付けずに、key の無い事業の数を表示する(無ければ終了コード 0)

規則:
  - 形は e と3桁以上の通し番号(e001 …)。計画の中で一意
  - 付いている key は変えない。key の無い事業に、文書順(タブ→ブロック→節→施策→事業)で、
    その計画で使った最大の番号の次から振る(削った事業の番号は使い回さない)
  - 計画 JSON の書式(字下げ・改行)は変えない。事業の { の直後に "key" を差し込むだけにする
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KEY_RE = re.compile(r"^e([0-9]{3,})$")


class Stop(Exception):
    pass


# ---------------------------------------------------------------- JSON の文字の位置
def object_offsets(text):
    """JSON の文字列をたどり、すべてのオブジェクトについて {パス(タプル): '{' の位置} を返す。"""
    pos = {}
    i = 0
    n = len(text)

    def ws(i):
        while i < n and text[i] in " \t\r\n":
            i += 1
        return i

    def string(i):
        assert text[i] == '"'
        j = i + 1
        while True:
            c = text[j]
            if c == "\\":
                j += 2
                continue
            if c == '"':
                return json.loads(text[i:j + 1]), j + 1
            j += 1

    def value(i, path):
        i = ws(i)
        c = text[i]
        if c == "{":
            pos[path] = i
            i = ws(i + 1)
            if text[i] == "}":
                return i + 1
            while True:
                k, i = string(ws(i))
                i = ws(i)
                assert text[i] == ":"
                i = value(i + 1, path + (k,))
                i = ws(i)
                if text[i] == ",":
                    i += 1
                    continue
                assert text[i] == "}"
                return i + 1
        if c == "[":
            i = ws(i + 1)
            if text[i] == "]":
                return i + 1
            k = 0
            while True:
                i = value(i, path + (k,))
                i = ws(i)
                k += 1
                if text[i] == ",":
                    i += 1
                    continue
                assert text[i] == "]"
                return i + 1
        if c == '"':
            return string(i)[1]
        m = re.compile(r"-?\d+(\.\d+)?([eE][+-]?\d+)?|true|false|null").match(text, i)
        if not m:
            raise Stop(f"JSON を読めません(位置 {i})")
        return m.end()

    value(0, ())
    return pos


# ---------------------------------------------------------------- 事業の列挙
def program_item_paths(d):
    """programs の事業を文書順に [(パス, item)] で返す。"""
    out = []
    for ti, t in enumerate(d.get("tabs", [])):
        for bi, b in enumerate(t.get("blocks", [])):
            if b.get("type") != "programs":
                continue
            base = ("tabs", ti, "blocks", bi)
            for gi, g in enumerate(b.get("groups", []) or []):
                gp = base + ("groups", gi)
                for ii, it in enumerate(g.get("items", []) or []):
                    out.append((gp + ("items", ii), it))
                for si, sg in enumerate(g.get("subgroups", []) or []):
                    for ii, it in enumerate(sg.get("items", []) or []):
                        out.append((gp + ("subgroups", si, "items", ii), it))
    return out


def assign(pid, check=False):
    path = ROOT / "data" / "plans" / f"{pid}.json"
    if not path.exists():
        raise Stop(f"計画 JSON がありません: {path}")
    text = path.read_text(encoding="utf-8")
    d = json.loads(text)
    items = program_item_paths(d)
    used = []
    for _, it in items:
        k = it.get("key")
        if k is not None:
            m = KEY_RE.match(str(k))
            if not m:
                raise Stop(f"形の違う key があります: {k!r}(e と3桁以上の数字)")
            used.append(int(m.group(1)))
    if len(used) != len(set(used)):
        raise Stop("key が計画の中で重複しています(手で直してください)")
    missing = [(p, it) for p, it in items if "key" not in it]
    if check:
        print(f"{pid}: 事業 {len(items)} / key あり {len(used)} / key なし {len(missing)}")
        return 0 if not missing else 1
    if not missing:
        print(f"{pid}: 事業 {len(items)}。key の無い事業はありません")
        return 0
    nxt = max(used, default=0) + 1
    width = max(3, len(str(nxt + len(missing) - 1)))
    offs = object_offsets(text)
    inserts = []
    for p, _ in missing:
        o = offs[p]
        j = o + 1
        while text[j] in " \t\r\n":
            j += 1
        sep = text[o + 1:j]             # { の後ろの空白(改行と字下げ、または空白)
        key = f"e{nxt:0{width}d}"
        nxt += 1
        if "\n" in sep:
            ins = f'{sep}"key": "{key}",'        # 改行して字下げをそろえる
        else:
            ins = f'"key": "{key}", ' if sep == "" else f'{sep}"key": "{key}",'
        inserts.append((o + 1, ins, key))
    new = text
    for at, ins, _ in sorted(inserts, reverse=True):
        new = new[:at] + ins + new[at:]
    # 差し込んだ後も、key のほかは元と同じ内容であることを確かめる
    d2 = json.loads(new)
    got = {p: it.get("key") for p, it in program_item_paths(d2)}
    want = {p: k for (p, _), (_, _, k) in zip(missing, inserts)}
    if any(got[p] != k for p, k in want.items()):
        raise Stop("差し込んだ key の位置が合いません(書き込みを中止)")
    for p, it in program_item_paths(d2):
        if p in want:
            it.pop("key")
    if d2 != d:
        raise Stop("key のほかの内容が変わりました(書き込みを中止)")
    path.write_text(new, encoding="utf-8")
    print(f"OK  {path}  (事業 {len(items)} のうち {len(missing)} に key を付けた: "
          f"{inserts[0][2]}〜{inserts[-1][2]})")
    return 0


def main(argv):
    if len(argv) < 2 or argv[1].startswith("-"):
        print(__doc__)
        return 2
    try:
        return assign(argv[1], check="--check" in argv)
    except Stop as e:
        print(f"NG  {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
