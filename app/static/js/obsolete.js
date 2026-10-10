/* ============================================================================
 * obsolete.js —— 废弃代码归档（前端）
 * ----------------------------------------------------------------------------
 * 本文件不参与加载（index.html 不会引入它），仅用于存放被废弃、但可能仍具参考
 * 价值的历史实现，便于日后回查或按需取回。
 *
 * 归档内容：v1.6.7 之后「进入/退出编辑模式视口不跳变」的第一版实现——
 * 基于「去空白字符计数」把视口顶部那一行文本映射回重渲染后的同一位置。
 * 该方案在公式（\[...\] 源文多行、渲染成一行）等场景下计数会错位，故整体废弃，
 * 由 app.js 中的「结构化文本锚点」（message / 模块 / 标题 + 顶部行文本 搜索匹配）取代。
 * ========================================================================== */

// 以下函数原为 renderChatView 闭包内声明，现原样归档。

// 取 range 起点所在文字节点起、向后若干字符的片段（用于重渲染后重新定位这一行）。
// 只保留非空白字符：正常历史是「整段一起渲染」，编辑态是「按行/按段分别渲染」，
// 两者的空白（软换行、段间空白）并不一致，按空白做精确匹配会失败。去掉所有空白后，
// 同一段文字在两种渲染下具备相同的字符序列。
function textSnippetAt(range, len) {
  let node = range.startContainer;
  if (!node || node.nodeType !== 3) return null;
  let text = node.data.slice(range.startOffset, range.startOffset + len);
  let cur = node;
  while (text.replace(/\s+/g, "").length < 8 && cur) {
    let nxt = cur.nextSibling;
    while (nxt && (nxt.nodeType !== 3 || isAnchorHidden(nxt))) nxt = nxt.nextSibling;
    if (!nxt) break;
    text += nxt.data.slice(0, len);
    cur = nxt;
  }
  const s = text.replace(/\s+/g, "");
  return s.length >= 6 ? s : null;
}

// 在 root 中按「忽略所有空白」查找 snippet，返回其所有出现的 Range。
function findTextRanges(root, snippet) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
  const nodes = [];
  let full = "";
  let n;
  while ((n = walker.nextNode())) {
    if (isAnchorHidden(n)) continue;
    nodes.push({ node: n, start: full.length });
    full += n.data;
  }
  const locate = (idx) => {
    for (let k = nodes.length - 1; k >= 0; k--) {
      if (idx >= nodes[k].start) return { node: nodes[k].node, offset: Math.min(idx - nodes[k].start, nodes[k].node.data.length) };
    }
    return nodes.length ? { node: nodes[0].node, offset: 0 } : null;
  };
  const normSnippet = snippet.replace(/\s+/g, "");
  if (!normSnippet) return [];
  let norm = "";
  const map = [];
  for (let i = 0; i < full.length; i++) {
    const ch = full[i];
    if (/\s/.test(ch)) continue;
    norm += ch;
    map.push(i);
  }
  const out = [];
  let from = 0;
  while (true) {
    const j = norm.indexOf(normSnippet, from);
    if (j < 0) break;
    const startLoc = locate(map[j]);
    const endLoc = locate(map[j + normSnippet.length - 1] + 1);
    if (startLoc && endLoc) {
      const r = document.createRange();
      r.setStart(startLoc.node, startLoc.offset);
      r.setEnd(endLoc.node, endLoc.offset);
      out.push(r);
    }
    from = j + 1;
  }
  return out;
}

// 以「非空白字符计数」作为锚点在消息块内的位置：正常历史（整段渲染）与编辑态
// （按行/按段渲染）字符序列一致、仅空白与分块不同，因此这个偏移在两种渲染间稳定。
// 锚点相关的文本遍历一律跳过 KaTeX 隐藏的 MathML（position:absolute+clip，
// getBoundingClientRect 恒为 0，且不可见），否则会把位置算到视口左上角。
function isAnchorHidden(node) {
  return !!(node.parentElement && node.parentElement.closest(".katex-mathml"));
}

function countNonWsBefore(root, range) {
  const target = range.startContainer;
  if (!target || target.nodeType !== 3 || !root.contains(target)) return null;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
  let count = 0, n;
  while ((n = walker.nextNode())) {
    if (n !== target && isAnchorHidden(n)) continue;
    if (n === target) {
      const s = n.data.slice(0, range.startOffset);
      for (let i = 0; i < s.length; i++) if (!/\s/.test(s[i])) count++;
      return count;
    }
    const d = n.data;
    for (let i = 0; i < d.length; i++) if (!/\s/.test(d[i])) count++;
  }
  return null;
}

