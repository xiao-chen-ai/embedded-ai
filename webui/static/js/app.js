// app.js — 启动 / 顶栏状态 / 模型设置弹窗（DSH 风 provider 行 + 手风琴 + 密钥管理 + 模型发现）/ 外观与工具路径设置
import { $, $$, el, esc, toast, fmtErr, icon, ICONS } from './util.js';
import { store, setState, onChange } from './store.js';
import { apiHealth, apiConfig, apiSetModel, apiSetCredentials, apiAddModel, apiAddProvider, apiDiscoverModels } from './api.js';
import { apiUiSettings, apiPutUiSettings, apiToolDetect } from './api.js';
import './chat.js'; // 聊天页（内部装配文件树）

const HW_TOOLS = new Set(['analyze_schematic', 'generate_pinmap']);

/* ---------------- 主题与 UI 设置 ---------------- */
const DEFAULT_THEME = { primary: '#6366f1', secondary: '#8b5cf6', accent: '#2dd4bf' };
const PRESETS = {
  violet: DEFAULT_THEME,
  amber: { primary: '#f59e0b', secondary: '#ef4444', accent: '#06b6d4' },
  green: { primary: '#10b981', secondary: '#06b6d4', accent: '#f59e0b' },
  rose: { primary: '#f43f5e', secondary: '#ec4899', accent: '#8b5cf6' },
};
const BRAND = '小臣-AI'; // 品牌名锁定（设置界面不提供修改）
const HEX_RE = /^#[0-9a-fA-F]{6}$/;
const normHex = (s) => {
  const t = String(s || '').trim();
  return HEX_RE.test(t) ? t.toLowerCase() : null;
};

/** 注入 CSS 变量（页面立即整体换色） */
export function applyTheme(theme) {
  const t = {
    primary: normHex(theme?.primary) || DEFAULT_THEME.primary,
    secondary: normHex(theme?.secondary) || DEFAULT_THEME.secondary,
    accent: normHex(theme?.accent) || DEFAULT_THEME.accent,
  };
  const root = document.documentElement.style;
  root.setProperty('--primary', t.primary);
  root.setProperty('--secondary', t.secondary);
  root.setProperty('--accent', t.accent);
  return t;
}

/** 品牌应用：顶栏 + 标签页标题（品牌名锁定 小臣-AI） */
function applyBrand(brand) {
  const name = BRAND; // 后端返回值不再用于展示，恒定
  $('#brandName').textContent = name;
  document.title = `${name} · 嵌入式智能开发助手`;
}

/** 填充外观表单（品牌不允许改，无输入框） */
function fillAppearanceForm(s) {
  const t = { ...DEFAULT_THEME, ...(s?.theme || {}) };
  $('#thPrimary').value = t.primary; $('#thPrimaryHex').value = t.primary;
  $('#thSecondary').value = t.secondary; $('#thSecondaryHex').value = t.secondary;
  $('#thAccent').value = t.accent; $('#thAccentHex').value = t.accent;
}

/** 填充工具路径表单 */
function fillToolPaths(s) {
  const tp = s?.tool_paths || {};
  $$('.tp-row').forEach((row) => {
    const input = row.querySelector('.tp-input');
    input.value = tp[row.dataset.tool] || '';
    const st = row.querySelector('.tp-status');
    st.textContent = input.value ? '✓ 已配置' : '';
    st.className = 'tp-status ok';
    if (!input.value) { st.textContent = ''; }
  });
}

export async function loadUiSettings(silent = false) {
  try {
    const s = await apiUiSettings();
    setState({ uiSettings: s });
    applyTheme(s?.theme);
    applyBrand(s?.brand);
    fillAppearanceForm(s);
    fillToolPaths(s);
  } catch (e) {
    if (!silent) toast(`读取界面设置失败：${fmtErr(e)}`, 'err');
  }
}

/** 收集当前表单 → 完整 settings 对象（PUT 全量；brand 恒为锁定的品牌名） */
function collectSettings() {
  const theme = {
    primary: $('#thPrimaryHex').value.trim(),
    secondary: $('#thSecondaryHex').value.trim(),
    accent: $('#thAccentHex').value.trim(),
  };
  const tool_paths = {};
  $$('.tp-row').forEach((row) => { tool_paths[row.dataset.tool] = row.querySelector('.tp-input').value.trim(); });
  return { brand: BRAND, theme, tool_paths };
}

/** 保存外观 → PUT；成功后立即应用颜色 + 品牌 */
async function saveAppearance() {
  const setStatus = (m, c) => { $('#appearanceStatus').textContent = m || ''; $('#appearanceStatus').className = `cred-status ${c || ''}`; };
  const body = collectSettings();
  for (const [k, v] of Object.entries(body.theme)) {
    if (!normHex(v)) return setStatus(`${k} 颜色须为 #RRGGBB 格式`, 'bad');
  }
  const btn = $('#btnAppearanceSave');
  btn.disabled = true; btn.textContent = '保存中…';
  try {
    const r = await apiPutUiSettings(body);
    const s = r?.settings || body;
    setState({ uiSettings: s });
    applyTheme(s.theme); applyBrand(s.brand); fillAppearanceForm(s); fillToolPaths(s);
    setStatus('✓ 已保存并应用', 'ok');
    toast('外观设置已保存', 'ok');
  } catch (e) {
    setStatus(`保存失败：${fmtErr(e)}`, 'bad');
    toast(`保存失败：${fmtErr(e)}`, 'err', 5000);
  } finally { btn.disabled = false; btn.textContent = '保存'; }
}

/** 工具路径探测（custom_path 优先，空则全盘）。label 为后端中文名（一键探测结果回填用） */
function setTpStatus(row, text, cls) {
  const st = row.querySelector('.tp-status');
  st.textContent = text || '';
  st.className = `tp-status ${cls || ''}`;
}
function fillTpRow(row, { found, path, hint }) {
  const input = row.querySelector('.tp-input');
  if (found && path) {
    input.value = path;
    setTpStatus(row, `✓ ${path}`, 'ok');
  } else {
    setTpStatus(row, `✕ ${hint || '未找到，可手动填写'}`, 'bad muted');
  }
}
async function detectTool(row) {
  const btn = row.querySelector('.tp-detect');
  const input = row.querySelector('.tp-input');
  btn.disabled = true;
  const label = btn.innerHTML;
  btn.textContent = '探测中…';
  setTpStatus(row, '正在扫描常见位置…', 'warn');
  try {
    const r = await apiToolDetect(row.dataset.tool, input.value);
    if (r?.found && r?.path) {
      fillTpRow(row, r);
      toast(`已找到 ${row.dataset.tool}: ${r.path}`, 'ok', 4000);
    } else {
      renderDownloadHint(row, r);
      if (!row.querySelector('.dl-box')) setTpStatus(row, `✕ ${r?.hint || '未找到，可手动填写'}`, 'bad muted');
      toast(`${row.dataset.tool} 未找到：${r?.hint || '请手动填写'}`, 'err', 5000);
    }
  } catch (e) {
    setTpStatus(row, `✕ ${fmtErr(e)}`, 'bad');
    toast(`探测失败：${fmtErr(e)}`, 'err', 5000);
  } finally {
    btn.disabled = false;
    btn.innerHTML = label;
    hydrateIcons(btn);
  }
}

