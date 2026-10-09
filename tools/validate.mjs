#!/usr/bin/env node
/* validate.mjs — 生成HTMLの機械検証(最小版)。
   検査: (1) 全<script>の構文チェック (2) 属性内の NaN/undefined/None
        (3) id重複 (4) SVG属性の負値幅・高さ (5) 未置換プレースホルダ
        ADR-8 ゲートA(デザインシステムの静的検査):
        (6) SVG の中に <text> がない(文字は HTML で描く)
        (7) font-size の指定はトークン(var(--fs-*))の参照だけ
            (CSS・style 属性・SVG の font-size 属性・font 一括指定のすべて)
        (8) --fs-* トークンの値は px で 12px 以上
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

  // (7) font-size はトークン参照のみ
  const css = [...html.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)].map((m) => m[1]).join("\n");
  for (const m of css.matchAll(/font-size\s*:\s*([^;}]+)/g)) {
    const v = m[1].trim();
    if (!v.startsWith("var(--fs-")) errors.push(`font-size がトークン参照でない: font-size:${v}`);
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

  // (8) --fs-* の値は 12px 以上
  for (const m of css.matchAll(/--fs-[\w-]+\s*:\s*([^;}]+)/g)) {
    const v = m[1].trim();
    const px = /^([\d.]+)px$/.exec(v);
    if (!px) errors.push(`--fs-* の値が px でない: ${m[0].trim()}`);
    else if (parseFloat(px[1]) < 12) errors.push(`--fs-* の値が 12px 未満: ${m[0].trim()}`);
  }

  const status = errors.length ? "NG" : "OK";
  console.log(`${status}  ${path}  (scripts=${scripts.length}, errors=${errors.length})`);
  errors.forEach((e) => console.log(`  [ERROR] ${e}`));
  if (errors.length) bad++;
}

process.exit(bad ? 1 : 0);
