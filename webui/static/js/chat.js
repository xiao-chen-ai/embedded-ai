// chat.js — 聊天面板：思考中状态（趣味文案轮播）+ 过程折叠（final 单次渲染）
// 会话(localStorage) / 停止 / SSE 事件驱动；assistant 中间文本归入折叠区，不进主流程
import { $, $$, el, icon, esc, toast, fmtErr, fmtTime } from './util.js';
import { store, setState } from './store.js';
import { streamChat } from './api.js';
import { mdRender } from './md.js';
import { initFiles } from './files.js';

const LS_SESS = 'trae_webui_sessions';
const LS_CUR = 'trae_webui_cur';
const HW_TOOLS = new Set(['analyze_schematic', 'generate_pinmap']);
const MAX_OUT = 8000; // 工具输出渲染上限

/* 趣味文案轮播池（无工具活动时，每 7s 换一条） */
const FUN_MSGS = [
  '🧠 思考中…', '💡 整理思路…', '🧩 拼凑线索…', '🔍 检查细节…',
  '🛠️ 摆弄工具箱…', '⚡ 计算中…', '🤖 AI 努力工作中…', '☕ 泡杯咖啡等等我…',
];

/* 工具事件 → 精准文案 */
const TOOL_MSG = {
  bash: '⌨️ 执行命令中…',
  execute_bash: '⌨️ 执行命令中…',
  str_replace_based_edit_tool: '✍️ 修改文件中…',
  edit_tool: '✍️ 修改文件中…',
  json_edit_tool: '🗃️ 编辑 JSON…',
  search_skills: '📚 翻技能库…',
  load_skill: '📖 读取指南…',
  run_skill_script: '▶️ 运行技能脚本…',
  analyze_schematic: '🔬 分析网表…',
  generate_pinmap: '🗺️ 生成引脚映射…',
  sequentialthinking: '🧠 深入思考…',
  task_done: '✅ 收尾中…',
  ckg: '🕸️ 检索代码知识…',
};

/* ---------------- 会话存储 ---------------- */
let sessions = [];
let curId = null;

function loadSessions() {
  try { sessions = JSON.parse(localStorage.getItem(LS_SESS) || '[]'); } catch { sessions = []; }
  if (!Array.isArray(sessions)) sessions = [];
  if (!sessions.length) sessions.push(blankSess());
  curId = localStorage.getItem(LS_CUR);
  if (!sessions.some((s) => s.id === curId)) curId = sessions[0].id;
}
function saveSessions() {
  try {
    localStorage.setItem(LS_SESS, JSON.stringify(sessions.slice(-50)));
    localStorage.setItem(LS_CUR, curId);
  } catch (e) { console.warn('会话保存失败', e); }
}
function blankSess() {
  return { id: 's' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6), title: '新会话', created: Date.now(), msgs: [] };
}
const curSess = () => sessions.find((s) => s.id === curId) || sessions[0];

/* ---------------- 侧栏会话列表 ---------------- */
function renderSessList() {
  const box = $('#sessList');
  box.innerHTML = '';
  const sorted = [...sessions].sort((a, b) => b.created - a.created);
  for (const s of sorted) {
    const item = el('div', { class: `sess-item${s.id === curId ? ' active' : ''}`, onclick: () => { curId = s.id; saveSessions(); renderSessList(); renderAllMsgs(); } }, [
      el('div', { style: { minWidth: 0, flex: '1' } }, [
        el('div', { class: 't', $text: s.title }),
        el('div', { class: 'meta', $text: `${fmtTime(s.created)} · ${s.msgs.length} 条` }),
      ]),
      el('span', {
        class: 'del', title: '删除会话', $text: '✕',
        onclick: (ev) => {
          ev.stopPropagation();
          sessions = sessions.filter((x) => x.id !== s.id);
          if (!sessions.length) sessions.push(blankSess());
          if (curId === s.id) curId = sessions[0].id;
          saveSessions(); renderSessList(); renderAllMsgs();
          toast('会话已删除', 'ok', 1500);
        },
      }),
    ]);
    box.append(item);
  }
}

/* ---------------- 消息容器 ---------------- */
const msgsBox = () => $('#msgs');

function nearBottom() {
  const b = msgsBox();
  return b.scrollHeight - b.scrollTop - b.clientHeight < 90;
}
function scrollBottom(force = false) {
  const b = msgsBox();
  if (force || nearBottom()) b.scrollTop = b.scrollHeight;
}

