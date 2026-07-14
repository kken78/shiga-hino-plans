# CLAUDE.md — hino-plans 作業手順書

このリポジトリは滋賀県日野町の計画ダッシュボード集(非公式)。
真実の源は `data/plans/<id>.json`、規範は `PLAN_SCHEMA.md`、
設計判断の経緯は `DESIGN.md`。迷ったらこの順に読む。

## 不変の制約(絶対)

1. **捏造禁止**: 原典(PDF等)で確認できない数値・固有名詞・年度は書かない。
   欠けているなら欠けたまま出す。推定は `estimate:true` +根拠必須。
2. **全データブロックに出典**: `source` は文書id+ページ。台帳(§sources)に
   ない文書は参照できない。表示上、連続する同一 source はまとめて1回だけ
   出す(集約は表示のみ・audit は全ブロック個別保持。PLAN_SCHEMA §4 参照)。
3. **vanilla のみ**: フレームワーク・CDN・fetch・外部フォント禁止。
   出力は file:// と GitHub Pages の両方で動く自己完結HTML。
   構造トークンの正典は `templates/tokens.css`(ソースは1ファイル)。
   実行時 fetch せず、ビルド時に各HTMLへインライン展開する(生成物には
   同内容が inline される。これは重複でなく仕様。DESIGN.md ADR-6)。
4. **図表は数値から計算生成**: 座標・バー幅・達成率をデータとして
   手書きしない。それらは render_plan.py の仕事。
5. **スキーマが先**: 語彙を増やしたい場合は PLAN_SCHEMA.md を先に更新し、
   validate → render の順で実装を追従させる。実装先行は禁止。
6. **手抜き禁止**: 検証をスキップしない。エラーを握りつぶさない。

## 新しい計画を1本追加する定型フロー

前提: 原典PDFを `sources_raw/<id>/` に置いてから開始。

```bash
# 0) 計画id を決める(英小文字+数字。PLAN_SCHEMA.md §8)

# 1) 抽出 — PDFを読み、PLAN_SCHEMA.md に従って JSON を起票
#    data/plans/<id>.json
#    - まとめタブ規約(tabs[0]=summary)を守る
#    - 全数値に出典ページ。読めないページの数値は書かない
#    - 図版(路線図等)は data/assets/ に切り出し、figure ブロックで参照

# 2) スキーマ検証
python3 tools/validate_schema.py data/plans/<id>.json

# 3) レンダリング(照合表も同時生成)
python3 tools/render_plan.py <id>
#    → docs/plans/<id>.html
#    → build/audit/<id>.tsv

# 4) 出力検証(既存ハーネス)
node tools/validate.mjs docs/plans/<id>.html

# 5) ハブへ登録
#    data/manifest.json の該当計画に "dashboard": "plans/<id>.html" を追記
python3 tools/build.py          # → docs/index.html 再生成

# 6) 人間の照合(Claude はここで止まり、依頼者に引き渡す)
#    build/audit/<id>.tsv を原典PDFと突合してもらう
```

3〜5 はどれか一つでも失敗したら先に進まない。修正して再実行する。

## セッション運用

- **1計画=1セッション**を基本とする。前セッションの文脈に依存しない
  (必要な前提はすべて本書と PLAN_SCHEMA.md にあるべき。足りなければ
  作業前に本書へ追記する)。
- **編集の成否は差分プレビューでなく実ファイルで確認**する。コード編集後は
  `python3 -m py_compile <file>`、Markdown等の構造編集後は
  `sed -n '/見出し/,/次の見出し/p' <file>` で該当箇所を実表示してから報告する。
  なおこの環境(penguin)ではターミナル表示・差分プレビューともに長い日本語で
  行頭記号や語句を脱落させることがある。ゆえに実ファイルの成否は本文の目視でなく
  `grep -c "キーワード" <file>`(数字は化けない)で確認する。長い日本語本文の
  投入・編集は、化けを避けるため GitHub web エディタ経由を優先する。
- レンダラーや共通CSSを触った場合は、影響が全計画に及ぶ。
  必ず全計画を再レンダリングして validate を通す:
  ```bash
  for f in data/plans/*.json; do id=$(basename "$f" .json); \
    python3 tools/render_plan.py "$id" && \
    node tools/validate.mjs "docs/plans/$id.html" || exit 1; done
  python3 tools/build.py
  ```

## 抽出時の判断基準

- どのタブ構成にするかは計画の性格で決める。参考パターン:
  - 指標・目標型(子ども等): まとめ / 現状データ / 調査・評価 / 施策 / 見込み
  - 資産・マネジメント型(公共施設等): まとめ / 現状 / 将来推計 / 課題 / 方針
  - インフラ・ネットワーク型(交通等): まとめ / 背景 / 方針 / 施策 / 目標
- 原典の章立てをなぞるのではなく、住民が知りたい順に再構成する。
  ただし数値・文言の内容は原典に忠実に。
- **原典の明白なタイポ**(誤変換・脱字・衍字)で正しい表記が一意に定まるものは、
  黙って正しい表記に修正してよい(例「体験でえきる」→「体験できる」)。ただし
  数値・固有名詞・事実関係に関わる誤り、または正解が複数あり得るものは勝手に
  直さず、報告して判断を仰ぐ。
- `custom_html` は最後の手段。使ったら `reason` に理由を書き、
  PR/引き渡しメモで明示する(3割超えは設計見直しのサイン。DESIGN.md参照)。

## やってはいけないこと

- docs/ 配下の生成物を手で編集する(必ず data/ と tools/ から生成)
- 検証エラーを条件緩和で「直す」(緩和はスキーマ改定の合意が先)
- 出典のない「それらしい」平均値・全国値で空欄を埋める
- 相対年度表記(本年度・今年度・来年度)を書く
- 計画JSONに色HEXやスタイルを書く(テーマトークンのみ)

