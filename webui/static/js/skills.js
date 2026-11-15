// skills.js — 技能面板：统计徽标 / 本地过滤 + AI 检索 / tag 分组列表 / 详情
import { $, $$, el, toast, fmtErr, debounce } from './util.js';
import { apiSkills, apiSkill, apiSkillSearch } from './api.js';
import { mdRender } from './md.js';

let inited = false;
let allSkills = [];   // GET /api/skills 的 skills 数组
let activeDetail = null; // 当前选中技能 id
let aiHitIds = null;  // AI 检索命中 id 集（null = 未检索）

const FM_LABELS = {
  'name': '名称', 'description': '描述', 'argument-hint': '参数提示', 'disable-model-invocation': '禁止模型自调',
  'allowed-tools': '允许工具', 'model': '模型', 'agent': '代理', 'when_to_use': '适用场景',
};

/* ---------- 三态帮助 ---------- */
function stateBox(kind, text, retry) {
  return el('div', { class: `state${kind === 'error' ? ' error' : ''}` }, [
    kind === 'loading' ? el('span', { class: 'spinner' }) : el('span', { class: 'glyph' }, [kind === 'error' ? '!!' : '□']),
    el('span', { $text: text }),
    retry ? el('button', { class: 'btn sm retry', onclick: () => init() }, ['重试']) : null,
  ]);
}

/* ---------- 统计 ---------- */
function renderStats() {
  const box = $('#skillStats');
  box.innerHTML = '';
  const total = allSkills.length;
  const tags = {};
  for (const s of allSkills) { const t = s.tag || '其他'; tags[t] = (tags[t] || 0) + 1; }
  box.append(el('span', { class: 'chip cyan' }, [`共 ${total} 个技能`]));
  for (const [t, n] of Object.entries(tags)) {
    box.append(el('span', { class: 'chip' }, [`${t} ${n}`]));
  }
  const enabled = allSkills.filter((s) => s.enabled !== false).length;
  if (enabled < total) box.append(el('span', { class: 'chip red' }, [`${total - enabled} 个已禁用`]));
}

/* ---------- 列表（tag 分组 + 两行截断） ---------- */
function matchLocal(s, q) {
  q = q.trim().toLowerCase();
  if (!q) return true;
  return [s.id, s.name, s.description].some((x) => String(x || '').toLowerCase().includes(q));
}

function renderList() {
  const q = $('#skillSearch').value;
  const box = $('#skillList');
  box.innerHTML = '';
  const list = allSkills.filter((s) => matchLocal(s, q));
  if (aiHitIds) {
    // AI 检索结果模式：高亮命中项，其余按原序低透明度跟随
    if (!list.length && !aiHitIds.size) {
      box.append(stateBox('empty', '没有匹配的技能', false));
      return;
    }
  }
  if (!allSkills.length) {
    box.append(stateBox('empty', '技能库为空（skills 目录未发现 SKILL.md）', false));
    return;
  }

  const groups = new Map();
  if (aiHitIds) {
    groups.set('__ai', list.filter((s) => aiHitIds.has(s.id)));
    const rest = allSkills.filter((s) => !aiHitIds.has(s.id));
    for (const s of rest) {
      if (!groups.has(s.tag || '其他')) groups.set(s.tag || '其他', []);
      groups.get(s.tag || '其他').push(s);
    }
  } else {
    for (const s of list) {
      const t = s.tag || '其他';
      if (!groups.has(t)) groups.set(t, []);
      groups.get(t).push(s);
    }
  }

  for (const [tag, items] of groups) {
    if (!items.length) continue;
    const g = el('div', { class: 'skill-group' }, [
      el('div', { class: 'sg-head' }, [
        tag === '__ai' ? el('span', { class: 'chip amber' }, ['🤖 AI 检索命中']) : el('span', { $text: `# ${tag}` }),
        el('span', { $text: `${items.length}` }),
      ]),
    ]);
    for (const s of items) {
      const hit = aiHitIds?.has(s.id);
      const item = el('div', {
        class: `skill-item${s.id === activeDetail ? ' active' : ''}${hit ? ' si-search-hit' : ''}`,
        style: aiHitIds && !hit ? { opacity: '.45' } : {},
        onclick: () => openDetail(s.id),
      }, [
        el('div', { class: 'si-name' }, [
          el('span', { $text: s.name || s.id }),
          s.enabled === false ? el('span', { class: 'chip red' }, ['已禁用']) : null,
          hit ? el('span', { class: 'score-pill', $text: '★' }) : null,
        ]),
        el('div', { class: 'si-desc', $text: s.description || '（无描述）' }),
      ]);
      g.append(item);
    }
    box.append(g);
  }
  if (aiHitIds && !aiHitIds.size) {
    // 无命中：上面 groups 已含 rest，补提示
  }
}