/* ---------------- 磁盘浏览 modal（工具路径「浏览」按钮） ---------------- */
let fsCtx = { row: null, path: '', parent: '', drives: [], selected: null }; // selected: {name, dir}

function fsJoin(dir, name) {
  const d = (dir || '').replace(/[\\/]+$/, '');
  if (!d) return name;
  return `${d}\\${name}`;
}

function fsOpen(row) {
  fsCtx.row = row;
  fsCtx.selected = null;
  const input = row.querySelector('.tp-input');
  // 初始路径：当前输入值所在目录（或其本身位置），否则从盘符列表开始
  const cur = (input.value || '').trim();
  const startDir = cur ? cur.replace(/[\\/][^\\/]+$/, '') : '';
  hydrateIcons($('#fsModal'));
  $('#fsModal').classList.remove('hidden');
  $('#fsSelected').textContent = '';
  $('#fsPick').disabled = true;
  fsLoad(startDir);
}

function fsClose() { $('#fsModal').classList.add('hidden'); fsCtx.row = null; }

async function fsLoad(path, keepManual) {
  const listBox = $('#fsList');
  $('#fsPathInput').value = path || (keepManual ? $('#fsPathInput').value : '');
  listBox.innerHTML = '';
  listBox.append(el('div', { class: 'state', style: { padding: '18px' } }, [el('span', { class: 'spinner' }), el('span', {}, ['正在读取目录…'])]));
  try {
    const r = await apiFsBrowse(path || '');
    fsCtx.path = r?.path || path || '';
    fsCtx.parent = r?.parent || '';
    const entries = Array.isArray(r?.entries) ? r.entries : [];
    renderDrives(entries);
    renderCrumbs();
    $('#fsPathInput').value = fsCtx.path;
    $('#fsUp').disabled = !fsCtx.parent;
    listBox.innerHTML = '';
    if (!entries.length) {
      listBox.append(el('div', { class: 'picker-empty' }, ['（空目录）']));
    }
    // 文件夹在前，其余按名称
    const sorted = [...entries].sort(cmpFsEntry);
    for (const e of sorted) {
      if (e.dir) {
        listBox.append(el('div', {
          class: 'fs-item dir', title: e.name,
          onclick: () => fsLoad(fsJoin(fsCtx.path, e.name)),
          ondblclick: () => fsLoad(fsJoin(fsCtx.path, e.name)),
        }, [icon('folder'), el('span', { class: 'fs-name', $text: e.name })]));
      } else {
        listBox.append(el('div', {
          class: 'fs-item file', title: e.name,
          onclick: (ev) => {
            $$('.fs-item.file.sel').forEach((x) => x.classList.remove('sel'));
            ev.currentTarget.classList.add('sel');
            fsCtx.selected = e;
            $('#fsSelected').textContent = fsJoin(fsCtx.path, e.name);
            $('#fsPick').disabled = false;
          },
          ondblclick: () => fsPickNow(e),
        }, [icon('file'), el('span', { class: 'fs-name', $text: e.name })]));
      }
    }
  } catch (e) {
    listBox.innerHTML = '';
    listBox.append(el('div', { class: 'picker-empty' }, [`读取失败：${fmtErr(e)}`]));
  }
}

function cmpFsEntry(a, b) {
  if (a.dir !== b.dir) return a.dir ? -1 : 1;
  return String(a.name).localeCompare(String(b.name), 'zh-Hans-CN', { numeric: true });
}

/** entries 为盘符列表（dir:true，name 如 "C:\"）时填充盘符下拉 */
function renderDrives(entries) {
  const sel = $('#fsDrive');
  const drives = entries.filter((e) => e.dir && /^[A-Za-z]:[\\/]?$/.test(e.name || ''));
  if (!drives.length) return; // 普通目录浏览不重绘
  sel.innerHTML = '';
  for (const d of drives) {
    sel.append(el('option', { value: d.name.replace(/[\\/]+$/, '') }, [d.name]));
  }
}
function renderCrumbs() {
  const box = $('#fsCrumbs');
  box.innerHTML = '';
  if (!fsCtx.path) { box.textContent = '此电脑（选择一个盘符）'; return; }
  const parts = fsCtx.path.split(/[\\/]+/).filter(Boolean);
  let acc = '';
  for (const p of parts) {
    if (/^[A-Za-z]:$/.test(p)) {
      acc = p;
      box.append(el('span', { class: 'crumb', onclick: () => fsLoad(p + '\\') }, [`${p}\\`]));
    } else {
      acc = fsJoin(acc, p);
      box.append(el('span', { class: 'crumb-sep' }, ['›']));
      const target = acc;
      box.append(el('span', { class: 'crumb', onclick: () => fsLoad(target) }, [p]));
    }
  }
}

function fsPickNow(entry) {
  const full = fsJoin(fsCtx.path, (entry || fsCtx.selected)?.name || '');
  if (!full) return;
  if (fsCtx.row) fsCtx.row.querySelector('.tp-input').value = full;
  toast(`已选定：${full}`, 'ok');
  fsClose();
}

function bindFsModal() {
  $('#fsClose').addEventListener('click', fsClose);
  $('#fsModal').addEventListener('click', (ev) => { if (ev.target.id === 'fsModal') fsClose(); });
  $('#fsUp').addEventListener('click', () => { if (fsCtx.parent) fsLoad(fsCtx.parent); });
  $('#fsGo').addEventListener('click', () => fsLoad($('#fsPathInput').value.trim()));
  $('#fsPathInput').addEventListener('keydown', (ev) => { if (ev.key === 'Enter') { ev.preventDefault(); fsLoad($('#fsPathInput').value.trim()); } });
  $('#fsDrive').addEventListener('change', () => { const v = $('#fsDrive').value; if (v) fsLoad(v + '\\'); });
  $('#fsPick').addEventListener('click', () => fsPickNow());
}

