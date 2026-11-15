// store.js — 全局状态（跨面板共享）
const listeners = new Set();

export const store = {
  /** GET /api/health → {ok, version, default_model} */
  health: null,
  /** GET /api/config 全量对象 */
  config: null,
  /** 当前激活 model key（config.active_model） */
  activeModel: '',
  /** 当前是否处于 SSE 运行中 */
  running: false,
  /** GET /api/ui-settings → {brand, theme, tool_paths} */
  uiSettings: null,
};

export function onChange(fn) { listeners.add(fn); return () => listeners.delete(fn); }

/** 修改 store 并广播 patch: {key: value} */
export function setState(patch) {
  Object.assign(store, patch);
  for (const fn of listeners) {
    try { fn(patch); } catch (e) { console.error('[store listener]', e); }
  }
}