function emptyHint() {
  const b = msgsBox();
  b.innerHTML = '';
  b.append(el('div', { class: 'state', style: { height: '100%' } }, [
    el('span', { class: 'glyph' }, ['◧']),
    el('span', {}, ['向嵌入式智能体提问，即可开始']),
    el('span', { style: { fontSize: '12px' } }, ['示例：用 keil 技能编译这个工程 · 帮我读 workspace 里的 README.md · 这块板的按键是什么极性？']),
  ]));
}

function appendUserMsg(text) {
  msgsBox().append(el('div', { class: 'msg user' }, [el('div', { class: 'bubble', $text: text })]));
}

/** AI 消息容器（内部 body） */
function aiMsgContainer() {
  const wrap = el('div', { class: 'msg ai' });
  const head = el('div', { class: 'who' }, [el('span', { class: 'dot ok' }), 'xiaochen']);
  const body = el('div', { class: 'ai-body', style: { display: 'flex', flexDirection: 'column', gap: '10px' } });
  wrap.append(head, body);
  msgsBox().append(wrap);
  return { root: wrap, body };
}

/* ---------------- 思考中状态区 ---------------- */
function makeThinking() {
  const label = el('span', { class: 't-label pulsing', $text: FUN_MSGS[Math.floor(Math.random() * FUN_MSGS.length)] });
  const ops = el('span', { class: 't-ops mono', $text: '' });
  const box = el('div', { class: 'thinking' }, [
    el('span', { class: 'spinner' }), label, ops,
  ]);
  let i = 0, lastEventAt = 0;
  const timer = setInterval(() => {
    i = (i + 1) % FUN_MSGS.length;
    if (Date.now() - lastEventAt > 15000) { label.textContent = FUN_MSGS[i]; label.classList.add('pulsing'); }
  }, 7000);
  return {
    box,
    setTool(name) {
      lastEventAt = Date.now();
      label.classList.remove('pulsing');
      label.textContent = TOOL_MSG[name] || `🔧 ${name} 执行中…`;
    },
    setOps(n) { ops.textContent = n > 0 ? `已执行 ${n} 个操作` : ''; },
    stopTimer() { clearInterval(timer); },
    fail(msg) {
      clearInterval(timer);
      label.classList.remove('pulsing');
      box.classList.add('failed');
      label.textContent = `✕ ${msg}`;
    },
    remove() { clearInterval(timer); box.remove(); },
  };
}

/* ---------------- 执行过程折叠区 ---------------- */
function makeFold() {
  const body = el('div', { class: 'fold-contents hidden' });
  const pill = el('button', { class: 'fold-pill', type: 'button' }, ['▸ 执行过程']);
  const paint = (steps, ops) => {
    const open = !body.classList.contains('hidden');
    const bits = [`执行过程（${steps} 步` + (ops ? ` / ${ops} 操作` : '') + '）'];
    pill.textContent = (open ? '▾ ' : '▸ ') + bits[0];
  };
  let steps = 0, ops = 0;
  paint(0, 0);
  pill.addEventListener('click', () => {
    const open = body.classList.toggle('hidden');
    paint(steps, ops);
    if (open) scrollBottom();
  });
  const wrap = el('div', { class: 'fold-wrap hidden' }, [pill, body]);
  return {
    wrap, body,
    setStep(n) { steps = Math.max(steps, n); paint(steps, ops); },
    setOp() { ops++; paint(steps, ops); },
    show() { if (steps > 0 || body.children.length) wrap.classList.remove('hidden'); },
  };
}

/* ---------------- 过程块 / 主体块渲染 ---------------- */
function argsSummary(args) {
  if (args == null) return '';
  if (typeof args === 'string') return args.length > 60 ? args.slice(0, 60) + '…' : args;
  try {
    const keys = Object.keys(args);
    if (!keys.length) return '{}';
    const first = keys[0];
    let v = args[first];
    if (typeof v === 'string') v = v.length > 40 ? v.slice(0, 40) + '…' : v;
    else { v = JSON.stringify(v); if (v?.length > 40) v = v.slice(0, 40) + '…'; }
    return `${first}: ${v}`;
  } catch { return String(args).slice(0, 60); }
}

function jsonPretty(v) {
  if (v == null) return String(v);
  if (typeof v === 'string') return v;
  try { return JSON.stringify(v, null, 2); } catch { return String(v); }
}