/** 一键探测全部：POST {all:true} → 逐行回填（found 绿✓，未 found 灰 hint + 下载指引） */
async function detectAllTools() {
  const btn = $('#btnDetectAll');
  const st = $('#toolPathsStatus');
  btn.disabled = true;
  const label = btn.innerHTML;
  btn.innerHTML = '<span class="spinner" style="width:13px;height:13px;border-width:2px"></span> 探测中…';
  st.textContent = '正在扫描本机全部工具（常见安装位置 + PATH）…'; st.className = 'cred-status warn';
  $$('.tp-row').forEach((row) => setTpStatus(row, '…', 'warn'));
  try {
    const r = await apiToolDetectAll();
    const tools = Array.isArray(r?.tools) ? r.tools : [];
    const rows = new Map($$('.tp-row').map((row) => [row.dataset.tool, row]));
    let foundN = 0;
    for (const t of tools) {
      const row = rows.get(t.tool);
      if (!row) continue;
      fillTpRow(row, t);
      if (t.found) foundN++;
      else renderDownloadHint(row, t);
    }
    st.textContent = `探测完成：找到 ${foundN} / ${tools.length || rows.size} 个工具，点「保存」持久化`;
    st.className = 'cred-status ok';
    toast(`一键探测完成：找到 ${foundN} 个工具`, foundN ? 'ok' : 'info', 4500);
  } catch (e) {
    st.textContent = `探测失败：${fmtErr(e)}`;
    st.className = 'cred-status bad';
    toast(`一键探测失败：${fmtErr(e)}`, 'err', 5000);
    $$('.tp-row').forEach((row) => setTpStatus(row, '', ''));
  } finally {
    btn.disabled = false;
    btn.innerHTML = label;
    hydrateIcons(btn);
  }
}

/** 未找到工具的下载指引：「📦 打开 {name} 下载页 ↗」+ note 小字（openocd 等） */
function renderDownloadHint(row, t) {
  const st = row.querySelector('.tp-status');
  const dl = t?.download;
  if (!dl?.url) return;
  const link = el('a', { href: dl.url, target: '_blank', rel: 'noopener noreferrer', class: 'dl-link' },
    [`📦 打开 ${dl.name || '官方'} 下载页 ↗`]);
  st.replaceChildren(el('div', { class: 'dl-box' }, [
    el('div', { class: 'tp-status bad muted', $text: t.hint || '未找到' }),
    link,
    dl.note ? el('div', { class: 'dl-note', $text: dl.note }) : null,
  ]));
}

/** 保存工具路径 */
async function saveToolPaths() {
  const setStatus = (m, c) => { $('#toolPathsStatus').textContent = m || ''; $('#toolPathsStatus').className = `cred-status ${c || ''}`; };
  const body = collectSettings();
  const btn = $('#btnToolPathsSave');
  btn.disabled = true; btn.textContent = '保存中…';
  try {
    const r = await apiPutUiSettings(body);
    const s = r?.settings || body;
    setState({ uiSettings: s });
    applyTheme(s.theme); applyBrand(s.brand); fillToolPaths(s);
    setStatus('✓ 已保存', 'ok');
    toast('工具路径已保存，聊天时 AI 将直接使用', 'ok');
  } catch (e) {
    setStatus(`保存失败：${fmtErr(e)}`, 'bad');
    toast(`保存失败：${fmtErr(e)}`, 'err', 5000);
  } finally { btn.disabled = false; btn.textContent = '保存'; }
}

/** 外观表单联动：color ↔ hex ↔ 实时预览 */
function bindAppearance() {
  const pairs = [['#thPrimary', '#thPrimaryHex'], ['#thSecondary', '#thSecondaryHex'], ['#thAccent', '#thAccentHex']];
  for (const [cSel, hexSel] of pairs) {
    const c = $(cSel), hx = $(hexSel);
    c.addEventListener('input', () => {
      if (normHex(c.value)) hx.value = c.value.toUpperCase();
      previewAppearance();
    });
    hx.addEventListener('input', () => {
      const v = normHex(hx.value);
      if (v) { c.value = v; previewAppearance(); }
    });
    hx.addEventListener('blur', () => { if (!normHex(hx.value)) hx.value = c.value.toUpperCase(); });
  }
  // 预设主题：填表 + 立即预览
  $$('.preset-btn').forEach((b) => b.addEventListener('click', () => {
    const t = PRESETS[b.dataset.theme] || DEFAULT_THEME;
    $('#thPrimary').value = t.primary; $('#thPrimaryHex').value = t.primary.toUpperCase();
    $('#thSecondary').value = t.secondary; $('#thSecondaryHex').value = t.secondary.toUpperCase();
    $('#thAccent').value = t.accent; $('#thAccentHex').value = t.accent.toUpperCase();
    previewAppearance();
    toast(`已切换「${b.textContent.trim()}」预览，记得点「保存」持久化`, 'info', 3500);
  }));
  $('#btnThemeReset').addEventListener('click', () => {
    const t = DEFAULT_THEME;
    $('#thPrimary').value = t.primary; $('#thPrimaryHex').value = t.primary.toUpperCase();
    $('#thSecondary').value = t.secondary; $('#thSecondaryHex').value = t.secondary.toUpperCase();
    $('#thAccent').value = t.accent; $('#thAccentHex').value = t.accent.toUpperCase();
    previewAppearance();
  });
  $('#btnAppearanceSave').addEventListener('click', saveAppearance);
}
function previewAppearance() {
  const t = {
    primary: normHex($('#thPrimaryHex').value) || DEFAULT_THEME.primary,
    secondary: normHex($('#thSecondaryHex').value) || DEFAULT_THEME.secondary,
    accent: normHex($('#thAccentHex').value) || DEFAULT_THEME.accent,
  };
  applyTheme(t);
}

/* ---------------- 图标注水（HTML 里的 data-icn 占位） ---------------- */
function hydrateIcons(root = document) {
  root.querySelectorAll('.icn[data-icn]').forEach((n) => {
    n.innerHTML = ICONS[n.dataset.icn] || '';
    delete n.dataset.icn;
  });
}
hydrateIcons();

/* ---------------- 设置弹窗 ---------------- */
export function openSettings() {
  hydrateIcons($('#settingsModal'));
  $('#settingsModal').classList.remove('hidden');
}
function closeSettings() { $('#settingsModal').classList.add('hidden'); }
$('#btnSettings').addEventListener('click', openSettings);
$('#btnCloseSettings').addEventListener('click', closeSettings);
$('#settingsModal').addEventListener('click', (ev) => { if (ev.target.id === 'settingsModal') closeSettings(); });
document.addEventListener('keydown', (ev) => {
  if (ev.key !== 'Escape') return;
  closeSettings();
  closePicker();
  fsClose();
});

