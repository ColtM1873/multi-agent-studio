/* i18n 自查：核对 app.js 里所有 t("中文原文") 的 key 都在 I18N_EN 里有英文值。
 *
 * 为什么单独写一个脚本：项目里没有任何构建步骤，漏补 I18N_EN 时界面**不会报错**，
 * 只在英文状态下静默显示中文原文，很难在人工点测中发现（见 ID06 / ID23）。
 *
 * 用法：node scripts/check_i18n.js
 */
"use strict";

const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const APP_JS = path.join(ROOT, "app", "static", "js", "app.js");

/** 从源码里截出 `const I18N_EN = { ... };` 并用 Function 求值（不加载整个前端）。 */
function loadDict(source) {
  const start = source.indexOf("const I18N_EN = {");
  if (start < 0) throw new Error("找不到 I18N_EN 字典");
  const bodyStart = source.indexOf("{", start);
  let depth = 0;
  let inString = null;
  let escaped = false;
  for (let i = bodyStart; i < source.length; i += 1) {
    const ch = source[i];
    if (inString) {
      if (escaped) escaped = false;
      else if (ch === "\\") escaped = true;
      else if (ch === inString) inString = null;
      continue;
    }
    if (ch === '"' || ch === "'" || ch === "`") { inString = ch; continue; }
    if (ch === "{") depth += 1;
    else if (ch === "}") {
      depth -= 1;
      if (depth === 0) {
        const literal = source.slice(bodyStart, i + 1);
        return new Function(`return (${literal});`)();
      }
    }
  }
  throw new Error("I18N_EN 字典没有闭合");
}

function main() {
  const source = fs.readFileSync(APP_JS, "utf8");
  const dict = loadDict(source);
  const keys = new Set(Object.keys(dict));

  const used = new Set();
  const re = /\bt\(\s*"((?:[^"\\]|\\.)*)"/g;
  let match;
  while ((match = re.exec(source)) !== null) {
    // 源码里的字面量含转义（如 \\(、\n），要和字典（已按 JS 规则求值）比较就得先反转义
    try {
      used.add(new Function(`return "${match[1]}";`)());
    } catch (err) {
      used.add(match[1]);
    }
  }

  const missing = [...used].filter((key) => !keys.has(key)).sort();

  // 反向检查：字典里出现「像 key 一样的纯键」但代码里根本不用的，仅提示（不判失败）
  const unused = [...keys].filter((key) => !used.has(key) && /^[\u4e00-\u9fff]/.test(key));

  if (missing.length) {
    console.error(`❌ 有 ${missing.length} 个 t("...") 缺少英文值：`);
    missing.forEach((key) => console.error("   " + JSON.stringify(key)));
    process.exit(1);
  }
  console.log(`OK: ${used.size} 个 t("...") key 全部有英文值（字典共 ${keys.size} 条）`);
  if (unused.length) {
    console.log(`提示：字典里有 ${unused.length} 条中文键当前没被 t("...") 直接使用（可能是动态文案，可忽略）`);
  }
}

main();