/** 工具调用卡（过程区） */
function makeToolCard(tu) {
  const isHw = HW_TOOLS.has(tu.name);
  const argsTxt = jsonPretty(tu.args);
  const outSec = el('div', { class: 'tool-sec' }, [el('div', { class: 'lbl' }, ['输出']), el('pre', { class: 'pulsing', $text: '等待执行…' })]);
  const stat = el('span', { class: 'stat-line' }, [el('span', { class: 'spinner', style: { width: '12px', height: '12px', borderWidth: '2px' } }), el('span', { style: { fontSize: '11px' }, $text: '运行中' })]);
  const root = el('div', { class: `toolcard${isHw ? ' hw' : ''}` }, [
    el('div', { class: 'toolcard-head', onclick: (ev) => { ev.currentTarget.parentElement.classList.toggle('open'); } }, [
      el('span', { class: 'tw' }, [icon('chevron')]),
      el('span', { class: `chip${isHw ? ' amber' : ' cyan'} mono` }, [tu.name || 'tool']),
      el('span', { class: 'mono', style: { fontSize: '11.5px', color: 'var(--fg-mute)', overflow: 'hidden', textOverflow: 'ellipsis' }, $text: argsSummary(tu.args) }),
      stat,
    ]),
    el('div', { class: 'toolcard-body' }, [
      el('div', { class: 'tool-sec' }, [el('div', { class: 'lbl' }, ['参数']), el('pre', { $html: esc(argsTxt) })]),
      outSec,
    ]),
  ]);
  root._setResult = (tr) => {
    const ok = tr.success !== false;
    outSec.innerHTML = '';
    const out = jsonPretty(tr.output);
    const clipped = out.length > MAX_OUT;
    outSec.append(el('div', { class: 'lbl' }, ['输出']), el('pre', { $html: esc(clipped ? out.slice(0, MAX_OUT) + `\n…（共 ${out.length} 字符，已截断）` : out) }));
    stat.innerHTML = '';
    stat.append(
      el('span', { class: `dot ${ok ? 'ok' : 'bad'}` }),
      el('span', { style: { fontSize: '11px', color: ok ? 'var(--green)' : 'var(--red)' }, $text: ok ? '成功' : '失败' }),
    );
  };
  return root;
}

/** 过程块（step / assistant / tool）→ DOM */
function renderProcBlock(blk, ctx) {
  if (blk.t === 'step') {
    return el('div', { class: 'step-sep' }, [el('span', {}, [el('span', { class: 'n', $text: `STEP ${blk.n}` }), ` · 第 ${blk.n} 步`])]);
  }
  if (blk.t === 'assistant') {
    return el('div', { class: 'md proc-md', $html: mdRender(blk.text || '') });
  }
  if (blk.t === 'tool_use') return makeToolCard(blk);
  if (blk.t === 'tool_result') {
    // 孤立 result（历史重渲时若对应 tool_use 卡已存在则不走到这里）
    const card = makeToolCard({ id: blk.id, name: blk.name, args: '' });
    card._setResult(blk);
    return card;
  }
  return null;
}

/** 主体块：final / error */
function renderFinalBody(blk) {
  if (blk.success === false) {
    return el('div', { class: 'final-wrap' }, [
      el('div', { class: 'errbox', $text: blk.text || '（无输出，任务失败）' }),
      el('span', { class: 'chip red final-badge' }, ['✕ 失败']),
    ]);
  }
  return el('div', { class: 'final-wrap' }, [
    el('div', { class: 'md', $html: mdRender(blk.text || '（无输出）') }),
    el('span', { class: 'chip green final-badge' }, ['✓ 完成']),
  ]);
}
function renderErrorBody(msg) {
  return el('div', { class: 'errbox', $text: `执行出错：${msg || '未知错误'}` });
}

