# hino-plans v2 アーキテクチャ設計書

対象: 日野町 計画ライブラリ(hino-plans)の計画ダッシュボード量産体制
作成: 2026-07 / 既存3埋め込み(交通・公共施設・子ども)の解剖に基づく

---

## 1. 背景と現状分析

### 1.1 現行アーキテクチャ

ハブ `index.html`(595KB)に43計画のカード情報(`PLANS` 配列)を持ち、
うち3計画は `<template id="*-embed">` として詳細ダッシュボードを丸ごと inline。
モーダル(`.pd`)に template を clone して差し込み、`runEmbedScripts` で
埋め込み内の IIFE を再実行する方式。

### 1.2 既存3本は「3世代」の作り方が混在している

| | 交通 (.hplan) | 公共施設 (.hpshi) | 子ども (.hkodomo) |
|---|---|---|---|
| サイズ | 305KB(95%が路線図JPEG、実質15KB) | 90KB | 50KB |
| 生成方式 | 純静的HTML | 静的+一部JS生成 | ほぼ全面JS実行時生成 |
| データの所在 | HTMLに直書き | HTML直書き+JS内配列 | JS内リテラル |
| 数値→図の整合 | 手置き(`width="95"` を手書き) | 混在 | 数値から計算生成 |
| タブ切替 | 646BのJS | 5.5KBのJS | レンダラー内 |

第3世代(子ども計画)が到達点に最も近い。`stackedBars` / `lineChart` /
`pairBars` / `stack100` / `barRank` / `kpi` / `tbl` の描画関数群を持ち、
冪等(runEmbedScripts 再実行耐性)・エスケープ・tabular-nums など品質が高い。

### 1.3 40計画スケールで破綻する3点

1. **データがコードに埋まっている。**
   `kpi('総人口','20,761',...)` のような整形済み文字列が描画コードに散在し、
   原典PDFとの照合・監査が構造的に不可能。数値の変更にコード修正が要る。
2. **レンダラーが計画ごとに複製される。**
   `.hkodomo` スコープ内にコピーされたチャート関数は、40本つくれば40の
   方言になる。バグ修正・改善が横展開できない。
3. **ハブへの全量inlineはサイズ破綻する。**
   3本で595KB。40本なら数MB。fetch禁止・自己完結の制約下では遅延読込も
   できないため、「1ファイルに全部」の方針自体を変える必要がある。

加えて第1世代の「手置きSVG座標」は、数値と図の乖離(=意図せぬ捏造)を
検出する手段がなく、データ整合性の方針と両立しない。

---

## 2. アーキテクチャ決定

### ADR-1: 真実の源を `data/plans/<id>.json` に分離し、レンダラーを1本化する

- 計画ごとの成果物は **スキーマ準拠のJSONデータのみ**(数KB〜数十KB)。
- `tools/render_plan.py` がビルド時に JSON → 自己完結HTMLを生成する。
- 図表は必ず数値から座標計算する。手置き座標は語彙から排除。
- 全データブロックに出典(`source`: 文書ID+ページ)を必須とし、
  スキーマ検証(`validate_schema.py`)で欠落を機械検出する。
- レンダリング時に照合表 `build/audit/<id>.tsv`(値↔表示↔出典の対応)を
  自動出力し、PDF突合を人間の定型作業として残す。

**採らなかった案とその理由**
- *ランタイムJSレンダリング(共通レンダラーJSをハブ常駐)*:
  修正の即時波及は魅力だが、(a)生成物が残らず差分レビュー・数値照合が
  しづらい、(b)実行時エラーで白画面になるリスク、(c)runEmbedScripts の
  冪等性管理が複雑、のため。ビルド時生成でも `build.py` 再実行で全計画に
  波及するので、実運用上の差はない。
- *埋め込みHTMLを引き続き1本ずつ手書き*: 1.3 の通り破綻する。

### ADR-2: 詳細ダッシュボードはモーダルinlineをやめ、個別ページへ

