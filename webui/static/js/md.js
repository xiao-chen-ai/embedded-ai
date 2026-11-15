// md.js — 轻量 markdown 渲染（安全：先全文 HTML 转义，再做受限替换；绝不 eval）
// 支持：```围栏代码块(带复制按钮)、#标题、表格、ul/ol 列表、>引用、---分隔、
//       行内 `code`、**粗体**、*斜体*、~~删除~~、[文本](http链接)
import { esc } from './util.js';

const PH = (i) => `\u0000CB${i}\u0000`; // 代码块占位符（NUL 不会出现在正常文本）

/** 行内标记（输入必须已 esc）。链接仅允许 http(s) 协议。 */
function inline(s) {
  const codes = [];
  // 1) 行内 code：`..`（内部不再处理其他标记）
  s = s.replace(/`([^`\n]+)`/g, (_, c) => { codes.push(`<code>${c}</code>`); return `\u0001IC${codes.length - 1}\u0001`; });
  // 2) 粗体 / 斜体 / 删除线
  s = s.replace(/\*\*([^*\n]+)\*\*/g, '<b>$1</b>');
  s = s.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, '$1<i>$2</i>');
  s = s.replace(/~~([^~\n]+)~~/g, '<s>$1</s>');
  // 3) 链接：[文本](https?://...) — target=_blank + noopener（不做裸链接自动识别，避免嵌套 <a>）
  s = s.replace(/\[([^\]\n]+)\]\((https?:\/\/[^)\s]+)\)/g,
    '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  s = s.replace(/\u0001IC(\d+)\u0001/g, (_, i) => codes[+i]);
  return s;
}

/** 围栏代码块 → .codeblock HTML（内容 esc），返回 [剩余文本, 块HTML数组] */
function extractFences(src) {
  const blocks = [];
  let text = '';
  const parts = src.split('```');
  for (let i = 0; i < parts.length; i++) {
    if (i % 2 === 0) { text += parts[i]; continue; }
    // 奇数段 = 代码内容；首行可能是语言标注
    const body = parts[i].replace(/^[ \t]*\r?\n/, '');
    let lang = '';
    let code = body;
    const m = body.match(/^([A-Za-z0-9_+\-#.]{0,20})[ \t]*\r?\n/);
    if (m) { lang = m[1]; code = body.slice(m[0].length); }
    code = code.replace(/\n$/, ''); // 收 fence 尾行换行
    blocks.push(
      `<div class="codeblock"><div class="code-head"><span>${esc(lang) || 'text'}</span>` +
      `<button class="copy-btn" type="button">复制</button></div>` +
      `<pre><code>${esc(code)}</code></pre></div>`
    );
    text += PH(blocks.length - 1);
  }
  return [text, blocks];
}

const isTableSep = (line) => /^\|?[\s:|-]+\|[\s:|-]*$/.test(line) && /-/.test(line);

function tableRow(line) {
  let s = line.trim();
  if (s.startsWith('|')) s = s.slice(1);
  if (s.endsWith('|')) s = s.slice(0, -1);
  return s.split('|').map((c) => c.trim());
}

/** 已转义文本 → 块级 HTML */
function blocks(text) {
  const lines = text.split('\n');
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const raw = lines[i];
    const line = raw.trim();

    if (line === '') { i++; continue; }

    // 占位符独立成块
    if (/^\u0000CB\d+\u0000$/.test(line)) { out.push(line); i++; continue; }

    // 标题
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) { out.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`); i++; continue; }

    // 分隔线
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(line)) { out.push('<hr>'); i++; continue; }

    // 引用块
    if (line.startsWith('>')) {
      const buf = [];
      while (i < lines.length && lines[i].trim().startsWith('>')) {
        buf.push(lines[i].trim().replace(/^>\s?/, '')); i++;
      }
      out.push(`<blockquote>${inline(buf.join(' '))}</blockquote>`);
      continue;
    }

    // 表格：| a | b | + |---|---|
    if (line.startsWith('|') && i + 1 < lines.length && isTableSep(lines[i + 1].trim())) {
      const head = tableRow(line);
      i += 2;
      const rows = [];
      while (i < lines.length && lines[i].trim().startsWith('|')) {
        rows.push(tableRow(lines[i])); i++;
      }
      let html = '<table><thead><tr>' + head.map((c) => `<th>${inline(c)}</th>`).join('') + '</tr></thead><tbody>';
      for (const r of rows) {
        html += '<tr>' + head.map((_, k) => `<td>${inline(r[k] ?? '')}</td>`).join('') + '</tr>';
      }
      out.push(html + '</tbody></table>');
      continue;
    }

    // 无序 / 有序列表
    const ul = line.match(/^[-*+]\s+(.*)$/);
    const ol = line.match(/^\d+[.)]\s+(.*)$/);
    if (ul || ol) {
      const ordered = !!ol;
      const items = [];
      while (i < lines.length) {
        const t = lines[i].trim();
        const mu = t.match(/^[-*+]\s+(.*)$/);
        const mo = t.match(/^\d+[.)]\s+(.*)$/);
        if (ordered ? mo : mu) { items.push((ordered ? mo : mu)[1]); i++; }
        else if (/^\s{2,}\S/.test(lines[i]) && items.length && lines[i].trim() && !/^(#{1,6})\s|^>/.test(t)) {
          items[items.length - 1] += '<br>' + inline(t); i++; // 简单续行
        } else break;
      }
      const tag = ordered ? 'ol' : 'ul';
      out.push(`<${tag}>${items.map((x) => `<li>${inline(x)}</li>`).join('')}</${tag}>`);
      continue;
    }

    // 段落（连续非空行合并）
    const para = [];
    while (i < lines.length && lines[i].trim() !== '' && !/^(#{1,6})\s|^>|^[-*+]\s|^\d+[.)]\s|^\|/.test(lines[i].trim()) && !/^\u0000CB\d+\u0000$/.test(lines[i].trim())) {
      const t = lines[i].trim();
      if (/^(-{3,}|\*{3,}|_{3,})$/.test(t)) break;
      para.push(t); i++;
    }
    if (para.length) out.push(`<p>${para.map(inline).join('<br>')}</p>`);
    else { out.push(`<p>${inline(line)}</p>`); i++; } // 罕见兜底
  }
  return out.join('\n');
}

/** 渲染 markdown → HTML 字符串（内部全部经 esc 转义） */
export function mdRender(src) {
  if (!src) return '';
  const norm = String(src).replace(/\r\n/g, '\n').replace(/\r/g, '\n');
  const [plain, codeBlocks] = extractFences(norm);
  const escaped = esc(plain);
  let html = blocks(escaped);
  html = html.replace(/\u0000CB(\d+)\u0000/g, (_, i) => codeBlocks[+i] ?? '');
  return html;
}
