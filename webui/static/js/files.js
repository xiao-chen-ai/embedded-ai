// files.js — 聊天侧栏：workspace 文件树 / 上传（按钮+拖拽文件/目录）/ 查看 / 删除 / 下载
import { $, $$, el, icon, toast, fmtErr, fmtBytes, showTextModal } from './util.js';
import { apiFiles, apiFile, apiUpload, apiDeletePath, downloadURL } from './api.js';

const VIEW_LIMIT = 120 * 1024; // modal 只渲染前 120KB 文本
let inited = false;
let loadedOnce = false;

/* ---------- 工具 ---------- */
const norm = (p) => String(p || '').replace(/\\/g, '/');
function cmpEntry(a, b) {
  if (a.dir !== b.dir) return a.dir ? -1 : 1;
  return String(a.name).localeCompare(String(b.name), 'zh-Hans-CN', { numeric: true });
}

/* ---------- 树构建（平铺 entries → 嵌套；目录占位升级防御） ---------- */
function buildTree(entries) {
  const root = { dir: true, name: '', path: '', children: new Map() };
  for (const e of entries || []) {
    const parts = norm(e.path).split('/').filter(Boolean);
    if (!parts.length) continue;
    let node = root;
    for (let i = 0; i < parts.length - 1; i++) {
      const p = parts.slice(0, i + 1).join('/');
      let mid = node.children.get(parts[i]);
      if (!mid) { mid = { dir: true, name: parts[i], path: p, children: new Map() }; node.children.set(parts[i], mid); }
      if (!mid.children) { mid.children = new Map(); mid.dir = true; }
      node = mid;
    }
    const leaf = parts[parts.length - 1];
    let kid = node.children.get(leaf);
    if (!kid) {
      kid = { dir: !!e.dir, name: leaf, path: norm(e.path) };
      if (kid.dir) kid.children = new Map();
      node.children.set(leaf, kid);
    } else if (e.dir) {
      kid.dir = true;
      if (!kid.children) kid.children = new Map();
    }
  }
  return root;
}

/* ---------- 节点操作小按钮（hover 出现） ---------- */
function nodeActions(kid) {
  const bg = el('span', { class: 'tag-acts' });

  // 下载（文件或目录：目录自动 zip）
  bg.append(el('button', {
    class: 'tag-act', title: kid.dir ? '下载目录（打包 zip）' : '下载文件',
    onclick: (ev) => {
      ev.stopPropagation();
      const a = document.createElement('a');
      a.href = downloadURL(kid.path || '');
      a.download = '';
      document.body.append(a); a.click(); a.remove();
      toast(kid.dir ? `正在打包下载 ${kid.name}.zip…` : `正在下载 ${kid.name}…`, 'info', 2200);
    },
  }, [icon('download')]));

  // 删除：inline 二次确认（3s 超时取消）
  const del = el('button', { class: 'tag-act del', title: '删除' }, [icon('trash')]);
  let timer = null;
  del.addEventListener('click', (ev) => {
    ev.stopPropagation();
    if (del.dataset.confirm) {
      clearTimeout(timer);
      doDelete(kid, del);
      return;
    }
    del.dataset.confirm = '1';
    del.textContent = '确认删除？';
    del.classList.add('confirming');
    del.title = kid.dir ? `删除目录 ${kid.name}（将递归删除目录内全部内容）` : `删除文件 ${kid.name}`;
    if (kid.dir) toast(`注意：将递归删除目录「${kid.path}」内全部内容`, 'info', 3000);
    timer = setTimeout(() => {
      del.innerHTML = ''; del.append(icon('trash'));
      del.classList.remove('confirming'); delete del.dataset.confirm;
    }, 3000);
  });
  bg.append(del);
  return bg;
}

async function doDelete(kid, btn) {
  btn.disabled = true;
  try {
    const r = await apiDeletePath(kid.path);
    toast(`已删除 ${r?.deleted || kid.path}`, 'ok');
    loadTree(true);
  } catch (e) {
    toast(`删除失败：${fmtErr(e)}`, 'err', 5000);
    btn.disabled = false;
    btn.innerHTML = ''; btn.append(icon('trash'));
    btn.classList.remove('confirming'); delete btn.dataset.confirm;
  }
}

