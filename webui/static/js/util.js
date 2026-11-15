// util.js — DOM 工具 / toast / 格式化（无依赖）
export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

/** HTML 转义（一切外部文本进入 DOM 前必经此处） */
export function esc(s) {
  return String(s ?? '')
    .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;').replaceAll("'", '&#39;');
}

/** DOM 构建器：el('div', {class:'x', onclick:fn, $text:'..', $html:'..'}, [children]) */
export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null) continue;
    if (k === '$text') node.textContent = v;
    else if (k === '$html') node.innerHTML = v; // 仅用于已转义内容
    else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
    else if (k === 'style' && typeof v === 'object') Object.assign(node.style, v);
    else node.setAttribute(k, v);
  }
  for (const c of [].concat(children)) {
    if (c == null || c === false) continue;
    node.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return node;
}

/* 内联 SVG 线条图标（UI chrome 统一风格；stroke=currentColor） */
const SVG = (d, vb = '0 0 24 24') =>
  `<svg viewBox="${vb}" width="1em" height="1em" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${d}"/></svg>`;
export const ICONS = {
  gear: SVG('M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Zm8.4-2.2.9-.7a1 1 0 0 0 .2-1.3l-1-1.7a1 1 0 0 0-1.2-.4l-1.1.4a7 7 0 0 0-1.2-.7l-.2-1.2a1 1 0 0 0-1-.8h-2a1 1 0 0 0-1 .8l-.2 1.2c-.4.2-.8.4-1.2.7l-1.1-.4a1 1 0 0 0-1.2.4l-1 1.7a1 1 0 0 0 .2 1.3l.9.7a7 7 0 0 0 0 1.4l-.9.7a1 1 0 0 0-.2 1.3l1 1.7a1 1 0 0 0 1.2.4l1.1-.4c.4.3.8.5 1.2.7l.2 1.2a1 1 0 0 0 1 .8h2a1 1 0 0 0 1-.8l.2-1.2c.4-.2.8-.4 1.2-.7l1.1.4a1 1 0 0 0 1.2-.4l1-1.7a1 1 0 0 0-.2-1.3l-.9-.7a7 7 0 0 0 0-1.4Z'),
  send: SVG('m3.5 11.5 17-8-8 17-2.4-6.6-6.6-2.4Zm6.6 2.4L20.5 3.5'),
  stop: SVG('M7 7h10v10H7z'),
  refresh: SVG('M20 12a8 8 0 1 1-2.3-5.6M20 4v4h-4'),
  download: SVG('M12 4v11m0 0-4-4m4 4 4-4M5 19h14'),
  upload: SVG('M12 15V4m0 0-4 4m4-4 4 4M5 19h14'),
  trash: SVG('M4 7h16M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2m-9 0 1 13h10l1-13M10 11v6m4-6v6'),
  folder: SVG('M3.5 7.5A1.5 1.5 0 0 1 5 6h4l2 2.5h8a1.5 1.5 0 0 1 1.5 1.5v8A1.5 1.5 0 0 1 19 19.5H5A1.5 1.5 0 0 1 3.5 18V7.5Z'),
  folderUp: SVG('M3.5 7.5A1.5 1.5 0 0 1 5 6h4l2 2.5h8a1.5 1.5 0 0 1 1.5 1.5v8A1.5 1.5 0 0 1 19 19.5H5A1.5 1.5 0 0 1 3.5 18V7.5Z M12 16v-5 m0 0-2.5 2.5M12 11l2.5 2.5'),
  file: SVG('M6 3.5h7.5L18.5 8.5V20a.5.5 0 0 1-.5.5H6a.5.5 0 0 1-.5-.5V4a.5.5 0 0 1 .5-.5Zm7.5 0V9H18 M8.5 13h7M8.5 16.5h5'),
  close: SVG('M6 6l12 12M18 6 6 18'),
  eye: SVG('M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Zm9.5 2.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Z'),
  eyeOff: SVG('M4 4l16 16M9.9 5.9A9.5 9.5 0 0 1 21.5 12S18 18.5 12 18.5c-1.5 0-2.9-.4-4.1-1M6.3 7.2C4 8.9 2.5 12 2.5 12S6 5.5 12 5.5M9.9 10.1a2.5 2.5 0 0 0 3.5 3.5'),
  plus: SVG('M12 5v14M5 12h14'),
  search: SVG('M10.5 17.5a7 7 0 1 0 0-14 7 7 0 0 0 0 14Zm5-2 5 5'),
  chevron: SVG('m9 6 6 6-6 6'),
  sparkle: SVG('M12 3.5l1.9 5.1 5.1 1.9-5.1 1.9L12 17.5l-1.9-5.1L5 10.5l5.1-1.9L12 3.5ZM19 3v3m1.5 1.5h-3'),
  copy: SVG('M8 8V5.5A1.5 1.5 0 0 1 9.5 4h9A1.5 1.5 0 0 1 20 5.5v9a1.5 1.5 0 0 1-1.5 1.5H16M4 9.5A1.5 1.5 0 0 1 5.5 8h9A1.5 1.5 0 0 1 16 9.5v9a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 4 18.5v-9Z'),
  sparkleList: SVG('M12 3.5l1.9 5.1 5.1 1.9-5.1 1.9L12 17.5l-1.9-5.1L5 10.5l5.1-1.9L12 3.5ZM4.5 19.5h9'),
  check: SVG('M4.5 12.5 10 18 19.5 6.5'),
};
export const icon = (name, cls = '') => {
  const s = document.createElement('span');
  s.className = `icn ${cls}`;
  s.innerHTML = ICONS[name] || '';
  return s;
};

