"""tokens.py — 構造トークンの値代入ユーティリティ(DESIGN.md ADR-6 / モデルA)。

templates/tokens.css(セレクタを持たない name→value マップ)を読み、shell.css /
hub.html 文字列内の同名トークン定義の「値」を、名前アンカーで代入する。
render_plan.py と build.py が共有する(単一機構=単一正典の思想)。
連結・前置・行スプライスはしない。
"""
import re

_DECL_RE = re.compile(r"--([\w-]+)\s*:\s*([^;]+?)\s*;")
_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{3,8}$")


def load_tokens(path):
    """tokens.css を {name: '#hex'} にして返す。

    全 --name:value; 宣言を拾って数え、値が HEX 形式でない宣言が1つでもあれば
    例外(移行A は色トークンのみ。「書いたのに HEX 限定で黙殺される」を禁止=
    fail-loud の一貫)。返すのは HEX 宣言の dict。
    """
    text = re.sub(r"/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.S)
    decls = _DECL_RE.findall(text)
    if not decls:
        raise ValueError(f"{path}: トークン宣言が1件も無い")
    names = [n for n, _ in decls]
    dup = sorted({n for n in names if names.count(n) > 1})
    if dup:
        raise ValueError(f"{path}: 同名トークンの重複定義(正典は1名1値): {dup}")
    bad = [f"--{n}:{v}" for n, v in decls if not _HEX_RE.match(v)]
    if bad:
        raise ValueError(
            f"{path}: HEX 形式でない宣言(移行A は色トークンのみ): {bad}")
    return dict(decls)


def apply_tokens(css, tokens, label):
    """css 内の各トークン定義の値を tokens の値へ代入して返す。

    各トークンは厳密1件マッチでなければ例外(0件 no-op 禁止・複数も禁止)。
    アンカーは '--name:' の直後(コロン直結)に #hex。コロンを名前直後に固定する
    ので --ink は --ink2 に、--line は --line2 に化けない(--ink2 は 'ink' の次が
    '2' で ':' でないため不一致)。
    """
    for name, value in tokens.items():
        pattern = re.compile(r"--" + re.escape(name) + r":#[0-9A-Fa-f]{3,8};")
        css, n = pattern.subn(f"--{name}:{value};", css)
        if n != 1:
            raise ValueError(
                f"{label}: --{name} の定義が {n} 件マッチ(期待1)。"
                f"0件=配線切れ/改名、複数=想定外の重複定義。")
    return css