/* ---------------- 头部 / 健康状态 ---------------- */
function renderConn() {
  const h = store.health;
  const dot = $('#connDot'), txt = $('#connText');
  if (!h) { dot.className = 'dot bad'; txt.textContent = '服务未连接'; return; }
  if (h.ok) { dot.className = 'dot ok'; txt.textContent = '服务在线'; }
  else { dot.className = 'dot warn'; txt.textContent = '服务异常'; }
  $('#verText').textContent = h.version ? `v${h.version}` : '';
}

function renderModelSel() {
  const sel = $('#modelSel');
  const models = store.config?.models || {};
  const active = store.activeModel;
  sel.innerHTML = '';
  const keys = Object.keys(models);
  if (!keys.length) { sel.append(el('option', { value: '' }, ['（无可用模型）'])); sel.disabled = true; return; }
  sel.disabled = false;
  for (const k of keys) {
    const m = models[k] || {};
    const noKey = m.api_key_set === false || m.configured === false;
    const label = `${k}${k === active ? ' ✓' : ''}${noKey ? '（未配置）' : ''}`;
    sel.append(el('option', { value: k, selected: k === active }, [label]));
  }
}

export async function loadHealth(silent = false) {
  try {
    const h = await apiHealth();
    setState({ health: h });
  } catch (e) {
    setState({ health: null });
    if (!silent) toast(`健康检查失败：${fmtErr(e)}`, 'err');
  }
  renderConn();
}

export async function loadConfig(silent = false) {
  try {
    const c = await apiConfig();
    if (!c || typeof c !== 'object' || !c.models) throw new Error('配置响应缺少 models 字段');
    setState({ config: c, activeModel: c.active_model || '' });
    renderModelSel();
    renderModels();
  } catch (e) {
    if (!silent) toast(`读取配置失败：${fmtErr(e)}`, 'err');
    const listWrap = $('#providerList');
    if (listWrap) {
      listWrap.innerHTML = '';
      listWrap.append(el('div', { class: 'state error' }, [
        el('span', { class: 'glyph' }, ['!!']), el('span', { $text: `模型配置加载失败：${fmtErr(e)}` }),
        el('button', { class: 'btn sm retry', onclick: () => initLoad() }, ['重试']),
      ]));
    }
  }
}

async function initLoad() {
  renderConn();
  await Promise.all([loadHealth(false), loadConfig(false), loadUiSettings(false)]);
}

/* ---------------- 快速切模型（顶栏胶囊下拉） ---------------- */
async function onModelChange() {
  const sel = $('#modelSel');
  const key = sel.value;
  if (!key || key === store.activeModel) return;
  sel.disabled = true;
  try {
    await apiSetModel(key);
    setState({ activeModel: key });
    toast(`已切换模型：${key}`, 'ok');
  } catch (e) {
    toast(`切换模型失败：${fmtErr(e)}`, 'err');
  } finally {
    sel.disabled = false;
    renderModelSel();
    renderModels();
  }
}
$('#modelSel').addEventListener('change', onModelChange);

/* ---------------- 模型发现（POST /api/config/discover-models） ---------------- */
/** 从 provider 编辑卡/添加表单收集探测参数：表单里有未保存的 key/url 就用表单值，否则用 provider 存档 */
function discoverPayload({ provider, keyVal, urlVal, protocol }) {
  const key = (keyVal || '').trim();
  const url = (urlVal || '').trim();
  if (key || url) {
    const body = {};
    if (url) body.base_url = url;
    if (key) body.api_key = key;
    body.protocol = protocol || 'openai_compatible';
    return body;
  }
  return provider ? { provider } : null;
}

async function runDiscover(payload, onPick) {
  if (!payload) { toast('请先填写 API 密钥或 Base URL，或选择已有提供商', 'info'); return; }
  toast('正在获取模型列表…', 'info', 2200);
  try {
    const r = await apiDiscoverModels(payload);
    const models = (r?.models || []).map((m) => (typeof m === 'string' ? m : m?.id || '')).filter(Boolean);
    if (!models.length) { toast('该 API 未返回任何模型', 'info'); return; }
    openPicker(models, onPick);
  } catch (e) {
    toast(`获取失败：${fmtErr(e)}`, 'err', 6000);
  }
}

/* ---------------- 模型选择弹窗 ---------------- */
let pickerPick = null;
let currentPickerModels = [];
export function openPicker(models, onPick) {
  const modal = $('#pickerModal');
  hydrateIcons(modal);
  currentPickerModels = models;
  $('#pickerCount').textContent = `${models.length} 个`;
  $('#pickerSearch').value = '';
  renderPickList(models, '');
  pickerPick = onPick;
  modal.classList.remove('hidden');
  $('#pickerSearch').focus();
}
function closePicker() { $('#pickerModal').classList.add('hidden'); pickerPick = null; }
function renderPickList(models, q) {
  const box = $('#pickerList');
  box.innerHTML = '';
  const list = q ? models.filter((m) => m.toLowerCase().includes(q.toLowerCase())) : models;
  if (!list.length) {
    box.append(el('div', { class: 'picker-empty' }, [q ? '没有匹配的模型' : '列表为空']));
    return;
  }
  for (const id of list) {
    box.append(el('div', { class: 'pick-item', $text: id, onclick: () => { pickerPick?.(id); closePicker(); } }));
  }
}
$('#pickerClose').addEventListener('click', closePicker);
$('#pickerModal').addEventListener('click', (ev) => { if (ev.target.id === 'pickerModal') closePicker(); });
$('#pickerSearch').addEventListener('input', () => {
  renderPickList(currentPickerModels, $('#pickerSearch').value);
});

/* ---------------- provider 行列表 + 手风琴 ---------------- */
let openProvider = null;
const provUI = new Map();

function normModels() {
  const models = store.config?.models || {};
  return Object.entries(models).map(([key, m]) => {
    m = m || {};
    const keyState = typeof m.api_key_set === 'boolean' ? m.api_key_set
      : typeof m.configured === 'boolean' ? m.configured : null;
    const client = String(m.provider || '');
    const isLocal = /ollama/i.test(client) || /localhost|127\.0\.0\.1/.test(String(m.base_url || ''));
    return {
      key: m.model_key || key, model: m.model || '',
      providerKey: m.provider_key || client || '(unknown)', client,
      base_url: m.base_url || '', api_key_set: keyState, local: isLocal,
    };
  });
}

