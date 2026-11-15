// api.js — fetch 封装（超时 + 中文错误）与 SSE 手动解析（fetch + ReadableStream，POST 流）
import { ApiError } from './util.js';

const BASE = '';
const DEFAULT_TIMEOUT = 20000;

/** 组合外部 signal 与超时：任一触发即中止 */
function withTimeout(ms, external) {
  const ctrl = new AbortController();
  const timer = ms > 0 ? setTimeout(() => ctrl.abort(new DOMException('请求超时', 'TimeoutError')), ms) : null;
  if (external) {
    if (external.aborted) ctrl.abort(external.reason);
    else external.addEventListener('abort', () => ctrl.abort(external.reason), { once: true });
  }
  return { signal: ctrl.signal, cleanup: () => timer && clearTimeout(timer) };
}

async function readErrBody(res) {
  try {
    const txt = await res.text();
    try {
      const j = JSON.parse(txt);
      return j.detail || j.message || j.error || txt;
    } catch { return txt; }
  } catch { return ''; }
}

/**
 * JSON 请求。返回解析后的对象（2xx）。
 * opts: {method, body, timeout, signal, formData}
 */
export async function fetchJSON(path, opts = {}) {
  const { method = 'GET', body, formData, timeout = DEFAULT_TIMEOUT, signal } = opts;
  const headers = {};
  if (body !== undefined && !formData) headers['Content-Type'] = 'application/json';
  const { signal: sig, cleanup } = withTimeout(timeout, signal);
  let res;
  try {
    res = await fetch(BASE + path, {
      method,
      headers,
      body: formData ? formData : body !== undefined ? JSON.stringify(body) : undefined,
      signal: sig,
    });
  } catch (e) {
    if (e instanceof DOMException && e.name === 'TimeoutError') throw new ApiError('请求超时，请检查服务是否响应', 0);
    if (e?.name === 'AbortError') throw e;
    throw new ApiError('网络连接失败，无法访问服务', 0);
  } finally { cleanup(); }

  if (!res.ok) {
    const detail = await readErrBody(res);
    throw new ApiError(`服务返回错误 ${res.status}${detail ? '：' + String(detail).slice(0, 300) : ''}`, res.status);
  }
  try { return await res.json(); } catch { throw new ApiError('服务响应不是有效 JSON', res.status); }
}

/**
 * POST /api/chat SSE 流解析（POST + ReadableStream 手动解析，不用 EventSource）。
 * onEvent(evtObj) 逐事件回调；':hb' 注释行忽略。
 * 返回 Promise<lastEvent>；由 final/error 收尾。signal 用于停止。
 */
export async function streamChat(payload, { onEvent, signal } = {}) {
  const res = await fetch(BASE + '/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    signal,
  });
  if (!res.ok) {
    const detail = await readErrBody(res);
    throw new ApiError(`服务返回错误 ${res.status}${detail ? '：' + String(detail).slice(0, 300) : ''}`, res.status);
  }
  if (!res.body) throw new ApiError('当前浏览器不支持流式响应', res.status);

  const reader = res.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buf = '';
  let last = null;
  let dataLines = [];   // 当前事件累积的 data: 行

  const dispatch = () => {
    if (!dataLines.length) return;
    const raw = dataLines.join('\n').trim();
    dataLines = [];
    if (!raw) return;
    let evt;
    try { evt = JSON.parse(raw); } catch { evt = { type: 'raw', text: raw }; }
    if (evt && typeof evt === 'object') {
      last = evt;
      try { onEvent?.(evt); } catch (e) { console.error('[sse onEvent]', e); }
    }
  };

  const feedLine = (line) => {
    if (line === '') { dispatch(); return; }      // 空行 = 事件边界
    if (line.startsWith(':')) return;             // 注释行（如 ':hb' 心跳）忽略
    if (line.startsWith('data:')) dataLines.push(line.slice(5).replace(/^ /, ''));
    // 其余 SSE 字段（event:/id:/retry:）本协议不使用，忽略
  };

  // 按 \n 切行，行尾 \r 归一化；\r\n\r\n 边界下的空行 dispatch 均为 no-op，安全。
  const splitFeed = () => {
    let idx;
    while ((idx = buf.indexOf('\n')) >= 0) {
      const line = buf.endsWith('\r', idx) ? buf.slice(0, idx - 1) : buf.slice(0, idx);
      buf = buf.slice(idx + 1);
      feedLine(line);
    }
  };

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      splitFeed();
    }
    buf += decoder.decode();
    if (buf) { buf.split('\n').forEach(feedLine); } // 流末尾无换行的事件
  } finally {
    try { reader.cancel(); } catch { /* noop */ }
  }
  return last;
}

/* ---------------- 具体端点 ---------------- */

/** GET /api/health → {ok, version, default_model} */
export const apiHealth = () => fetchJSON('/api/health', { timeout: 8000 });

/** GET /api/config → {models, active_model, workspace, tools} */
export const apiConfig = () => fetchJSON('/api/config');

/** GET /api/config */
export const apiSkills = () => fetchJSON('/api/skills');

/** GET /api/skills/{id}（id 形如 "mp/tdd"，需编码；后端若按 :path 路由也未编码可用原样） */
export function apiSkill(id) {
  const enc = encodeURIComponent(id);
  return fetchJSON(`/api/skills/${enc}`).catch((e) => {
    if (e instanceof ApiError && e.status === 404 && enc !== id) {
      return fetchJSON(`/api/skills/${id}`); // 兜底：原始斜杠路径
    }
    throw e;
  });
}