/* ---------------- 历史整段重渲（final 主体 + 过程折叠） ---------------- */
function renderAllMsgs() {
  const b = msgsBox();
  const sess = curSess();
  if (!sess.msgs.length) { emptyHint(); renderStepBadge(0); return; }
  b.innerHTML = '';
  let lastStep = 0;
  for (const m of sess.msgs) {
    if (m.k === 'user') { appendUserMsg(m.text); continue; }
    const ai = aiMsgContainer();
    const blocks = m.blocks || [];
    const finals = blocks.filter((x) => x.t === 'final');
    const errors = blocks.filter((x) => x.t === 'error');
    const proc = blocks.filter((x) => x.t !== 'final' && x.t !== 'error');
    for (const x of proc) if (x.t === 'step') lastStep = Math.max(lastStep, Number(x.n) || 0);

    // 主体：final 优先；缺失时回退到 assistant 中间文本（流被中断的旧消息）
    if (finals.length) {
      ai.body.append(renderFinalBody(finals[finals.length - 1]));
    } else {
      const asm = blocks.filter((x) => x.t === 'assistant').map((x) => x.text || '').join('\n\n').trim();
      if (asm) {
        ai.body.append(el('div', { class: 'md', $html: mdRender(asm) }));
        ai.body.append(el('span', { class: 'chip amber final-badge' }, ['⚠ 未收到最终答复（历史中断）']));
      }
    }
    for (const e2 of errors.slice(0, 1)) ai.body.append(renderErrorBody(e2.message));

    // 过程折叠
    if (proc.length) {
      const fold = makeFold();
      const seen = {};
      for (const blk of proc) {
        if (blk.t === 'step') fold.setStep(Number(blk.n) || 0);
        if (blk.t === 'tool_result') fold.setOp();
        let node = null;
        if (blk.t === 'tool_use') { node = makeToolCard(blk); if (blk.id != null) seen[blk.id] = node; }
        else if (blk.t === 'tool_result' && blk.id != null && seen[blk.id]) { seen[blk.id]._setResult(blk); continue; }
        else node = renderProcBlock(blk);
        if (node) fold.body.append(node);
      }
      fold.show();
      ai.body.append(fold.wrap);
    }
  }
  renderStepBadge(lastStep);
  scrollBottom(true);
}

function renderStepBadge(n) { $('#stepBadge').textContent = `步数 ${n}`; }

/* ---------------- 发送 / SSE ---------------- */
let running = false;
let abortCtl = null;

function setRunning(on) {
  running = on;
  setState({ running: on });
  $('#btnStop').classList.toggle('hidden', !on);
  $('#btnSend').disabled = on || !$('#input').value.trim();
  $('#runState').classList.toggle('hidden', !on);
}
function updateSending() { $('#btnSend').disabled = running || !$('#input').value.trim(); }

