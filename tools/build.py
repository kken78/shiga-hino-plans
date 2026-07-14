#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build.py — data/manifest.json → docs/index.html(計画ライブラリのハブ)

templates/hub.html(hub-legacy から抽出した shell。PLANS はプレースホルダ)に、
manifest の plans を JSON インライン注入して自己完結HTMLを生成する。

設計原則:
- vanilla のみ。生成物に fetch/CDN/外部依存を持ち込まない(テンプレートが
  既に自己完結。PLANS はインライン埋め込みで fetch しない)。
- データ(manifest)は色を持たない。分野色(CATS)/状態(STATUS_META)は
  templates/hub.html 側に維持。build.py は cat 検証キーを **テンプレートの
  CATS 定義から実行時抽出**する(手書きの二重管理でズレるのを避ける)。
- 捏造・不整合の隠蔽をしない。健全性チェックに失敗したら全件を列挙して停止する。

使い方: python3 tools/build.py

────────────────────────────────────────────────────────────────────────
hub-manifest-v1 スキーマ(正典。PLAN_SCHEMA.md は per-plan 専用で別物)
────────────────────────────────────────────────────────────────────────
トップレベル:
  { "schema": "hub-manifest-v1", "plans": [ {…}, … ] }

plan の全フィールド:
  ── 必須(全計画)──
  id        英小数字・一意・"_" 始まり禁止(ファイル/フィルタ用)
  name      計画の正式名称
  cat       CATS の9キーのいずれか(sogo/toshi/kotsu/kankyo/kenko/
            kodomo/bosai/gyosei/sangyo。正典は templates/hub.html)
  lead      1文の導入(やさしい文体)
  summary   やさしい概要
  points    ポイント配列(1件以上)
  url       公式掲載ページ
  posted    掲載・更新時期(不明時は "掲載ページ参照")
  verified  期間バーを描けるか(bool)

  ── 任意 ──
  dept        所管課
  start,end   計画期間(令和・数値)。verified:true のとき必須ペア
  periodBadge 期間の補足バッジ(例「毎年度見直し」)
  periodNote  verified:false 時の期間表示文
  next        進捗と予定(次期策定など)
  ended       次期へ移行済み=期間終了(bool)
  flagship    最上位計画(bool。sogo6)
  stat,statLabel 象徴数値とそのラベル
  vision      将来像・基本理念
  info        柱図 { type:"system", vision, pillars:[{h,d}] }
  targets     数値目標の配列(将来用。現状0件)
  dashboard   個別ページの相対パス(例 "plans/kodomo.html")。
              個別ページ実体があるものだけ付与(embed 機構は廃止)
"""
import json
import re
import sys
from pathlib import Path
from tokens import load_tokens, apply_tokens
# tools/ が sys.path[0] に入る前提(python3 tools/build.py 起動に依存)。DESIGN.md ADR-6

ROOT = Path(__file__).resolve().parent.parent
TPL = ROOT / "templates"
MANIFEST = ROOT / "data" / "manifest.json"
OUT = ROOT / "docs" / "index.html"
DOCS = ROOT / "docs"

REQUIRED = ("id", "name", "cat", "lead", "summary",
            "points", "url", "posted", "verified")

PLACEHOLDER = '"__PLANS_PLACEHOLDER__"'


def cats_keys_from_template(template):
    """templates/hub.html の CATS 定義から分野キーを抽出する(色は持たない)。
    二重管理を避けるため build 側にキーをハードコードしない。抽出できなければ
    テンプレート書式の変更なので、黙って続けず明示エラーで停止する。"""
    m = re.search(r"const CATS = \{(.*?)\n\};", template, re.S)
    if not m:
        raise SystemExit("NG  templates/hub.html: CATS 定義が見つからない "
                         "(書式変更?)。cat 検証キーを抽出できない")
    keys = re.findall(r"\n\s*(\w+)\s*:\s*\{\s*label", m.group(1))
    if not keys:
        raise SystemExit("NG  templates/hub.html: CATS からキーを抽出できない "
                         "(書式変更?)")
    return frozenset(keys)


def validate(plans, cats_keys):
    """manifest の健全性チェック。問題を全て集めて返す(隠蔽しない)。"""
    errors = []
    seen = {}
    for i, p in enumerate(plans):
        where = f"plans[{i}]" + (f"(id={p['id']})"
                                 if isinstance(p, dict) and "id" in p else "")
        if not isinstance(p, dict):
            errors.append(f"{where}: オブジェクトでない")
            continue

        # 必須フィールド
        for k in REQUIRED:
            if k not in p:
                errors.append(f"{where}: 必須フィールド '{k}' がない")

        pid = p.get("id")
        if isinstance(pid, str):
            if pid.startswith("_"):
                errors.append(f"{where}: id が '_' 始まり(ハブ対象外の命名)")
            if pid in seen:
                errors.append(f"{where}: id が重複(既出 plans[{seen[pid]}])")
            else:
                seen[pid] = i

        # points は1件以上の配列
        if "points" in p and (not isinstance(p["points"], list) or not p["points"]):
            errors.append(f"{where}: points は1件以上の配列であること")

        # cat が CATS に存在
        cat = p.get("cat")
        if cat is not None and cat not in cats_keys:
            errors.append(f"{where}: 未定義カテゴリ cat={cat!r} "
                          f"(使用可: {sorted(cats_keys)})")

        # verified:true なら start/end が数値(bool は除外)
        if p.get("verified") is True:
            for k in ("start", "end"):
                v = p.get(k)
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    errors.append(f"{where}: verified:true には数値の '{k}' が必須 "
                                  f"(現在 {v!r})")

        # dashboard 指定があれば実体が存在
        dash = p.get("dashboard")
        if dash is not None:
            if not isinstance(dash, str):
                errors.append(f"{where}: dashboard は文字列パス")
            elif not (DOCS / dash).exists():
                errors.append(f"{where}: dashboard の実体がない: docs/{dash}")

    return errors


def build():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if manifest.get("schema") != "hub-manifest-v1":
        raise SystemExit(f"NG  {MANIFEST}: schema が hub-manifest-v1 でない "
                         f"({manifest.get('schema')!r})")
    plans = manifest.get("plans")
    if not isinstance(plans, list) or not plans:
        raise SystemExit(f"NG  {MANIFEST}: plans が非空配列でない")

    template = (TPL / "hub.html").read_text(encoding="utf-8")
    cats_keys = cats_keys_from_template(template)

    errors = validate(plans, cats_keys)
    if errors:
        print(f"NG  {MANIFEST}  (errors={len(errors)})", file=sys.stderr)
        for e in errors:
            print(f"  [ERROR] {e}", file=sys.stderr)
        raise SystemExit(1)

    n = template.count(PLACEHOLDER)
    if n != 1:
        raise SystemExit(f"NG  templates/hub.html: PLANS プレースホルダが "
                         f"{n} 箇所(期待1)。抽出テンプレートを確認")

    # 構造トークンの値代入(ADR-6 / モデルA)。hub は値が変わる=計画側と違いバイト不変ではない。
    tokens = load_tokens(TPL / "tokens.css")
    template = apply_tokens(template, tokens, "hub.html")

    # PLANS を JSON インライン注入(fetch せず自己完結。indent で差分を見やすく)
    plans_json = json.dumps(plans, ensure_ascii=False, indent=2)
    page = template.replace(PLACEHOLDER, plans_json)

    OUT.write_text(page, encoding="utf-8")
    print(f"OK  {OUT}  ({len(page):,} bytes, plans={len(plans)}, "
          f"cats={len(cats_keys)})")


def main(argv):
    build()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
