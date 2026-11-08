#!/usr/bin/env node
/* validate.mjs — 生成HTMLの機械検証(最小版)。
   検査: (1) 全<script>の構文チェック (2) 属性内の NaN/undefined/None
        (3) id重複 (4) SVG属性の負値幅・高さ (5) 未置換プレースホルダ
        ADR-8 ゲートA(デザインシステムの静的検査):
        (6) SVG の中に <text> がない(文字は HTML で描く)
        (7) font-size の指定はトークン(var(--fs-*))の参照だけ
            (CSS・style 属性・SVG の font-size 属性・font 一括指定のすべて)
        (8) --fs-* トークンの値は、共通の部品のトークン6つ(--portal-fs-sub 14px・--portal-fs 16px・
            --portal-fs-h1 18px・--portal-fs-h3 20px・--portal-fs-h2 24px・--portal-fs-num 32px)のどれかの
            参照か、同じ値(px、rem(1rem=16px で換算))。狭い画面での上書きも同じ。--portal-fs* の値は 14px 以上
            (clamp(最小値,…) は最小値で判定。hino-portal DESIGN.md 5.2・DESIGN.md ADR-15・ADR-16)
        (9) style 属性と SVG の fill・stroke 属性に色の値(#…・rgb(…)・hsl(…))を直接書かない。
            ページの部品の色は CSS のトークン(var(--portal-…) など)で書く(DESIGN.md ADR-14)。
            共通の部品(<!-- portal-shared:… -->)の中は正本のまま(hino-portal の shared/check.py が確かめる)
        (10) font-weight の値は 400・700・800(normal・bold を含む)だけ。CSS・style 属性のすべて
            (800 は数字の札の主の値に使う。hino-portal DESIGN.md 5.2・DESIGN.md ADR-16)
        日野町データポータルの共通の部品の CSS(<!-- portal-shared:css start --> … end。正本は
        hino-portal の shared/shell.css。DESIGN.md ADR-12)は、(7)(8) を共通の部品のトークン
        --portal-fs* で同じように検査する(ページの CSS では --portal-fs* を使えない)
   使い方: node tools/validate.mjs docs/plans/<id>.html [...] */