const isLocalProvider = (g) => g.models.some((m) => m.local) || /ollama/i.test(g.client || '') || /ollama/i.test(g.name);

export function renderModels() {
  const listWrap = $('#providerList');
  if (!listWrap) return;
  const list = normModels();
  const active = store.activeModel;

  $('#wsActiveModel').textContent = active || '（未选择）';
  if (store.config?.workspace) $('#wsPath').textContent = store.config.workspace;
  $('#wsToolCount').textContent = `工具 ${(store.config?.tools || []).length} 个`;

  listWrap.innerHTML = '';
  provUI.clear();

  if (!list.length) {
    listWrap.append(el('div', { class: 'state' }, [
      el('span', { class: 'glyph' }, ['□']), el('span', {}, ['没有配置任何模型——用上方「添加模型提供商」或编辑 trae_config.yaml']),
    ]));
  }

  const byProvider = new Map();
  for (const m of list) {
    if (!byProvider.has(m.providerKey)) byProvider.set(m.providerKey, { name: m.providerKey, models: [], urls: new Set(), clients: new Set() });
    const g = byProvider.get(m.providerKey);
    g.models.push(m);
    if (m.base_url) g.urls.add(m.base_url);
    if (m.client) g.clients.add(m.client);
  }
  const topProviders = Array.isArray(store.config?.providers) ? store.config.providers : null;
  if (topProviders) {
    for (const p of topProviders) {
      const name = typeof p === 'string' ? p : p?.name;
      if (name && !byProvider.has(name)) byProvider.set(name, { name, models: [], urls: new Set(), clients: new Set(), empty: true });
    }
  }

  renderAdPviders([...byProvider.keys()]);

  for (const g of byProvider.values()) {
    const row = providerRow(g, active);
    const card = providerEditCard(g, active);
    listWrap.append(row, card);
    provUI.set(g.name, { row, card });
    if (g.name === openProvider) { card.classList.remove('hidden'); row.classList.add('open'); }
  }

  renderToolChips(Array.isArray(store.config?.tools) ? store.config.tools : []);
}

function toggleCard(provider) {
  const ui = provUI.get(provider);
  if (!ui) return;
  const willOpen = ui.card.classList.contains('hidden');
  if (!willOpen) {
    ui.card.classList.add('hidden');
    ui.row.classList.remove('open');
    openProvider = null;
    return;
  }
  for (const [name, other] of provUI) {
    if (name !== provider && !other.card.classList.contains('hidden')) {
      if (cardHasDraft(other)) {
        if (!confirm('当前编辑卡有未保存的修改，切换将保留草稿（再次展开可继续编辑）。确定切换吗？')) return;
      }
      other.card.classList.add('hidden');
      other.row.classList.remove('open');
    }
  }
  openProvider = provider;
  ui.card.classList.remove('hidden');
  ui.row.classList.add('open');
  ui.card.querySelector('.cred-key')?.focus();
}

function cardHasDraft(ui) {
  const key = ui.card.querySelector('.cred-key')?.value?.trim();
  const url = ui.card.querySelector('.cred-url')?.value?.trim();
  const mk = ui.card.querySelector('.mk-input')?.value?.trim();
  const mn = ui.card.querySelector('.mn-input')?.value?.trim();
  return !!(key || mk || mn) || !!(url && url !== (ui.card._origUrl || ''));
}

function hostOf(url) {
  try { const u = new URL(url); return u.host; } catch { return url; }
}

/** 提供商行 */
function providerRow(g, active) {
  const local = isLocalProvider(g);
  const keyOk = g.models.some((m) => m.api_key_set === true);
  const keyBad = !local && g.models.length > 0 && g.models.every((m) => m.api_key_set === false);
  const inUse = g.models.some((m) => m.key === active);
  const noModels = g.models.length === 0;

  const dotCls = local ? 'local' : keyOk ? 'ok' : keyBad ? 'bad' : 'warn';
  const dotTitle = local ? '本地服务' : keyOk ? 'API Key 已配置' : keyBad ? '缺少 API Key' : noModels ? '无模型（Key 状态未知）' : 'Key 状态未知';
  const endpoint = g.urls.size === 1 ? hostOf([...g.urls][0]) : g.urls.size > 1 ? `${g.urls.size} 个端点` : '—';

  const editBtn = el('button', { class: 'btn sm ghost' }, ['编辑', icon('chevron')]);
  editBtn.addEventListener('click', (ev) => { ev.stopPropagation(); toggleCard(g.name); });

  let delTimer = null;
  const delBtn = el('button', { class: 'btn sm ghost danger-icon', title: '删除提供商' }, [icon('trash')]);
  delBtn.addEventListener('click', (ev) => {
    ev.stopPropagation();
    if (delBtn.dataset.confirm) {
      delBtn.innerHTML = ''; delBtn.append(icon('trash'));
      delete delBtn.dataset.confirm;
      clearTimeout(delTimer);
      toast('后端暂未提供删除提供商的接口：请在 trae_config.yaml 中删除该条目及引用它的 models，保存后重启服务。', 'info', 6500);
      return;
    }
    delBtn.dataset.confirm = '1';
    delBtn.textContent = '确认删除？';
    delBtn.classList.add('confirming');
    delTimer = setTimeout(() => {
      delBtn.innerHTML = ''; delBtn.append(icon('trash'));
      delBtn.classList.remove('confirming'); delete delBtn.dataset.confirm;
    }, 3000);
  });

  const protoTxt = [...(g.clients || [])].join('/') || (local ? 'ollama' : '');

  return el('div', { class: `prov-row${inUse ? ' inuse' : ''}`, onclick: () => toggleCard(g.name) }, [
    el('span', { class: `dot ${dotCls}`, title: dotTitle }),
    el('span', { class: 'pr-name mono', $text: g.name }),
    local ? el('span', { class: 'chip dim' }, ['本地']) : null,
    protoTxt ? el('span', { class: 'chip mono dim', title: '协议（client_type）', $text: protoTxt }) : null,
    keyBad ? el('span', { class: 'chip red' }, ['缺 Key']) : null,
    noModels ? el('span', { class: 'chip dim' }, ['无模型']) : null,
    inUse ? el('span', { class: 'chip cyan' }, ['使用中']) : null,
    el('span', { class: 'pr-endpoint mono', $text: noModels ? '尚未挂载模型' : `${endpoint} · ${g.models.length} 模型` }),
    el('span', { class: 'pr-actions' }, [editBtn, delBtn]),
  ]);
}