/* ---------- 树渲染 ---------- */
function renderTreeNode(node, box, depth) {
  const kids = [...(node.children?.values() || [])].sort(cmpEntry);
  for (const kid of kids) {
    if (kid.dir) {
      if (!kid.children) kid.children = new Map();
      const children = el('div', { class: 'tree-children' });
      children.style.display = 'none';
      const item = el('div', { class: 'tree-item dir' }, [
        el('span', { class: 'caret' }, [icon('chevron')]),
        el('span', { class: 'ico' }, [icon('folder')]),
        el('span', { class: 'name', $text: kid.name }),
        nodeActions(kid),
      ]);
      item.addEventListener('click', () => {
        const open = children.style.display !== 'none';
        children.style.display = open ? 'none' : '';
        item.classList.toggle('open', !open);
      });
      renderTreeNode(kid, children, depth + 1);
      box.append(item, children);
    } else {
      const item = el('div', { class: 'tree-item file' }, [
        el('span', { class: 'caret' }, ['']),
        el('span', { class: 'ico' }, [icon('file')]),
        el('span', { class: 'name', $text: kid.name }),
        kid.size != null ? el('span', { class: 'sz', $text: fmtBytes(kid.size) }) : null,
        nodeActions(kid),
      ]);
      item.addEventListener('click', () => { $$('.tree-item.file.active').forEach((x) => x.classList.remove('active')); item.classList.add('active'); viewFile(kid.path); });
      box.append(item);
    }
  }
}

/* ---------- 三态 ---------- */
function treeLoading() {
  const w = $('#treeWrap');
  w.innerHTML = '';
  w.append(el('div', { class: 'state' }, [el('span', { class: 'spinner' }), el('span', {}, ['正在加载文件列表…'])]));
}
function treeEmpty() {
  const w = $('#treeWrap');
  w.innerHTML = '';
  w.append(el('div', { class: 'state' }, [el('span', { class: 'glyph' }, ['□']), el('span', {}, ['Workspace 暂无文件'])
    , el('span', { style: { fontSize: '12px' } }, ['点击右上「↑ 上传」或把文件拖到此处'])]));
}
function treeError(msg) {
  const w = $('#treeWrap');
  w.innerHTML = '';
  w.append(el('div', { class: 'state error' }, [
    el('span', { class: 'glyph' }, ['!!']),
    el('span', { $text: `文件列表加载失败：${msg}` }),
    el('button', { class: 'btn sm retry', onclick: () => loadTree() }, ['重试']),
  ]));
}

async function loadTree(silent = false) {
  treeLoading();
  try {
    const data = await apiFiles('', true);
    const entries = Array.isArray(data?.files) ? data.files : data?.entries;
    if (!Array.isArray(entries)) throw new Error('响应缺少 files/entries 数组');
    loadedOnce = true;
    if (!entries.length) { treeEmpty(); return; }
    const w = $('#treeWrap');
    w.innerHTML = '';
    const tree = el('div', { class: 'tree' });
    renderTreeNode(buildTree(entries), tree, 0);
    w.append(tree);
  } catch (e) {
    treeError(fmtErr(e));
    if (!silent) toast(`文件列表加载失败：${fmtErr(e)}`, 'err');
  }
}

/* ---------- 文件查看 ---------- */
async function viewFile(path) {
  try {
    const data = await apiFile(path);
    let content = data?.content ?? '';
    const size = data?.size;
    const truncated = data?.truncated === true || content.length > VIEW_LIMIT;
    if (truncated) {
      content = content.slice(0, VIEW_LIMIT) + `\n\n…（文件原大小 ${fmtBytes(size || content.length)}${data?.truncated ? '，服务端已截断' : ''}，仅显示前 ${fmtBytes(VIEW_LIMIT)}）`;
    }
    showTextModal('文件预览', path, content || '（空文件或二进制内容）', size);
  } catch (e) {
    toast(`读取文件失败：${fmtErr(e)}`, 'err');
  }
}

/* ---------- 上传（含目录拖拽） ---------- */
async function doUpload(fileList) {
  const files = [...fileList].map((f) => {
    // 目录拖拽场景：webkitRelativePath / 手工构造的 File.name 已含相对路径
    const rel = (f.webkitRelativePath && f.webkitRelativePath.includes('/')) ? f.webkitRelativePath : f.name;
    if (rel === f.name) return f;
    return new File([f], rel); // 用完整相对路径作为 name，后端按路径落盘
  });
  if (!files.length) return;
  toast(`正在上传 ${files.length} 个文件…`, 'info', 2500);
  try {
    const data = await apiUpload(files);
    const nameOf = (s) => (typeof s === 'string' ? s : (s?.name || s?.path || ''));
    const saved = Array.isArray(data?.saved) ? data.saved
      : Array.isArray(data?.files) ? data.files : [];
    const skipped = Array.isArray(data?.skipped) ? data.skipped
      : Array.isArray(data?.rejected) ? data.rejected : [];
    const unzipped = Array.isArray(data?.unzipped) ? data.unzipped : [];
    if (saved.length) toast(`已保存 ${saved.length} 个文件：${saved.slice(0, 3).map(nameOf).join('、')}${saved.length > 3 ? ' 等' : ''}`, 'ok', 4000);
    if (unzipped.length) toast(`zip 已解包：${unzipped.slice(0, 3).map(nameOf).join('、')}${unzipped.length > 3 ? ' 等' : ''}`, 'ok', 4000);
    if (skipped.length) {
      const first = skipped[0];
      const why = typeof first === 'object' && first?.reason ? `（${first.reason}）` : '';
      toast(`跳过 ${skipped.length} 个：${skipped.slice(0, 3).map(nameOf).join('、')}${skipped.length > 3 ? ' 等' : ''}${why}`, 'info', 5000);
    }
    if (!saved.length && !skipped.length && !unzipped.length) toast('服务未返回保存结果', 'info');
    loadTree(true);
  } catch (e) {
    toast(`上传失败：${fmtErr(e)}`, 'err', 5000);
  }
}