import { readFileSync, writeFileSync, rmSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { tmpdir } from "node:os";
import { join } from "node:path";

let bad = 0;

for (const path of process.argv.slice(2)) {
  const html = readFileSync(path, "utf-8");
  const errors = [];

  // (1) script構文
  const scripts = [...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)];
  scripts.forEach((m, i) => {
    const tmp = join(tmpdir(), `hpv1_check_${process.pid}_${i}.js`);
    writeFileSync(tmp, m[1]);
    try {
      execFileSync("node", ["--check", tmp], { stdio: "pipe" });
    } catch (e) {
      errors.push(`script[${i}] 構文エラー: ${String(e.stderr).split("\n")[0]}`);
    } finally {
      rmSync(tmp, { force: true });
    }
  });

  // (2) 属性内の NaN / undefined / None
  for (const m of html.matchAll(/\s[\w-]+="[^"]*\b(NaN|undefined|None|null)\b[^"]*"/g)) {
    errors.push(`属性に${m[1]}: ${m[0].trim().slice(0, 80)}`);
  }

  // (3) id重複
  const ids = new Map();
  for (const m of html.matchAll(/\sid="([^"]+)"/g)) {
    ids.set(m[1], (ids.get(m[1]) || 0) + 1);
  }
  for (const [id, n] of ids) if (n > 1) errors.push(`id重複: "${id}" ×${n}`);

  // (4) SVGの負値
  for (const m of html.matchAll(/\s(width|height|x|y|cx|cy|r)="(-[\d.]+)"/g)) {
    errors.push(`SVG属性が負値: ${m[1]}="${m[2]}"`);
  }

  // (5) 未置換プレースホルダ
  for (const m of html.matchAll(/\{\{[A-Z_]+\}\}/g)) {
    errors.push(`未置換プレースホルダ: ${m[0]}`);
  }

  // (6) SVG 内の文字
  for (const m of html.matchAll(/<svg[\s\S]*?<\/svg>/g)) {
    if (/<text[\s>]/.test(m[0])) errors.push("SVG の中に <text> がある(ADR-8: 文字は HTML で描く)");
  }

  // (7) font-size はトークン参照のみ。共通の部品の CSS は分けて、--portal-fs* で検査する(ADR-12)
  const SHARED = /<!-- portal-shared:css start -->([\s\S]*?)<!-- portal-shared:css end -->/g;
  const sharedCss = [...html.matchAll(SHARED)].map((m) => m[1]).join("\n");
  const styles = (h) => [...h.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)].map((m) => m[1]).join("\n");
  const css = styles(html.replace(SHARED, ""));
  for (const [text, prefix] of [[css, "var(--fs-"], [sharedCss, "var(--portal-fs"]]) {
    for (const m of text.matchAll(/font-size\s*:\s*([^;}]+)/g)) {
      const v = m[1].trim();
      if (!v.startsWith(prefix)) errors.push(`font-size がトークン参照でない: font-size:${v}`);
    }
  }
  for (const m of sharedCss.matchAll(/(?:^|[{;\s])font\s*:\s*([^;}]+)/g)) {
    if (/\d(px|em|rem|pt|%)/.test(m[1])) errors.push(`font 一括指定に大きさがある: font:${m[1].trim()}`);
  }
  for (const m of css.matchAll(/(?:^|[{;\s])font\s*:\s*([^;}]+)/g)) {
    if (/\d(px|em|rem|pt|%)/.test(m[1])) errors.push(`font 一括指定に大きさがある: font:${m[1].trim()}`);
  }
  for (const m of html.matchAll(/\sstyle="([^"]*)"/g)) {
    if (/font-size|(^|;)\s*font\s*:/.test(m[1])) errors.push(`style 属性で文字の大きさを指定: ${m[1].slice(0, 60)}`);
  }
  for (const m of html.matchAll(/\sfont-size="([^"]*)"/g)) {
    errors.push(`font-size 属性: font-size="${m[1]}"`);
  }

  // (8) --fs-* の値は共通の部品のトークン6つのどれか。--portal-fs* の値は 14px 以上(ADR-15・ADR-16)
  const MIN_PX = 14;
  const STEPS = { "--portal-fs-sub": 14, "--portal-fs": 16, "--portal-fs-h1": 18, "--portal-fs-h3": 20, "--portal-fs-h2": 24, "--portal-fs-num": 32 };
  const toPx = (v) => {
    const c = /^clamp\(\s*([^,]+),/.exec(v);
    if (c) v = c[1].trim();
    const u = /^([\d.]+)(px|rem)$/.exec(v);
    if (!u) return null;
    return parseFloat(u[1]) * (u[2] === "rem" ? 16 : 1);
  };
  const steps = Object.values(STEPS);
  for (const m of css.matchAll(/--fs-[\w-]+\s*:\s*([^;}]+)/g)) {
    const v = m[1].trim();
    const ref = /^var\(\s*(--portal-fs[\w-]*)\s*\)$/.exec(v);
    if (ref) {
      if (!(ref[1] in STEPS)) errors.push(`--fs-* が共通の部品のトークン6つ以外を参照している: ${m[0].trim()}`);
      continue;
    }
    const px = /^clamp\(/.test(v) ? null : toPx(v);
    if (px === null) errors.push(`--fs-* の値を判定できない(--portal-fs* の参照・px・rem のみ可): ${m[0].trim()}`);
    else if (!steps.includes(px)) errors.push(`--fs-* の値が 14・16・18・20・24・32px のどれでもない(${px}px): ${m[0].trim()}`);
  }
  for (const m of sharedCss.matchAll(/--portal-fs[\w-]*\s*:\s*([^;}]+)/g)) {
    const px = toPx(m[1].trim());
    if (px === null) errors.push(`--portal-fs* の値を判定できない(px・rem・clamp のみ可): ${m[0].trim()}`);
    else if (px < MIN_PX) errors.push(`--portal-fs* の値が ${MIN_PX}px 未満: ${m[0].trim()}`);
  }
  // <small> は親より小さくなるので使わない(hino-portal DESIGN.md 5.2)
  if (/<small[\s>]/i.test(html)) errors.push("<small> の要素がある(文字の大きさはトークンで決める)");

  // (9) style 属性・fill・stroke 属性の色の値(ADR-14)
  const RAW_COLOR = /#[0-9A-Fa-f]{3,8}\b|\b(?:rgba?|hsla?)\(/;
  const body = html.replace(/<!-- portal-shared:(\w+) start -->[\s\S]*?<!-- portal-shared:\1 end -->/g, "");
  for (const m of body.matchAll(/\s(style|fill|stroke)="([^"]*)"/g)) {
    if (RAW_COLOR.test(m[2])) errors.push(`${m[1]} 属性に色の値を直接書いている(トークンを使う): ${m[0].trim().slice(0, 80)}`);
  }

  // (10) font-weight は 400・700・800 だけ(hino-portal DESIGN.md 5.2・ADR-16)
  const WEIGHTS = new Set(["400", "700", "800", "normal", "bold"]);
  for (const [where, text] of [["CSS", css + "\n" + sharedCss], ...[...html.matchAll(/\sstyle="([^"]*)"/g)].map((m) => ["style 属性", m[1]])]) {
    for (const m of text.matchAll(/font-weight\s*:\s*([^;}"]+)/g)) {
      const v = m[1].trim().replace(/\s*!important$/, "");
      if (!WEIGHTS.has(v)) errors.push(`font-weight が 400・700・800 のどれでもない(${where}): font-weight:${v}`);
    }
  }
  for (const m of sharedCss.concat(css).matchAll(/(?:^|[{;\s])font\s*:\s*([^;}]+)/g)) {
    const w = /\b(100|200|300|500|600|900|bolder|lighter)\b/.exec(m[1]);
    if (w) errors.push(`font 一括指定の太さが 400・700・800 のどれでもない: font:${m[1].trim()}`);
  }

  const status = errors.length ? "NG" : "OK";
  console.log(`${status}  ${path}  (scripts=${scripts.length}, errors=${errors.length})`);
  errors.forEach((e) => console.log(`  [ERROR] ${e}`));
  if (errors.length) bad++;
}

process.exit(bad ? 1 : 0);