/** 字节格式化 */
export function fmtBytes(n) {
  n = Number(n);
  if (!Number.isFinite(n) || n < 0) return '—';
  if (n < 1024) return n + ' B';
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
  return (n / 1024 / 1024).toFixed(2) + ' MB';
}

/** MM-DD HH:mm */
export function fmtTime(ts) {
  const d = new Date(ts);
  if (isNaN(d)) return '';
  const p = (x) => String(x).padStart(2, '0');
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

export function debounce(fn, ms = 200) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

/** 中文 toast（右上角） */
export function toast(msg, kind = 'info', ms = 3200) {
  const box = $('#toasts');
  const t = el('div', { class: `toast ${kind === 'ok' ? 'ok' : kind === 'err' ? 'err' : 'info'}` },
    [el('span', { $text: String(msg) })]);
  box.append(t);
  setTimeout(() => { t.style.opacity = '0'; t.style.transition = 'opacity .3s'; setTimeout(() => t.remove(), 320); }, ms);
}

/** 把任意异常转成中文可读消息 */
export function fmtErr(e) {
  if (!e) return '未知错误';
  if (e.name === 'AbortError') return '请求已取消';
  if (e.name === 'TimeoutError') return '请求超时，请检查服务是否响应';
  if (e instanceof ApiError) return e.message;
  if (e instanceof TypeError && /fetch|network/i.test(e.message || '')) return '网络连接失败，请确认服务已启动';
  return e.message || String(e);
}

/** API 层错误（携带状态码） */
export class ApiError extends Error {
  constructor(message, status = 0) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

/** 弹出一个文本查看 modal */
export function showTextModal(title, path, content, size) {
  const close = () => back.remove();
  const back = el('div', { class: 'modal-back', onclick: (ev) => { if (ev.target === back) close(); } });
  back.append(el('div', { class: 'modal' }, [
    el('div', { class: 'modal-head' }, [
      el('span', { $text: title }),
      el('span', { class: 'path', $text: path }),
      size != null ? el('span', { class: 'chip dim', $text: fmtBytes(size) }) : null,
      el('button', { class: 'btn sm ghost x', onclick: () => close() }, ['✕ 关闭']),
    ]),
    el('pre', { class: 'body mono', $text: content }),
  ]));
  document.body.append(back);
  return close;
}