- 生成先: `docs/plans/<id>.html`(自己完結・file://可・GitHub Pages可)。
- ハブは従来どおりカード+概要モーダル(PLANSのlead/summary/points)。
  ダッシュボード保有計画にはモーダル内に
  「くわしいダッシュボードを見る」リンク(通常のページ遷移)を出す。
- ハブの `PLANS` 配列は `data/manifest.json` に外出しし、`build.py` が
  ハブHTMLへ inline する(二重管理の解消)。ダッシュボード有無は
  manifest の `dashboard` フィールドで管理。
- 既存3本の `<template>` embed は移行完了(=3本の個別ページ化)まで並走可。
  移行後に削除するとハブは約450KB軽くなる。

**採らなかった案**
- *全量inline継続*: サイズ破綻(1.3)。
- *iframe*: 既定方針(embed-as-dialog-content、iframe不使用)に反する。
  個別ページ遷移なら iframe は不要。

**副次効果**: 各計画に共有可能なURLが付く。CSS衝突問題(.hplan/.hpshi等の
ラッパー分離が必要だった)はページ分離で構造的に消える。ただし将来の
再埋め込みに備え、ラッパークラス `.hpv1` スコープの習慣は維持する。

**実装状況(2026-07 完了)**: embed 機構(`<template>` 差し込み+script
再実行)を**廃止**した。`templates/hub.html`(hub-legacy から shell を抽出)
から embed style×2・template×3・script×2 と `planEmbedHTML`/`runEmbedScripts`/
スロット注入を除去。ハブのカードは、**`dashboard` 保有計画=個別ページへの
直接リンク**(`<a class="p-more p-more-link">`。data-id を持たずネイティブ遷移)、
**非保有計画=従来どおり概要モーダル**(`pdInfographic`/`pdSystem` で lead/
summary/points/info を図解)にフォールバックする。当初案(モーダル内リンク)
から「カードから直接リンク」へ変更。`build.py` が `data/manifest.json`(43計画)
を `docs/index.html` へ inline 生成する(fetch 不使用の自己完結)。分野色 CATS は
生HEXを `:root` の `--cat-*` トークンへ集約(データは色を持たない)。

### ADR-3: かんたん/くわしくは「まとめタブ規約」で実現する

hino-finance の学び:「トグルの境界はルールで能動的に強制しないと崩れる」。
40計画で計画ごとのトグル境界を人力保守するのは持続しない。

- 規約: **tabs[0] は必ず `id:"summary"`, `label:"まとめ"`** とし、
  かんたん語彙(`kpi_grid` / `vision` / `narrative` / `targets` /
  `actions` / `note`)のみ配置可。表・多年度詳細・事業一覧・用語集は
  タブ02以降に置く。
- この規約は `validate_schema.py` が機械的に強制する(人の規律に頼らない)。
- 住民は最初のタブだけで要点が完結し、関心があれば深いタブへ進む。

### ADR-4: 計画の「個性」はテーマトークンに限定する

- 共通CSS(シェル・ブロック・チャート)は1本。計画ごとに変えられるのは
  `theme` の色トークン(accent / accent2 / bg系 5〜6変数)のみ。
- 40本の視覚的統一と、分野ごとの見分けやすさを両立する。
  (子ども=松×杏、交通=藍×赤 のような既存の配色個性はトークンで再現可能)

### ADR-5: 画像はJSONに埋めず、アセット参照でビルド時にinline化する

- 交通計画の路線図(290KB JPEG)のような図版は `data/assets/` に実体を置き、
  JSONは `{"type":"figure","asset":"koutsu_map.jpg",...}` で参照。
- render 時に base64 data URI 化して出力HTMLに inline(自己完結は維持)。
- JSONの可読性・差分レビュー可能性を守るため。

---

## 3. リポジトリ構成

```
hino-plans/
├── CLAUDE.md               # Claude Code 作業手順書(不変ルール+定型フロー)
├── DESIGN.md               # 本書
├── PLAN_SCHEMA.md          # スキーマ規範(ブロック語彙リファレンス)
├── data/
│   ├── manifest.json       # 43計画のカード情報(現PLANS配列の外出し)
│   ├── plans/<id>.json     # ダッシュボード化した計画のデータ(真実の源)
│   └── assets/             # figure用の画像実体
├── tools/
│   ├── render_plan.py      # JSON → docs/plans/<id>.html + audit.tsv
│   ├── validate_schema.py  # スキーマ検証(出典必須・まとめタブ規約 等)
│   ├── validate.mjs        # 既存の出力HTML検証ハーネス
│   └── build.py            # manifest → ハブ index.html 生成
├── templates/
│   ├── shell.html          # 計画ページ共通シェル
│   ├── shell.css           # 共通CSS(デザイントークン+ブロック+チャート)
│   └── runtime.js          # 共通ランタイム(タブ切替のみ、~1KB)
├── build/audit/<id>.tsv    # 照合表(git管理外でも可)
└── docs/                   # GitHub Pages 公開ルート
    ├── index.html          # ハブ
    └── plans/<id>.html     # 生成された計画ダッシュボード
```

---

## 4. 品質保証の全体像

| 段階 | ツール | 何を保証するか |
|---|---|---|
| 抽出 | Claude Code + PLAN_SCHEMA.md | 出典なき数値を書かない(捏造禁止) |
| スキーマ検証 | validate_schema.py | 必須項目・型・出典・まとめタブ規約・ID一意性 |
| レンダリング | render_plan.py | 図表座標は数値から計算(手置き禁止の構造化) |
| 出力検証 | validate.mjs(既存) | JS構文・NaN/undefined属性・ID重複 |
| 照合 | build/audit/<id>.tsv | 人がPDFと突合(値・単位・出典ページ) |

---

## 5. 移行計画

1. **Phase 0(このリポジトリ骨格)**: スキーマ・レンダラー骨格・手順書。
2. **Phase 1(逆抽出で検収)**: 子ども計画を `kodomo.json` に逆抽出し、
   render 出力が既存embedと同等以上であることを確認。語彙の過不足を
   ここで洗い出してスキーマを確定する。次いで公共施設・交通を移行
   (交通の路線図は figure/asset 方式へ)。
3. **Phase 2(ハブ接続)**: manifest 外出し+`build.py`。モーダルに
   ダッシュボードリンク追加。既存 `<template>` embed を撤去。
4. **Phase 3(量産)**: 残り37計画をCLAUDE.mdの定型フローで1計画ずつ。
   1計画=1セッション。スキーマ変更が必要になったら必ず
   PLAN_SCHEMA.md を先に更新してから実装する(文書が常に規範)。

### 撤退基準(設計の健全性チェック)

- 最初の5計画で `custom_html` ブロック(逃げ道)の使用が3割を超えたら、
  ブロック語彙の切り方が誤っているサイン。量産を止めて語彙を再設計する。
- audit.tsv の照合で不一致が出た場合、個別修正ではなく
  「なぜ抽出時に混入したか」をCLAUDE.mdの手順改善に還元する。