/** POST /api/skills/search {query} → {results:[{id,name,score,why}]} */
export const apiSkillSearch = (query) => fetchJSON('/api/skills/search', { method: 'POST', body: { query }, timeout: 45000 });

/** POST /api/config/model {model_key} → {ok} */
export const apiSetModel = (model_key) => fetchJSON('/api/config/model', { method: 'POST', body: { model_key } });

/** PUT /api/config/credentials {provider, api_key?, base_url?} → {ok}（只传要改的字段；400/405 返回 {error}） */
export const apiSetCredentials = (provider, patch = {}) => {
  const body = { provider };
  if (patch.api_key) body.api_key = patch.api_key;
  if (patch.base_url) body.base_url = patch.base_url;
  return fetchJSON('/api/config/credentials', { method: 'PUT', body, timeout: 30000 });
};

/** POST /api/config/models {model_key, model, provider, max_tokens?} → {ok} */
export const apiAddModel = (model_key, model, provider, max_tokens) => {
  const body = { model_key, model, provider };
  const n = Number(max_tokens);
  if (max_tokens != null && max_tokens !== '' && Number.isFinite(n) && n > 0) body.max_tokens = Math.floor(n);
  return fetchJSON('/api/config/models', { method: 'POST', body, timeout: 30000 });
};

/** POST /api/config/providers {provider, base_url, api_key?, protocol?, first_model?} → {ok, provider, model_key} */
export const apiAddProvider = (provider, base_url, patch = {}) => {
  const body = { provider, base_url };
  if (patch.api_key) body.api_key = patch.api_key;
  if (patch.protocol) body.protocol = patch.protocol;
  if (patch.first_model) body.first_model = patch.first_model;
  return fetchJSON('/api/config/providers', { method: 'POST', body, timeout: 30000 });
};

/**
 * POST /api/config/discover-models — 从 API 拉取可用模型列表。
 * body 二选一：{provider}（用已存 key）或 {base_url, api_key?, protocol}（临时凭证，不落盘）
 * → {models: [{id}], total}
 */
export const apiDiscoverModels = (body) => fetchJSON('/api/config/discover-models', {
  method: 'POST', body, timeout: 60000,
});

/** GET /api/ui-settings → {brand, theme:{primary,secondary,accent}, tool_paths:{keil,...}} */
export const apiUiSettings = () => fetchJSON('/api/ui-settings', { timeout: 10000 });

/** PUT /api/ui-settings body 同结构 → {ok, settings}（400：颜色 #RRGGBB / 路径存在性中文校验） */
export const apiPutUiSettings = (settings) => fetchJSON('/api/ui-settings', {
  method: 'PUT', body: settings, timeout: 20000,
});

/** POST /api/tools/detect {tool, custom_path?} → {found, path, hint}（全盘探测较慢，超时放宽） */
export const apiToolDetect = (tool, custom_path) => {
  const body = { tool };
  const cp = (custom_path || '').trim();
  if (cp) body.custom_path = cp;
  return fetchJSON('/api/tools/detect', { method: 'POST', body, timeout: 180000 });
};

/** POST /api/tools/detect {all:true} → {tools:[{tool,label,found,path,hint,download?} × N]} 一键探测全部 */
export const apiToolDetectAll = () => fetchJSON('/api/tools/detect', {
  method: 'POST', body: { all: true }, timeout: 200000,
});

/** GET /api/fs/browse?path= → {path, parent, entries:[{name,dir}]}（空 path = 盘符列表） */
export const apiFsBrowse = (path) => fetchJSON(`/api/fs/browse?path=${encodeURIComponent(path || '')}`, { timeout: 20000 });

/** GET /api/files?path=&recursive= → {files/entries:[{name,path,dir,size}]} */
export function apiFiles(path = '', recursive = true) {
  const q = new URLSearchParams({ path, recursive: recursive ? 'true' : 'false' });
  return fetchJSON(`/api/files?${q}`, { timeout: 30000 });
}

/** GET /api/file?path= → {content,size} */
export const apiFile = (path) => fetchJSON(`/api/file?path=${encodeURIComponent(path)}`, { timeout: 30000 });

/** POST /api/files/upload FormData(files多值 + subdir可选) → {saved, skipped}（新后端 {files, rejected, unzipped}） */
export async function apiUpload(fileList, subdir = '') {
  const fd = new FormData();
  for (const f of fileList) fd.append('files', f, f.name); // File.name 已含相对路径（new File([f], relPath)）
  if (subdir) fd.append('subdir', subdir);
  return fetchJSON('/api/files/upload', { method: 'POST', formData: fd, timeout: 120000 });
}

/** POST /api/files/delete {path, confirm:true} → {ok, deleted}（404 不存在 / 400 缺 confirm / 禁止根） */
export const apiDeletePath = (path) => fetchJSON('/api/files/delete', {
  method: 'POST', body: { path, confirm: true }, timeout: 30000,
});

/** GET /api/files/download?path= → 文件本体；目录/空 path 自动打 zip。返回走浏览器下载 */
export const downloadURL = (path = '') => `/api/files/download?path=${encodeURIComponent(path)}`;