function rangeAtNonWsOffset(root, offset) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
  let seen = 0, n, last = null;
  while ((n = walker.nextNode())) {
    if (isAnchorHidden(n)) continue;
    const d = n.data;
    for (let i = 0; i < d.length; i++) {
      if (/\s/.test(d[i])) continue;
      // 必须在「下一个非空白字符」处返回，而不是在节点边界返回上一个节点的末尾，
      // 否则当偏移正好落在某节点末尾（如 emoji 表头之后）时会锚到上一节点所在行。
      if (seen === offset) { const r = document.createRange(); r.setStart(n, i); r.setEnd(n, Math.min(i + 1, d.length)); return r; }
      seen++;
    }
    last = n;
  }
  if (last && seen === offset) {
    const r = document.createRange();
    r.setStart(last, Math.max(0, last.data.length - 1));
    r.setEnd(last, last.data.length);
    return r;
  }
  return null;
}

// 找到「视口顶部第一条可见文字行」：按文档顺序扫描文本节点的行矩形，
// 取 top 之上/之内仍有可见部分（bottom > paneTop）的第一行。
// 比直接用 caretRangeFromPoint 猜点更稳：不会落在段间空档或错误的行上。
function firstVisibleTextLine(pane) {
  const pr = pane.getBoundingClientRect();
  const paneTop = pr.top, paneBottom = pr.bottom;
  const walker = document.createTreeWalker(pane, NodeFilter.SHOW_TEXT, null);
  let n, best = null, bestTop = Infinity;
  while ((n = walker.nextNode())) {
    if (!n.data || !/\S/.test(n.data)) continue;
    const block = n.parentElement && n.parentElement.closest("[data-msg-indice]");
    if (!block) continue;
    // 跳过 KaTeX 的隐藏 MathML：它 position:absolute+clip，rect 不可靠，且不可见
    if (n.parentElement && n.parentElement.closest(".katex-mathml")) continue;
    const pe = n.parentElement;
    if (pe) {
      const er = pe.getBoundingClientRect();
      if (er.bottom <= paneTop + 0.5) continue;   // 该节点整体在视口上方
      if (er.top >= paneBottom) {                 // 该节点整体在视口下方
        if (best) break;                          // 已找到顶部可见行：其后的只会更靠下
        continue;
      }
    }
    const range = document.createRange();
    range.selectNodeContents(n);
    const rects = range.getClientRects();
    for (let i = 0; i < rects.length; i++) {
      const r = rects[i];
      if (r.height < 0.5) continue;
      if (r.bottom <= paneTop + 0.5) continue;    // 这一行完全在视口上方
      if (r.top >= paneBottom) continue;          // 这一行完全在视口下方
      // 取「最靠上」的可见行（top 最小），保证与用户看到的顶部一致
      if (r.top < bestTop) { bestTop = r.top; best = { node: n, rect: r, block }; }
    }
  }
  return best;
}

// 取某个字符索引处单字符的矩形（用于把「某一行」映射回精确的字符偏移）
function charRectAt(node, i) {
  const len = node.data.length;
  if (len === 0) return null;
  const k = Math.min(Math.max(0, i), len - 1);
  const range = document.createRange();
  range.setStart(node, k);
  range.setEnd(node, Math.min(k + 1, len));
  const rects = range.getClientRects();
  return rects.length ? rects[0] : null;
}

// 在一个文本节点里，二分找出「落在 top≈targetTop 那一行」的字符索引。
// 用它来精确锚定可见行，避免 caretRangeFromPoint 落到别的节点/隐藏元素上。
function offsetForLine(node, targetTop) {
  const len = node.data.length;
  let lo = 0, hi = len;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    const r = charRectAt(node, mid);
    if (r && (r.top + r.height / 2) < targetTop) lo = mid + 1;
    else hi = mid;
  }
  return Math.max(0, Math.min(lo, len - 1));
}

// 捕获锚点：优先精确到「视口顶部那一行文字」（字符级），失败再退回消息块级。
function captureMsgAnchor() {
  const pane = $("#historyPane");
  if (!pane) return null;
  const pr = pane.getBoundingClientRect();
  if (pr.height < 8) return null;
  const found = firstVisibleTextLine(pane);
  if (found) {
    // 直接在该文本节点里定位可见行对应的字符，不依赖 caretRangeFromPoint
    const k = offsetForLine(found.node, found.rect.top);
    const range = document.createRange();
    range.setStart(found.node, Math.min(k, found.node.data.length));
    range.setEnd(found.node, Math.min(k + 1, found.node.data.length));
    const b = found.block;
    const blockRect = b.getBoundingClientRect();
    const blockH = blockRect.height || 1;
    const rr = range.getBoundingClientRect();
    const top = (rr && rr.height) ? rr.top : found.rect.top;
    const base = { indice: b.getAttribute("data-msg-indice"), beforeTop: top, blockHeight: blockH, offsetRatio: (top - blockRect.top) / blockH };
    const snippet = textSnippetAt(range, 48);
    if (snippet) base.snippet = snippet;
    const charOffset = countNonWsBefore(b, range);
    if (charOffset != null) base.charOffset = charOffset;
    return base;
  }
  // 回退：消息块级锚点
  const blocks = pane.querySelectorAll("[data-msg-indice]");
  for (const b of blocks) {
    const r = b.getBoundingClientRect();
    if (r.bottom > pr.top + 8) return { indice: b.getAttribute("data-msg-indice"), beforeTop: r.top };
  }
  return { scrollTop: pane.scrollTop };
}