## 現状の実装状態(2026-07 更新)

動作確認済み: `validate_schema.py` → `render_plan.py kodomo` →
`validate.mjs` のパイプライン一式。`data/plans/kodomo.json` は
**6タブ完成**(summary / genjo / needs / hyoka / shisaku / ryo・全実データ、
audit 886行)。既存 `.hkodomo` 埋め込みからの逆抽出を全タブに展開済み。

チャートは全6 kind 実装済み(`bars` / `stacked_bars` / `stacked_100` /
`line` / `pair_bars` / `rank_bars`)。ブロック語彙も全20種を実装
(programs の `intro`/`desc`、table の `fold`、ジャンプ目次 `toc` を含む)。
出典表示は連続同一 source をラン末尾に集約(表示のみ・PLAN_SCHEMA §4)。
移植の視覚リファレンスは `sources_raw/hub-legacy.html` の
`.hkodomo`(stack100/pairBars)と `.hpshi`(render)。描画確認用フィクスチャは
`data/plans/_charttest.json`(3種を1本に。id が `_` 始まりでも
`data/plans/*.json` の全計画ループに乗るので、レンダラー改修時に
自動再描画・自動検証される)。

構造トークン(`--ink`/`--line`/`--card`)は `templates/tokens.css` を単一正典とし、
`tools/tokens.py`(load_tokens/apply_tokens・モデルA)が render_plan.py / build.py
経由で各セレクタへ値代入する(移行A・ADR-6。計画=バイト不変、ハブ=計画側値へ収束)。

### Phase 1 の進捗

- [x] **kodomo.json の全タブ完成**(2026-07 実装完了) — `.hkodomo` 埋め込みから
  needs / hyoka / shisaku / ryo を逆抽出し6タブ化。SETSU(節>グループ>事業)は
  `programs` ブロックへ。あわせて全6タブの見た目調整3件を実施:
  ①セクション見出しの統一(b-heading/phead/chart title を明朝17pxに集約)
  ②縦長タブ(shisaku/ryo)へのジャンプ目次 `toc`
  ③連続同一 source の出典表示集約。
  ※ここでの「完成」は**実装が完成**の意味。**最終検収(人間による audit 突合=
  `build/audit/kodomo.tsv` を原典 `sources_raw/kodomo/honpen.pdf` と照合)は
  次工程**で、まだ未実施。

### 残タスク

**完了**
- [x] **tools/build.py(Phase 2)**(2026-07 完了) — ハブを data 外出しの
  リファクタで再構築。成果物:
  - `templates/hub.html` — hub-legacy から shell を抽出。embed 機構を廃止
    (template×3・style×2・script×2・関数を除去)、CATS 生HEXを `:root` の
    `--cat-*` トークンへ集約、`const PLANS` をプレースホルダ化、`dashboard`
    保有計画はカードから個別ページへ直接リンク(非保有は図解モーダル)。
  - `data/manifest.json` — `const PLANS`(43計画)を**データ改変ゼロ**で移行。
    id 統一(`kosodate3` → `kodomo`)、`embed` 削除、`{schema, plans}` 構造化。
  - `tools/build.py` — manifest → `docs/index.html` 生成。健全性チェック
    (必須9・id一意・`_`始まり禁止・cat∈CATS・verified:true→start/end数値・
    dashboard実在)、cat 検証キーは templates/hub.html から実行時抽出(二重管理
    回避)、PLANS を indent 付き JSON で inline(fetch 不使用の自己完結)。
  - `docs/index.html` — 43計画カード・kodomo はダッシュボードへリンク・
    vanilla 自己完結(生成・依頼者のブラウザ目視で確認済み)。
  - **id が `_` で始まる計画(フィクスチャ等)は manifest 対象外**(据え置き)。
- [x] CLAUDE.md を現状に整合(build.py 完了の反映を含む)。

**着手予定**
- [ ] 論点4:デザイントークン統一(DESIGN.md ADR-6)。移行A/Bに分割。
  - [x] ADR-6 を DESIGN.md に記録(2026-07)。
  - [x] **移行A**(構造色・見た目不変のリファクタ)(2026-07 完了) —
    `templates/tokens.css` を新設(監査済み3トークン `--ink`/`--line`/`--card`
    の単一正典)。役割 audit → 役割確定 → 値決定 → 置換・検証(ADR-6)を実施し、
    3トークンを計画側値へ収束。値代入機構は `tools/tokens.py` に単一実装
    (load_tokens/apply_tokens・名前アンカー `--name:#hex`・fail-loud)、
    render_plan.py が shell.css の `.hpv1{}` 内、build.py が hub.html の
    `:root{}` 内へ代入(モデルA。連結・前置はしない。詳細は DESIGN.md ADR-6)。
    計画ページはバイト不変、ハブは L25 の1行のみ収束。三点セット検証
    (バイト一致/センチネル/fail-loud)合格。実装は 85fa870(計画側)・
    3897746(ハブ側)。
  - [ ] **移行B**(フォント・見た目変化): rem 化と本文底上げ(15→16px 等)。
    Aとは別コミット群にして bisect 可能に。全計画再レンダリング+目視必須。

**原典PDF待ち(配置後に着手)**
- [ ] **koutsu の移行** — 路線図JPEGを `data/assets/koutsu_map.jpg` に切り出し
  `figure` ブロックで参照。前提: `sources_raw/koutsu/` に原典配置。
- [ ] **shisetsu の移行** — 前提: `sources_raw/shisetsu/` に原典配置。
- [ ] 他40計画の抽出 — 各原典PDFを配置してから定型フローで1本ずつ。