/** 编辑卡 */
function providerEditCard(g, active) {
  const local = isLocalProvider(g);
  const keySaved = g.models.some((m) => m.api_key_set === true);
  const origUrl = g.urls.size ? [...g.urls][0] : '';
  const proto = [...(g.clients || [])][0] || 'openai_compatible';

  /* API 密钥 */
  const keyInput = el('input', {
    type: 'password', class: 'cred-key', spellcheck: 'false', autocomplete: 'new-password',
    placeholder: local ? '本地服务无需密钥' : keySaved ? '已保存（输入以更换）' : '粘贴 API 密钥',
  });
  const eyeBtn = el('button', { class: 'eye-btn', type: 'button', title: '显示/隐藏密钥', hidden: '' }, [icon('eye')]);
  keyInput.addEventListener('input', () => {
    eyeBtn.hidden = !keyInput.value;
    if (!keyInput.value) { keyInput.type = 'password'; eyeBtn.innerHTML = ''; eyeBtn.append(icon('eye')); }
  });
  eyeBtn.addEventListener('click', () => {
    const show = keyInput.type === 'password';
    keyInput.type = show ? 'text' : 'password';
    eyeBtn.innerHTML = ''; eyeBtn.append(icon(show ? 'eyeOff' : 'eye'));
    keyInput.focus();
  });

  const status = el('div', { class: 'cred-status' });
  const setStatus = (msg, cls) => { status.textContent = msg || ''; status.className = `cred-status ${cls || ''}`; };

  /* 模型行 */
  const modelRows = el('div', { class: 'pm-list' });
  const buildModelRow = (m) => {
    let delTimer = null;
    const delBtn = el('button', { class: 'pm-del', title: '删除模型条目' }, ['×']);
    delBtn.addEventListener('click', (ev) => {
      ev.stopPropagation();
      if (delBtn.dataset.confirm) {
        clearTimeout(delTimer);
        toast(`后端暂未提供删除模型的接口：请在 trae_config.yaml 的 models 中删除「${m.key}」条目后重启服务。`, 'info', 6500);
        delBtn.textContent = '×'; delBtn.classList.remove('confirming'); delete delBtn.dataset.confirm;
        return;
      }
      delBtn.dataset.confirm = '1';
      delBtn.textContent = '确认?';
      delBtn.classList.add('confirming');
      delTimer = setTimeout(() => { delBtn.textContent = '×'; delBtn.classList.remove('confirming'); delete delBtn.dataset.confirm; }, 3000);
    });
    return el('div', { class: `pm-row${m.key === active ? ' active' : ''}` }, [
      el('span', {
        class: 'radio', title: '设为当前使用',
        onclick: async (ev) => {
          ev.stopPropagation();
          if (m.key === store.activeModel || store.running) return;
          try { await apiSetModel(m.key); setState({ activeModel: m.key }); renderModelSel(); renderModels(); toast(`已切换模型：${m.key}`, 'ok'); }
          catch (e) { toast(`切换失败：${fmtErr(e)}`, 'err'); }
        },
      }),
      el('div', { class: 'pm-main' }, [
        el('div', { class: 'pm-key mono' }, [m.key, m.key === active ? el('span', { class: 'chip cyan' }, ['使用中']) : null, m.api_key_set === false && !local ? el('span', { class: 'chip red' }, ['缺 Key']) : null]),
        m.model ? el('div', { class: 'pm-model mono', $text: m.model }) : null,
      ]),
      delBtn,
    ]);
  };
  for (const m of g.models) modelRows.append(buildModelRow(m));

  /* 添加模型行（无 max_tokens） */
  const mkInput = el('input', { type: 'text', class: 'mk-input', placeholder: 'model_key 如 my-glm', spellcheck: 'false' });
  const mnInput = el('input', { type: 'text', class: 'mn-input', placeholder: '模型名 如 zai-org/GLM-5.3', spellcheck: 'false' });
  const discBtn = el('button', { class: 'btn sm disc-btn', title: '从 API 拉取模型列表' }, [icon('sparkleList'), ' 获取']);
  discBtn.addEventListener('click', () => {
    runDiscover(discoverPayload({ provider: g.name, keyVal: keyInput.value, urlVal: urlInput.value, protocol: proto }), (id) => { mnInput.value = id; mnInput.focus(); });
  });
  const addBtn = el('button', { class: 'btn sm primary' }, ['＋ 添加']);
  const doAdd = async () => {
    const key = mkInput.value.trim(), name = mnInput.value.trim();
    if (!key) return setStatus('请填写 model_key', 'bad');
    if (!/^[A-Za-z0-9_-]+$/.test(key)) return setStatus('model_key 仅限字母/数字/_/-', 'bad');
    if (!name) return setStatus('请填写模型名', 'bad');
    if (store.config?.models?.[key]) return setStatus(`model_key「${key}」已存在`, 'bad');
    addBtn.disabled = true; addBtn.textContent = '添加中…';
    try {
      await apiAddModel(key, name, g.name);
      await loadConfig(true);
      openProvider = g.name;
      toast(`已添加模型 ${key}，已保存到 trae_config.yaml，立即生效`, 'ok');
    } catch (e) { setStatus(`添加失败：${fmtErr(e)}`, 'bad'); toast(`添加失败：${fmtErr(e)}`, 'err', 5000); addBtn.disabled = false; addBtn.textContent = '＋ 添加'; }
  };
  addBtn.addEventListener('click', doAdd);
  [mkInput, mnInput].forEach((i) => i.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') doAdd(); }));

  /* 自定义设置折叠区 */
  const urlInput = el('input', { type: 'text', class: 'cred-url', spellcheck: 'false', value: origUrl, placeholder: origUrl || 'https://…' });
  const foldBody = el('div', { class: 'fold-body hidden' }, [
    el('div', { class: 'cred-field' }, [el('span', {}, ['Base URL']), urlInput]),
    el('div', { class: 'cred-field' }, [
      el('span', {}, [`模型列表（${g.models.length}）`]),
      modelRows,
      el('div', { class: 'model-add-row' }, [
        mkInput,
        el('div', { class: 'mn-row' }, [mnInput, discBtn]),
        addBtn,
      ]),
    ]),
  ]);
  const foldBtn = el('button', { class: 'fold-head', type: 'button' }, ['自定义设置 ', icon('chevron')]);
  foldBtn.addEventListener('click', () => {
    const open = foldBody.classList.toggle('hidden');
    foldBtn.textContent = '';
    foldBtn.append(`自定义设置 `, icon('chevron'));
    if (open) foldBtn.querySelector('.icn').style.transform = 'rotate(0deg)';
    else { const c = foldBtn.querySelector('.icn'); if (c) c.style.transform = 'rotate(90deg)'; }
  });

  /* 应用 */
  const applyBtn = el('button', { class: 'btn primary' }, ['应用']);
  applyBtn.addEventListener('click', async () => {
    if (local && !keyInput.value.trim() && !urlInput.value.trim()) { setStatus('本地服务无需密钥；如需修改 Base URL 请填写', 'warn'); return; }
    const patch = {};
    if (keyInput.value.trim()) patch.api_key = keyInput.value.trim();
    if (urlInput.value.trim() && urlInput.value.trim() !== origUrl) patch.base_url = urlInput.value.trim();
    if (!Object.keys(patch).length) { setStatus('未修改任何字段', 'warn'); return; }
    applyBtn.disabled = true; applyBtn.textContent = '保存中…';
    setStatus('', '');
    try {
      await apiSetCredentials(g.name, patch);
      await loadConfig(true);
      openProvider = g.name;
      toast('已保存到 trae_config.yaml，立即生效', 'ok');
    } catch (e) {
      setStatus(`保存失败：${fmtErr(e)}`, 'bad');
      toast(`保存失败：${fmtErr(e)}`, 'err', 5000);
      applyBtn.disabled = false; applyBtn.textContent = '应用';
    }
  });

  const card = el('div', { class: 'prov-editcard hidden' }, [
    el('div', { class: 'cred-field' }, [
      el('span', {}, ['API 密钥']),
      el('div', { class: 'key-row' }, [keyInput, eyeBtn]),
    ]),
    foldBtn, foldBody,
    el('div', { class: 'cred-actions' }, [applyBtn, status]),
  ]);
  card._origUrl = origUrl;
  return card;
}

/* ---------------- 添加模型提供商（分段式） ---------------- */
function renderAdPviders(providers) {
  const sel = $('#apProvider');
  if (!sel) return;
  const cur = sel.value;
  sel.innerHTML = '';
  if (!providers.length) {
    sel.append(el('option', { value: '' }, ['（暂无提供商）']));
    sel.disabled = true;
    return;
  }
  sel.disabled = false;
  for (const p of providers) sel.append(el('option', { value: p }, [p]));
  if (providers.includes(cur)) sel.value = cur;
}

function toggleAddProviderCard(show) {
  const card = $('#addProviderCard');
  const btn = $('#btnAddProvider');
  const willShow = show ?? card.classList.contains('hidden');
  card.classList.toggle('hidden', !willShow);
  btn.textContent = willShow ? '× 收起' : '＋ 添加模型提供商';
  if (!willShow) $('#apStatus').textContent = '';
}

function segSwitch(name) {
  $$('.seg-tab').forEach((b) => b.classList.toggle('active', b.dataset.seg === name));
  $('#segPage-builtin').classList.toggle('hidden', name !== 'builtin');
  $('#segPage-custom').classList.toggle('hidden', name !== 'custom');
  if (name === 'custom') updateYamlPreview();
}

function customYaml() {
  const name = $('#apcName').value.trim() || 'my-relay';
  const url = $('#apcUrl').value.trim() || 'https://your-relay.example.com/v1';
  const proto = $('#apcProto').value || 'openai_compatible';
  const model = $('#apcModel').value.trim() || 'zai-org/GLM-5.3';
  return [
    '# 等价 YAML —— 也可手工追加到 trae_config.yaml（保存后重启服务）',
    'model_providers:',
    `  ${name}:`,
    `    api_key: "在此填入密钥"`,
    `    provider: ${proto}`,
    `    base_url: ${url}`,
    '',
    'models:',
    `  ${name}-default:`,
    `    model: ${model}`,
    `    model_provider: ${name}`,
    '',
  ].join('\n');
}

function updateYamlPreview() { $('#apcYaml').textContent = customYaml(); }

async function onApplyAddProvider() {
  const provider = $('#apProvider').value;
  const key = $('#apKey').value.trim();
  const url = $('#apUrl').value.trim();
  const mk = $('#apModelKey').value.trim();
  const mn = $('#apModelName').value.trim();
  const status = $('#apStatus');
  const btn = $('#btnApApply');
  const setStatus = (msg, cls) => { status.textContent = msg || ''; status.className = `cred-status ${cls || ''}`; };

  if (!provider) return setStatus('请选择提供商', 'bad');
  if (mk && !/^[A-Za-z0-9_-]+$/.test(mk)) return setStatus('model_key 仅限字母/数字/_/-', 'bad');
  if ((mk || mn) && !(mk && mn)) return setStatus('新模型需同时填写 model_key 与模型名', 'bad');
  if (mk && store.config?.models?.[mk]) return setStatus(`model_key「${mk}」已存在`, 'bad');
  if (!key && !url && !(mk && mn)) return setStatus('请至少填写密钥、Base URL 或新模型之一', 'warn');

  btn.disabled = true; btn.textContent = '保存中…'; setStatus('', '');
  try {
    const patch = {};
    if (key) patch.api_key = key;
    if (url) patch.base_url = url;
    if (Object.keys(patch).length) await apiSetCredentials(provider, patch);
    if (mk && mn) await apiAddModel(mk, mn, provider);
    openProvider = provider;
    await loadConfig(true);
    toggleAddProviderCard(false);
    $('#apKey').value = ''; $('#apUrl').value = ''; $('#apModelKey').value = ''; $('#apModelName').value = '';
    toast('已保存到 trae_config.yaml，立即生效', 'ok');
  } catch (e) {
    setStatus(`保存失败：${fmtErr(e)}`, 'bad');
    toast(`保存失败：${fmtErr(e)}`, 'err', 5000);
  } finally {
    btn.disabled = false; btn.textContent = '应用';
  }
}

async function onCreateProvider() {
  const name = $('#apcName').value.trim();
  const url = $('#apcUrl').value.trim();
  const proto = $('#apcProto').value;
  const key = $('#apcKey').value.trim();
  const model = $('#apcModel').value.trim();
  const status = $('#apcStatus');
  const btn = $('#btnApcCreate');
  const setStatus = (msg, cls) => { status.textContent = msg || ''; status.className = `cred-status ${cls || ''}`; };

  if (!name) return setStatus('请填写 Provider 名称', 'bad');
  if (!/^[A-Za-z0-9_-]{2,32}$/.test(name)) return setStatus('名称须为 2-32 位字母/数字/_/-', 'bad');
  if (!url) return setStatus('请填写 Base URL', 'bad');
  if (!/^https?:\/\//.test(url)) return setStatus('Base URL 必须以 http:// 或 https:// 开头', 'bad');
  const known = [
    ...(Array.isArray(store.config?.providers) ? store.config.providers.map((p) => (typeof p === 'string' ? p : p?.name)) : []),
    ...Object.values(store.config?.models || {}).map((m) => m?.provider_key || m?.provider),
  ].filter(Boolean);
  if (known.includes(name)) return setStatus(`provider「${name}」已存在`, 'bad');

  btn.disabled = true; btn.textContent = '创建中…'; setStatus('', '');
  try {
    const patch = { protocol: proto };
    if (key) patch.api_key = key;
    if (model) patch.first_model = model;
    const r = await apiAddProvider(name, url, patch);
    openProvider = name;
    await loadConfig(true);
    $('#apcName').value = ''; $('#apcUrl').value = ''; $('#apcKey').value = ''; $('#apcModel').value = '';
    $('#segPage-custom .eye-btn').hidden = true; $('#apcKey').type = 'password';
    updateYamlPreview();
    toggleAddProviderCard(false);
    const mk = r?.model_key;
    toast(`提供商 ${name} 已创建${mk ? `（模型 ${mk}）` : ''}，已保存到 trae_config.yaml，立即生效`, 'ok', 5000);
  } catch (e) {
    setStatus(`创建失败：${fmtErr(e)}`, 'bad');
    toast(`创建失败：${fmtErr(e)}`, 'err', 6000);
    btn.disabled = false; btn.textContent = '创建提供商';
    return;
  }
  btn.disabled = false; btn.textContent = '创建提供商';
}

function renderToolChips(tools) {
  const box = $('#toolChips');
  if (!box) return;
  box.innerHTML = '';
  if (!tools.length) {
    box.append(el('span', { class: 'chip dim' }, ['配置未启用任何工具（trae_config.yaml → agents.trae_agent.tools）']));
    return;
  }
  for (const t of tools) {
    box.append(el('span', { class: `chip mono${HW_TOOLS.has(t) ? ' amber' : ''}`, title: HW_TOOLS.has(t) ? '硬件工具' : '' }, [String(t)]));
  }
  box.append(el('span', { class: 'chip dim' }, [`${tools.length} 个工具`]));
}

/* ---------------- 启动绑定 ---------------- */
// 侧栏子 tab（会话/文件）
$$('.side-tab').forEach((b) => b.addEventListener('click', () => {
  $$('.side-tab').forEach((x) => x.classList.toggle('active', x === b));
  $('#side-sessions').classList.toggle('hidden', b.dataset.side !== 'sessions');
  $('#side-files').classList.toggle('hidden', b.dataset.side !== 'files');
}));

// 添加模型提供商卡
$('#btnAddProvider').addEventListener('click', () => toggleAddProviderCard());
$$('.seg-tab').forEach((b) => b.addEventListener('click', () => segSwitch(b.dataset.seg)));
$('#btnApApply').addEventListener('click', onApplyAddProvider);
$('#btnApcCreate').addEventListener('click', onCreateProvider);

// 「获取模型列表」（添加 tab：表单值优先，否则已存 key）
$('#btnApDiscover').addEventListener('click', () => {
  const provider = $('#apProvider').value;
  const known = normModels().find((m) => m.providerKey === provider);
  const proto = known?.client || 'openai_compatible';
  const url = $('#apUrl').value.trim() || (known ? known.base_url : '');
  runDiscover(
    discoverPayload({ provider, keyVal: $('#apKey').value, urlVal: url, protocol: proto }),
    (id) => { $('#apModelName').value = id; },
  );
});

// 两处眼睛按钮
function bindEye(keySel, eyeSel) {
  const input = $(keySel), eye = $(eyeSel);
  input.addEventListener('input', () => {
    eye.hidden = !input.value;
    if (!input.value) { input.type = 'password'; eye.innerHTML = ''; eye.append(icon('eye')); }
  });
  eye.addEventListener('click', () => {
    const show = input.type === 'password';
    input.type = show ? 'text' : 'password';
    eye.innerHTML = ''; eye.append(icon(show ? 'eyeOff' : 'eye'));
    input.focus();
  });
}
bindEye('#apKey', '#segPage-builtin .eye-btn');
bindEye('#apcKey', '#segPage-custom .eye-btn');

['apcName', 'apcUrl', 'apcProto', 'apcModel'].forEach((id) => {
  const n = document.getElementById(id);
  n.addEventListener('input', updateYamlPreview);
  n.addEventListener('change', updateYamlPreview);
});
$('#apcYaml').textContent = customYaml();

$('#btnCopyYaml').addEventListener('click', () => {
  const yaml = customYaml();
  navigator.clipboard?.writeText(yaml).then(
    () => { $('#apcStatus').textContent = '✓ 示例 YAML 已复制到剪贴板'; $('#apcStatus').className = 'cred-status ok'; toast('示例 YAML 已复制，粘贴到 trae_config.yaml 后重启服务即可手工添加', 'ok', 4500); },
    () => { $('#apcStatus').textContent = '复制失败：浏览器未授权剪贴板'; $('#apcStatus').className = 'cred-status bad'; },
  );
});

// 全局：代码块复制按钮（委托）
document.addEventListener('click', (ev) => {
  const btn = ev.target.closest('.copy-btn');
  if (!btn) return;
  const code = btn.closest('.codeblock')?.querySelector('code')?.textContent ?? '';
  navigator.clipboard?.writeText(code).then(
    () => { btn.textContent = '已复制'; setTimeout(() => (btn.textContent = '复制'), 1400); toast('代码已复制到剪贴板', 'ok', 1600); },
    () => toast('复制失败：浏览器未授权剪贴板', 'err'),
  );
});

// 配置加载失败重试
document.addEventListener('click', (ev) => {
  if (ev.target.closest('.state .retry')) initLoad();
});

// 外观 / 工具路径
bindAppearance();
bindFsModal();
$('#btnToolPathsSave').addEventListener('click', saveToolPaths);
$('#btnDetectAll').addEventListener('click', detectAllTools);
$$('.tp-row .tp-detect').forEach((btn) => {
  btn.addEventListener('click', (ev) => { ev.stopPropagation(); detectTool(btn.closest('.tp-row')); });
});
$$('.tp-row .tp-browse').forEach((btn) => {
  btn.addEventListener('click', (ev) => { ev.stopPropagation(); fsOpen(btn.closest('.tp-row')); });
});
hydrateIcons($('.modal.settings')); // 弹窗内 data-icn（探测按钮等）

onChange((patch) => { if ('activeModel' in patch) renderModelSel(); });

initLoad();