/** dataTransferItem 递归收集目录内文件（webkitGetAsEntry） */
async function collectDropFiles(dataTransfer) {
  const out = [];
  const items = [...(dataTransfer?.items || [])];
  const entries = items
    .filter((it) => it.kind === 'file')
    .map((it) => (typeof it.webkitGetAsEntry === 'function' ? it.webkitGetAsEntry() : null))
    .filter(Boolean);
  if (!entries.length) return [...(dataTransfer?.files || [])];

  const readDir = (dirEntry) => new Promise((resolve) => {
    const reader = dirEntry.createReader();
    const all = [];
    const readBatch = () => reader.readEntries(async (batch) => {
      if (!batch.length) { resolve(all); return; }
      all.push(...batch);
      readBatch(); // readEntries 每次最多 100 条，必须循环读到空
    }, () => resolve(all));
    readBatch();
  });

  const walk = async (entry, prefix) => {
    if (entry.isFile) {
      const file = await new Promise((res, rej) => entry.file(res, rej)).catch(() => null);
      if (file) out.push(new File([file], prefix ? `${prefix}/${file.name}` : file.name));
      return;
    }
    const children = await readDir(entry);
    for (const c of children) await walk(c, prefix ? `${prefix}/${entry.name}` : entry.name);
  };

  for (const entry of entries) await walk(entry, '');
  return out;
}

/* ---------- 绑定 ---------- */
export function initFiles() {
  if (inited) return;
  inited = true;

  $('#btnUpload').addEventListener('click', () => $('#fileInput').click());
  $('#fileInput').addEventListener('change', (ev) => { doUpload(ev.target.files); ev.target.value = ''; });
  // 目录上传：webkitRelativePath 携带相对结构，后端按路径还原目录树
  const dirInput = $('#dirInput');
  $('#btnUploadFolder').addEventListener('click', () => dirInput.click());
  dirInput.addEventListener('change', (ev) => { doUpload(ev.target.files); ev.target.value = ''; });
  $('#btnRefreshTree').addEventListener('click', () => loadTree());

  // 全部下载（工作区 zip）
  $('#btnDownloadAll').addEventListener('click', () => {
    const a = document.createElement('a');
    a.href = downloadURL('');
    a.download = '';
    document.body.append(a); a.click(); a.remove();
    toast('正在打包下载 workspace.zip…', 'info', 2500);
  });

  // 首次切到「文件」子页才加载
  $$('.side-tab').forEach((b) => b.addEventListener('click', () => {
    if (b.dataset.side === 'files' && !loadedOnce) loadTree(true);
  }));

  // 拖拽（支持文件与目录）到整个侧栏
  const dz = $('#dropzone');
  ['dragenter', 'dragover'].forEach((n) => dz.addEventListener(n, (ev) => {
    ev.preventDefault(); ev.stopPropagation();
    if (![...(ev.dataTransfer?.types || [])].includes('Files')) return;
    dz.classList.add('drag');
  }));
  dz.addEventListener('dragleave', (ev) => {
    ev.preventDefault(); ev.stopPropagation();
    if (dz.contains(ev.relatedTarget)) return;
    dz.classList.remove('drag');
  });
  dz.addEventListener('drop', async (ev) => {
    ev.preventDefault(); ev.stopPropagation();
    dz.classList.remove('drag');
    if (!ev.dataTransfer) return;
    // 优先 entries（目录支持），回退 files
    const files = await collectDropFiles(ev.dataTransfer).catch(() => [...(ev.dataTransfer?.files || [])]);
    if (!files?.length) return;
    $$('.side-tab').forEach((x) => x.classList.toggle('active', x.dataset.side === 'files'));
    $('#side-sessions').classList.add('hidden');
    $('#side-files').classList.remove('hidden');
    doUpload(files);
  });
}