// 把「锚点行上方、但仍探入视口」的文字行推回视口之上。
// 作用：保证锚点行始终是视口显示的第一行——当上方内容（如思考块）在重渲染后
// 变高、尾部探进顶部空白外边距时，仅靠对齐锚点行还不算「第一行」，这里再补一点点滚动把它藏掉。
function hideLinesAbove(pane, anchorTopViewport) {
  const pr = pane.getBoundingClientRect();
  const paneTop = pr.top, paneBottom = pr.bottom;
  const walker = document.createTreeWalker(pane, NodeFilter.SHOW_TEXT, null);
  let n, maxBottom = -Infinity;
  while ((n = walker.nextNode())) {
    if (!n.data || !/\S/.test(n.data)) continue;
    if (!n.parentElement || !n.parentElement.closest("[data-msg-indice]")) continue;
    if (isAnchorHidden(n)) continue;
    const pe = n.parentElement;
    const er = pe.getBoundingClientRect();
    if (er.bottom <= paneTop + 0.5) continue;
    if (er.top >= paneBottom) continue;
    const range = document.createRange();
    range.selectNodeContents(n);
    const rects = range.getClientRects();
    for (let i = 0; i < rects.length; i++) {
      const r = rects[i];
      if (r.height < 0.5) continue;
      if (r.bottom <= paneTop + 0.5) continue;
      if (r.top >= paneBottom) continue;
      if (r.top >= anchorTopViewport - 0.5) continue;   // 不是锚点行上方的行
      if (r.bottom > maxBottom) maxBottom = r.bottom;
    }
  }
  if (maxBottom > paneTop + 0.5) pane.scrollTop += (maxBottom - paneTop);
}

// 回滚锚点：把同一行文字重新拉到它原先的视口纵向位置，并确保它仍是视口第一行。
function restoreMsgAnchor(anchor) {
  if (!anchor) return;
  const pane = $("#historyPane");
  if (!pane) return;
  let targetRect = null;
  if (anchor.indice != null) {
    const b = pane.querySelector(`[data-msg-indice="${anchor.indice}"]`);
    if (b) {
      const blockRect = b.getBoundingClientRect();
      // ① 精确：按「非空白字符计数」定位到同一处（正常历史与编辑态字符序列一致）
      if (anchor.charOffset != null) {
        const r = rangeAtNonWsOffset(b, anchor.charOffset);
        if (r) { const rr = r.getBoundingClientRect(); if (rr && rr.height) targetRect = rr; }
      }
      // ② 文本片段：按字符序列找到同一行（处理字符序列略有差异的情况）
      if (!targetRect && anchor.snippet) {
        const cands = findTextRanges(b, anchor.snippet);
        if (cands.length) {
          const targetOffset = (anchor.offsetRatio || 0) * (blockRect.height || 1);
          let best = null, bestDiff = Infinity;
          for (const c of cands) {
            const cr = c.getBoundingClientRect();
            if (!cr || !cr.height) continue;
            const diff = Math.abs((cr.top - blockRect.top) - targetOffset);
            if (diff < bestDiff) { bestDiff = diff; best = cr; }
          }
          if (best) targetRect = best;
        }
      }
      // ③ 近似：按「在消息块内的纵向比例」还原
      if (!targetRect && anchor.offsetRatio != null && blockRect.height) {
        targetRect = { top: blockRect.top + anchor.offsetRatio * blockRect.height };
      }
      // ④ 兜底：对齐消息块顶部
      if (!targetRect) targetRect = blockRect;
    }
  }
  if (targetRect) {
    pane.scrollTop += (targetRect.top - anchor.beforeTop);
    // 对齐后锚点行位于 beforeTop；把其上方仍探入视口的行推走
    hideLinesAbove(pane, anchor.beforeTop);
  } else if (anchor.scrollTop != null) {
    pane.scrollTop = anchor.scrollTop;
  }
}