async function send() {
  const input = $('#input');
  const text = input.value.trim();
  if (!text || running) return;

  const sess = curSess();
  if (sess.msgs.length === 0 || sess.msgs[sess.msgs.length - 1].k === 'ai') {
    sess.title = text.slice(0, 26);
  }
  sess.msgs.push({ k: 'user', text });
  saveSessions();
  renderSessList();

  if (sess.msgs.length === 1) msgsBox().innerHTML = ''; // 清掉空态提示
  appendUserMsg(text);
  input.value = '';
  updateSending();

  const ai = { k: 'ai', blocks: [] };
  sess.msgs.push(ai);
  const view = aiMsgContainer();

  /* 思考区 + 过程折叠区 */
  const think = makeThinking();
  const fold = makeFold();
  view.body.append(fold.wrap); // 先占位（隐藏），final 时 show；主体插到它前面
  view.body.append(think.box);
  scrollBottom();

  let step = 0;
  let opCount = 0;
  let toolNodes = {};  // tool_use id → 卡片节点（过程折叠区内）
  let procMd = null;   // 过程区的 assistant 文本块（拼接用）
  let finalBlk = null;
  let mainNode = null; // 主体节点（final 渲染产物）

  const pushBlk = (blk) => { ai.blocks.push(blk); saveSessions(); };

  const onEvent = (evt) => {
    switch (evt.type) {
      case 'step': {
        step = Number(evt.n) || step + 1;
        renderStepBadge(step);
        fold.setStep(step);
        fold.body.append(renderProcBlock({ t: 'step', n: step }));
        procMd = null;
        $('#runStateText').textContent = `第 ${step} 步`;
        break;
      }
      case 'assistant': {
        // 中间文本：归入过程折叠区（不进主流程）
        const text2 = evt.text || '';
        const lastBlk = ai.blocks[ai.blocks.length - 1];
        if (procMd && lastBlk && lastBlk.t === 'assistant' && lastBlk._proc) {
          lastBlk.text = lastBlk.text ? lastBlk.text + '\n\n' + text2 : text2;
          procMd.innerHTML = mdRender(lastBlk.text);
        } else {
          const blk = { t: 'assistant', text: text2, _proc: true };
          ai.blocks.push(blk);
          procMd = el('div', { class: 'md proc-md', $html: mdRender(text2) });
          fold.body.append(procMd);
        }
        saveSessions();
        break;
      }
      case 'tool_use': {
        procMd = null;
        think.setTool(evt.name);
        const blk = { t: 'tool_use', id: evt.id, name: evt.name, args: evt.args };
        const node = makeToolCard(blk);
        if (evt.id != null) toolNodes[evt.id] = node;
        fold.body.append(node);
        pushBlk(blk);
        break;
      }
      case 'tool_result': {
        procMd = null;
        opCount++;
        think.setOps(opCount);
        fold.setOp();
        $('#runStateText').textContent = `运行中 · ${opCount} 操作`;
        const blk = { t: 'tool_result', id: evt.id, name: evt.name, output: evt.output, success: evt.success };
        const matched = evt.id != null && toolNodes[evt.id];
        if (matched) matched._setResult(evt);
        else fold.body.append(renderProcBlock(blk));
        pushBlk(blk);
        break;
      }
      case 'final': {
        mainNode?.remove();
        mainNode = renderFinalBody({ t: 'final', text: evt.text, success: evt.success });
        fold.wrap.before(mainNode);
        fold.show();
        finalBlk = { t: 'final', text: evt.text, success: evt.success };
        pushBlk(finalBlk);
        if (evt.success === false) toast('任务执行失败，详见消息', 'err');
        break;
      }
      case 'error': {
        mainNode?.remove();
        mainNode = renderErrorBody(evt.message);
        fold.wrap.before(mainNode);
        fold.show();
        pushBlk({ t: 'error', message: evt.message });
        toast(`执行出错：${evt.message || '未知'}`, 'err', 5000);
        break;
      }
      case 'hb': break;
      default: break;
    }
    scrollBottom();
  };

  abortCtl = new AbortController();
  setRunning(true);
  $('#runStateText').textContent = '运行中';
  let lastEvt = null;
  try {
    const payload = { message: text };
    if (store.activeModel) payload.model_key = store.activeModel;
    lastEvt = await streamChat(payload, { onEvent, signal: abortCtl.signal });
  } catch (e) {
    const aborted = e?.name === 'AbortError';
    const msg = aborted ? '已手动停止' : fmtErr(e);
    mainNode?.remove();
    mainNode = renderErrorBody(msg);
    fold.wrap.before(mainNode);
    // 用户主动停止：给主体加个轻提示
    if (aborted) mainNode.append(el('span', { class: 'chip amber final-badge', style: { marginTop: '6px' } }, ['✋ 已手动停止']));
    pushBlk({ t: 'error', message: msg });
    if (!aborted) toast(`对话失败：${msg}`, 'err', 5000);
  } finally {
    // 流结束：拆思考区，final 缺失时做回退渲染
    think.stopTimer();
    const hasFinal = ai.blocks.some((x) => x.t === 'final');
    think.remove();
    if (!hasFinal) {
      fold.show();
      if (!mainNode) {
        const asm = ai.blocks.filter((x) => x.t === 'assistant').map((x) => x.text || '').join('\n\n').trim();
        if (asm) {
          mainNode = el('div', { class: 'md' }, []);
          fold.wrap.before(mainNode);
          mainNode.innerHTML = mdRender(asm);
          mainNode.after(el('span', { class: 'chip amber final-badge' }, ['⚠ 未收到最终答复（结果可能不完整）']));
        } else if (lastEvt && lastEvt.type === 'error') {
          mainNode = renderErrorBody(lastEvt.message);
          fold.wrap.before(mainNode);
        } else {
          fold.wrap.before(el('div', { class: 'errbox', $text: '流式连接结束但未收到最终答复（final），结果可能不完整' }));
        }
      } else {
        fold.wrap.before(el('span', { class: 'chip amber final-badge' }, ['⚠ 未收到最终答复（结果可能不完整）']));
      }
    }
    setRunning(false);
    renderStepBadge(step);
    saveSessions();
    scrollBottom();
    input.focus();
  }
}

/* ---------------- 初始化 ---------------- */
export function init() {}

loadSessions();
renderSessList();
renderAllMsgs();
initFiles();

const input = $('#input');
input.addEventListener('input', updateSending);
// 输入框自动伸缩
input.addEventListener('input', () => {
  input.style.height = 'auto';
  input.style.height = Math.min(input.scrollHeight, 200) + 'px';
});
// Enter 直接发送；Shift+Enter 换行（textarea 默认）；Ctrl+Enter 兼容保留
input.addEventListener('keydown', (ev) => {
  if (ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing) {
    ev.preventDefault();
    send();
  }
});
$('#btnSend').addEventListener('click', send);
$('#btnStop').addEventListener('click', () => { abortCtl?.abort(); toast('正在停止…', 'info', 1500); });
$('#btnNewSess').addEventListener('click', () => {
  const s = blankSess();
  sessions.push(s);
  curId = s.id;
  saveSessions(); renderSessList(); renderAllMsgs();
  input.focus();
});
updateSending();