/* ---------- AI 检索结果条 ---------- */
function renderAiResults(results) {
  const box = $('#aiSearchResult');
  box.innerHTML = '';
  box.classList.remove('hidden');
  if (!results.length) {
    box.append(el('div', { style: { padding: '8px 10px', fontSize: '12.5px', color: 'var(--fg-mute)', border: '1px dashed var(--line2)', borderRadius: '6px' } },
      ['AI 检索未命中——试试换个说法，或直接本地过滤（结果仍按分组展示）']));
    return;
  }
  for (const r of results) {
    const whys = Array.isArray(r.why) ? r.why : (r.why ? [r.why] : []);
    box.append(el('div', {
      class: 'skill-item si-search-hit', style: { marginBottom: '6px' },
      onclick: () => openDetail(r.id),
    }, [
      el('div', { class: 'si-name' }, [
        el('span', { $text: r.name || r.id }),
        el('span', { class: 'score-pill', $text: `匹配度 ${Number(r.score ?? 0).toFixed ? Number(r.score).toFixed(2) : r.score}` }),
      ]),
      whys.length ? el('div', { class: 'why', $html: whys.map((w) => `<b>▸</b> ${escapeLite(w)}`).join('<br>') }) : null,
    ]));
  }
}
const escapeLite = (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

/* ---------- 详情 ---------- */
function fmValue(v) {
  if (v === null || v === undefined) return '';
  if (Array.isArray(v)) return v.map((x) => String(x)).join(', ');
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}

async function openDetail(id) {
  activeDetail = id;
  renderList();
  const box = $('#skillDetail');
  box.innerHTML = '';
  box.append(stateBox('loading', '正在加载技能详情…'));
  try {
    const d = await apiSkill(id);
    box.innerHTML = '';
    const fm = d?.frontmatter && typeof d.frontmatter === 'object' ? d.frontmatter : {};
    const head = el('div', { class: 'sd-head' }, [
      el('h2', {}, [d.name || id]),
      el('span', { class: 'chip cyan' }, [d.id || id]),
      d.tag ? el('span', { class: 'chip' }, [`#${d.tag}`]) : null,
    ]);
    const fmBox = el('div', { class: 'card' }, [el('h3', {}, ['Frontmatter'])]);
    const pairs = el('div', { class: 'fm-pairs' });
    for (const [k, v] of Object.entries(fm)) {
      pairs.append(el('span', { class: 'k', $text: FM_LABELS[k] || k }), el('span', { class: 'v', $text: fmValue(v) }));
    }
    if (!Object.keys(fm).length) pairs.append(el('span', { class: 'k' }, ['（无）']), el('span', { class: 'v' }, ['—']));
    fmBox.append(pairs);
    const bodyCard = el('div', { class: 'card' }, [
      el('h3', {}, ['SKILL.md 正文']),
      el('div', { class: 'md', $html: mdRender(d.body || '（无正文）') }),
    ]);
    const files = Array.isArray(d.files) ? d.files : filesFallback(d);
    const filesCard = el('div', { class: 'card' }, [el('h3', {}, [`附属文件 ${files.length}`])]);
    const chips = el('div', { class: 'file-chips' });
    for (const f of files) {
      const name = typeof f === 'string' ? f : (f?.name || f?.path || JSON.stringify(f));
      chips.append(el('span', { class: 'chip mono', title: typeof f === 'object' && f?.path ? f.path : '' }, [name]));
    }
    if (!files.length) chips.append(el('span', { class: 'chip dim' }, ['无附属文件']));
    filesCard.append(chips);
    box.append(head, fmBox, bodyCard, filesCard);
  } catch (e) {
    box.innerHTML = '';
    box.append(stateBox('error', `技能详情加载失败：${fmtErr(e)}`, false));
    toast(`技能详情加载失败：${fmtErr(e)}`, 'err');
  }
}
// 兜底字段名（若后端用 extra_files 而非 files，避免空白）
function filesFallback(d) {
  const alt = d?.files ?? d?.extra_files ?? d?.extraFiles;
  return Array.isArray(alt) ? alt : [];
}

/* ---------- 初始化 ---------- */
export async function init() {
  if (!inited) {
    inited = true;
    $('#skillSearch').addEventListener('input', debounce(() => { aiHitIds = null; $('#aiSearchResult').classList.add('hidden'); renderList(); }, 150));
    $('#btnAiSearch').addEventListener('click', doAiSearch);
    $('#skillSearch').addEventListener('keydown', (ev) => { if (ev.key === 'Enter') doAiSearch(); });
  }
  const box = $('#skillList');
  box.innerHTML = '';
  box.append(stateBox('loading', '正在加载技能库…'));
  try {
    const data = await apiSkills();
    allSkills = Array.isArray(data?.skills) ? data.skills : [];
  } catch (e) {
    box.innerHTML = '';
    box.append(stateBox('error', `技能库加载失败：${fmtErr(e)}`, true));
    toast(`技能库加载失败：${fmtErr(e)}`, 'err');
    return;
  }
  renderStats();
  renderList();
}

async function doAiSearch() {
  const q = $('#skillSearch').value.trim();
  if (!q) { toast('请先输入检索内容', 'info'); return; }
  const btn = $('#btnAiSearch');
  btn.disabled = true; btn.textContent = '检索中…';
  try {
    const data = await apiSkillSearch(q);
    const results = Array.isArray(data?.results) ? data.results : [];
    aiHitIds = new Set(results.map((r) => r.id));
    renderAiResults(results);
    renderList();
    if (results.length) toast(`AI 检索命中 ${results.length} 个技能`, 'ok');
  } catch (e) {
    aiHitIds = null;
    toast(`AI 检索失败：${fmtErr(e)}`, 'err', 5000);
  } finally {
    btn.disabled = false; btn.textContent = '🤖 AI 检索';
  }
}
