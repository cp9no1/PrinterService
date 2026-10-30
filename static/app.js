/* ============================================================
 * 内网打印服务 PrintServer v3 — 前端逻辑
 * 原生 ES2020，无第三方依赖，无外部网络请求。
 *
 * 目录：
 *   1. 常量与工具        2. 运行状态
 *   3. API 封装          4. Toast / 弹窗
 *   5. 顶栏 / 环境       6. 步骤导航
 *   7. 上传与文件列表    8. 预览查看器
 *   9. 打印设置          10. 打印执行 / 日志
 *   11. 初始化
 * ============================================================ */
(function () {
  'use strict';

  /* ==========================================================
   * 1. 常量与工具
   * ========================================================== */

  const $ = function (id) { return document.getElementById(id); };
  const BOOT = (window.PRINTSERVER_BOOT && typeof window.PRINTSERVER_BOOT === 'object')
    ? window.PRINTSERVER_BOOT : {};

  const VIEWER_MIN = 0.25;   // 25%
  const VIEWER_MAX = 4;      // 400%
  const ZOOM_STEP = 1.25;

  const PAPER_MM = {
    A4: { w: 210, h: 297 },
    A5: { w: 148, h: 210 },
    Letter: { w: 215.9, h: 279.4 },
    '4R': { w: 102, h: 152 },
    '5R': { w: 127, h: 178 }
  };

  const KIND_META = {
    image: { emoji: '\u{1F5BC}\u{FE0F}', label: '图片' },
    pdf: { emoji: '\u{1F4D5}', label: 'PDF' },
    text: { emoji: '\u{1F4DD}', label: '文本' },
    doc: { emoji: '\u{1F4C4}', label: 'Word' },
    sheet: { emoji: '\u{1F4CA}', label: 'Excel' },
    slide: { emoji: '\u{1F4FD}\u{FE0F}', label: 'PPT' },
    other: { emoji: '\u{1F4CE}', label: '文件' }
  };

  const FIT_HINTS = {
    fit: '适应页面（默认）：完整显示整张图片，四周留白；手机照片想打更大可选「铺满页面」。',
    fill: '铺满页面：裁切四边铺满整张纸，打印出来最大，推荐用于手机照片。',
    actual: '原始大小：按图片原始尺寸放置，可能超出纸张。'
  };

  const MAX_PAGE_RANGE_LEN = 100; // 后端 page_range 最多保留 100 个字符

  const TOAST_ICONS = { success: '\u2713', error: '!', warning: '!', info: 'i' };

  function h(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function clear(node) {
    while (node && node.firstChild) node.removeChild(node.firstChild);
  }

  function append(parent) {
    for (let i = 1; i < arguments.length; i++) {
      if (arguments[i]) parent.appendChild(arguments[i]);
    }
    return parent;
  }

  function clamp(value, lo, hi) {
    return Math.min(hi, Math.max(lo, value));
  }

  function uid() {
    return 'q' + Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
  }

  function formatSize(bytes) {
    const n = Number(bytes);
    if (!isFinite(n) || n <= 0) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB'];
    const i = Math.min(units.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
    const value = n / Math.pow(1024, i);
    const fixed = i === 0 ? String(Math.round(value)) : value.toFixed(value >= 100 ? 0 : 1);
    return fixed + ' ' + units[i];
  }

  function kindMeta(kind) {
    return KIND_META[kind] || KIND_META.other;
  }

  function kindLabel(kind) {
    return kindMeta(kind).label;
  }

  function isImage(file) { return !!file && file.kind === 'image'; }
  function isSheet(file) { return !!file && file.kind === 'sheet'; }

  function isPrintable(file) {
    if (!file || !file.name) return false;
    if (file.converted === true) return true;
    if (file.kind === 'image' || file.kind === 'pdf' || file.kind === 'text') return true;
    const status = String(file.status || '');
    if (file.status_color === 'success' || status.indexOf('就绪') >= 0) return true;
    return false;
  }

  function displayName(file) {
    return (file && (file.display_name || file.name)) || '文件';
  }

  /** 管理员预览其他会话文件时，为其 API 请求附加 ?sid=（仅本机管理员有效）。 */
  function sidOf(file) {
    return (file && file._sid) ? String(file._sid) : '';
  }

  function withSid(url, file) {
    const sid = sidOf(file);
    if (!sid) return url;
    return url + (url.indexOf('?') >= 0 ? '&' : '?') + 'sid=' + encodeURIComponent(sid);
  }

  /** 纸张的 CSS 像素尺寸（96 DPI） */
  function paperPixelSize(paper, orientation) {
    const base = PAPER_MM[paper] || PAPER_MM.A4;
    const landscape = orientation === 'landscape';
    const wmm = landscape ? base.h : base.w;
    const hmm = landscape ? base.w : base.h;
    return {
      wmm: wmm,
      hmm: hmm,
      w: Math.round((wmm / 25.4) * 96),
      h: Math.round((hmm / 25.4) * 96)
    };
  }

  function statusBadgeInfo(file) {
    if (!isPrintable(file)) {
      const text = (file && file.status && String(file.status).trim()) || '不可打印';
      return { cls: 'badge-danger', text: /就绪/.test(text) ? '不可打印' : text };
    }
    const colorMap = {
      success: 'badge-success',
      danger: 'badge-danger',
      warning: 'badge-warning',
      info: 'badge-info',
      primary: 'badge-info',
      secondary: 'badge-neutral',
      dark: 'badge-neutral'
    };
    const cls = colorMap[file.status_color] || 'badge-success';
    const text = (file.status && String(file.status).trim()) || '已就绪';
    return { cls: cls, text: text };
  }

  function errorMessage(err, fallback) {
    if (err && err.isNetwork) return '无法连接打印服务，请检查网络或服务是否已启动。';
    if (err && err.message) {
      const msg = String(err.message);
      if (/failed to fetch|networkerror|load failed/i.test(msg)) {
        return '网络连接失败，请检查服务是否在线。';
      }
      return msg;
    }
    return fallback || '操作失败，请稍后重试。';
  }

  /* ==========================================================
   * 2. 运行状态
   * ========================================================== */

  const state = {
    version: BOOT.version || '3.0',
    server: BOOT.server || null,
    env: BOOT.env || null,
    isAdmin: BOOT.is_admin === true,
    maxUploadMb: (Number(BOOT.max_upload_mb) > 0) ? Number(BOOT.max_upload_mb) : 200,

    step: 1,
    healthTimer: null,

    files: [],                 // 当前会话文件（/api/files）
    adminSessions: [],         // 管理员：所有会话（/api/files/all）
    printers: [],
    defaultPrinter: '',
    printersLoaded: false,

    uploads: [],               // 本次上传队列
    uploadBatchSize: 0,

    previewFile: null,         // 步骤 2 当前文件对象
    preview: {
      page: 1,
      pageCount: 1,
      layoutUrl: null,
      layoutToken: 0,
      renderToken: 0,
      failed: false,
      isLayout: false
    },

    viewer: {
      scale: 1,
      tx: 0,
      ty: 0,
      mode: 'fit',             // fit | manual
      pageW: 794,
      pageH: 1123,
      stageW: 0,
      stageH: 0
    },

    focusedName: null,         // 步骤 3 参数对象（file.name）
    perFile: {},               // name -> { selected, rangeMode, pageRange, sheet, printArea, layout }
    pageCounts: {},            // name -> Promise<number> 缓存

    settings: {
      printer: '',
      copies: 1,
      duplex: 1,
      paper: 'A4',
      orientation: 'portrait',
      quality: 'normal'
    },

    excelSheets: {},           // name -> { sheets: [...], loaded: bool, loading: bool }
    logs: [],

    printing: false,
    lastFailed: [],
    lastResults: []
  };

  function ensurePerFile(name) {
    if (!state.perFile[name]) {
      state.perFile[name] = {
        selected: true,
        rangeMode: 'all',
        pageRange: 'all',
        sheet: '',
        printArea: '',
        layout: defaultLayout()
      };
    }
    return state.perFile[name];
  }

  function defaultLayout() {
    return { fit: 'fit', rotate: 0, margin: 5, paper: 'A4', orientation: 'portrait' };
  }

  function layoutFor(file) {
    return ensurePerFile(file.name).layout;
  }

  function findFile(name) {
    if (!name) return null;
    if (state.previewFile && state.previewFile.name === name) return state.previewFile;
    return state.files.find(function (f) { return f.name === name; }) || null;
  }

  function printableFiles() {
    return state.files.filter(isPrintable);
  }

  function selectedPrintFiles() {
    return printableFiles().filter(function (f) {
      return ensurePerFile(f.name).selected;
    });
  }

  /* ==========================================================
   * 3. API 封装
   * ========================================================== */

  async function apiJson(path, options) {
    const opts = Object.assign({ credentials: 'same-origin', cache: 'no-store' }, options || {});
    let res;
    try {
      res = await fetch(path, opts);
    } catch (err) {
      const e = new Error('network');
      e.isNetwork = true;
      throw e;
    }
    let data = null;
    try { data = await res.json(); } catch (err) { data = null; }
    if (!res.ok) {
      const msg = (data && (data.message || data.error)) || ('请求失败（HTTP ' + res.status + '）');
      const e = new Error(msg);
      e.status = res.status;
      throw e;
    }
    if (data && data.success === false) {
      const e = new Error(data.message || '操作失败');
      e.status = res.status;
      throw e;
    }
    return data;
  }

  function postJson(path, body) {
    return apiJson(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {})
    });
  }

  /* ==========================================================
   * 4. Toast / 弹窗
   * ========================================================== */

  function toast(type, title, message, duration) {
    const root = $('toastRoot');
    if (!root) return;
    const item = h('div', 'toast toast-' + (type || 'info'));
    item.setAttribute('role', type === 'error' ? 'alert' : 'status');
    const icon = h('span', 'toast-icon', TOAST_ICONS[type] || TOAST_ICONS.info);
    icon.setAttribute('aria-hidden', 'true');
    const content = h('div', 'toast-content');
    content.appendChild(h('div', 'toast-title', title || ''));
    if (message) content.appendChild(h('div', 'toast-msg', message));
    append(item, icon, content);
    root.appendChild(item);
    const ms = Number(duration) > 0 ? Number(duration) : (type === 'error' ? 5200 : 3800);
    setTimeout(function () {
      item.classList.add('is-hiding');
      setTimeout(function () { item.remove(); }, 240);
    }, ms);
  }

  const modalState = { resolve: null, lastFocus: null };

  /**
   * 通用弹窗。返回 Promise，值为被点击按钮的 value（关闭/取消为 null）。
   * options: { title, body(Node|Node[]), buttons:[{label,value,variant,onClick}], size, onMount }
   */
  function showModal(options) {
    const opts = options || {};
    return new Promise(function (resolve) {
      if (modalState.resolve) closeModal(null, true);
      modalState.resolve = resolve;
      modalState.lastFocus = document.activeElement;

      const backdrop = $('modalBackdrop');
      const modal = $('modal');
      $('modalTitle').textContent = opts.title || '提示';
      modal.classList.toggle('modal-lg', opts.size === 'lg');

      const body = $('modalBody');
      clear(body);
      const nodes = Array.isArray(opts.body) ? opts.body : (opts.body ? [opts.body] : []);
      nodes.forEach(function (n) { body.appendChild(n); });

      const foot = $('modalFoot');
      clear(foot);
      const buttons = opts.buttons && opts.buttons.length
        ? opts.buttons
        : [{ label: '关闭', value: null, variant: 'outline' }];
      buttons.forEach(function (cfg) {
        const btn = h('button', 'btn btn-' + (cfg.variant || 'outline'), cfg.label);
        btn.type = 'button';
        btn.addEventListener('click', function () {
          if (typeof cfg.onClick === 'function') {
            const keep = cfg.onClick(btn);
            if (keep === false) return;
          }
          closeModal(cfg.value === undefined ? null : cfg.value);
        });
        foot.appendChild(btn);
      });

      backdrop.hidden = false;
      document.body.classList.add('modal-open');
      const focusTarget = foot.lastElementChild || $('modalCloseBtn');
      setTimeout(function () { try { focusTarget.focus(); } catch (e) { /* ignore */ } }, 40);
      if (typeof opts.onMount === 'function') {
        try { opts.onMount({ body: body, foot: foot, modal: modal }); } catch (e) { /* ignore */ }
      }
    });
  }

  function closeModal(value, silent) {
    const backdrop = $('modalBackdrop');
    if (!backdrop || backdrop.hidden) return;
    backdrop.hidden = true;
    document.body.classList.remove('modal-open');
    const resolve = modalState.resolve;
    const lastFocus = modalState.lastFocus;
    modalState.resolve = null;
    modalState.lastFocus = null;
    if (!silent && lastFocus && typeof lastFocus.focus === 'function') {
      try { lastFocus.focus(); } catch (e) { /* ignore */ }
    }
    if (resolve) resolve(value === undefined ? null : value);
  }

  function confirmDialog(opts) {
    const body = h('div');
    if (opts.warning) body.appendChild(h('div', 'modal-warn', opts.warning));
    if (opts.message) body.appendChild(h('p', 'modal-text', opts.message));
    if (opts.node) body.appendChild(opts.node);
    if (opts.details && opts.details.length) {
      const dl = h('dl', 'confirm-list');
      opts.details.forEach(function (pair) {
        dl.appendChild(h('dt', null, pair[0]));
        dl.appendChild(h('dd', null, pair[1]));
      });
      body.appendChild(dl);
    }
    return showModal({
      title: opts.title || '请确认',
      body: body,
      buttons: [
        { label: opts.cancelText || '取消', value: false, variant: 'outline' },
        { label: opts.confirmText || '确认', value: true, variant: opts.danger ? 'danger' : 'primary' }
      ]
    }).then(function (result) { return result === true; });
  }

  /* ==========================================================
   * 5. 顶栏 / 环境状态
   * ========================================================== */

  function applyIdentity() {
    const versionChip = $('versionChip');
    if (versionChip) versionChip.textContent = 'v' + state.version;

    const server = state.server || {};
    let addr = '';
    if (server.ip && server.port) addr = server.ip + ':' + server.port;
    else if (server.ip) addr = String(server.ip);
    else addr = location.host || '本机服务';
    const chip = $('serverChip');
    if (chip) {
      chip.textContent = addr;
      chip.title = '服务地址：' + addr;
    }

    const adminBadge = $('adminBadge');
    if (adminBadge) adminBadge.hidden = !state.isAdmin;
    const qrBtn = $('qrBtn');
    if (qrBtn) qrBtn.hidden = !state.isAdmin;
    const adminCard = $('adminCard');
    if (adminCard) adminCard.hidden = !state.isAdmin;

    const maxEl = $('maxUploadText');
    if (maxEl) maxEl.textContent = String(state.maxUploadMb);

    if (document.title !== '内网打印服务') document.title = '内网打印服务';
  }

  const ENV_ROWS = [
    ['pymupdf', 'PDF 引擎'],
    ['office', 'Office 转换'],
    ['wps', 'WPS'],
    ['libreoffice', 'LibreOffice']
  ];

  function renderEnvChips() {
    const wrap = $('envStrip');
    if (!wrap) return;
    clear(wrap);
    const env = (state.env && typeof state.env === 'object') ? state.env : {};
    ENV_ROWS.forEach(function (row) {
      if (!(row[0] in env)) return;
      const on = !!env[row[0]];
      const chip = h('span', 'env-chip' + (on ? '' : ' is-off'));
      chip.appendChild(h('span', 'env-dot'));
      chip.appendChild(h('span', null, row[1] + (on ? ' 可用' : ' 不可用')));
      chip.title = row[1] + (on ? '：已检测到，相关文件可转换/预览。' : '：未检测到，相关文件可能无法转换，请安装对应组件。');
      wrap.appendChild(chip);
    });
    wrap.hidden = wrap.children.length === 0;
  }

  async function loadSessionInfo() {
    try {
      const data = await apiJson('/api/session');
      if (!data || typeof data !== 'object') return;
      if (data.version) state.version = String(data.version);
      if (data.server) state.server = data.server;
      if (data.env) state.env = data.env;
      if (data.is_admin === true) state.isAdmin = true;
      if (Number(data.max_upload_mb) > 0) state.maxUploadMb = Number(data.max_upload_mb);
      applyIdentity();
      renderEnvChips();
    } catch (err) {
      /* 会话接口不可用时使用 boot 数据，不打扰用户 */
    }
    if (state.env && state.env.pymupdf === false) {
      toast('warning', 'PDF 引擎不可用', '未检测到 PyMuPDF，PDF 预览与打印可能受限。', 6000);
    }
  }

  async function checkHealth(silent) {
    const chip = $('serverStatus');
    const text = $('serverStatusText');
    const banner = $('offlineBanner');
    try {
      const res = await fetch('/health', { cache: 'no-store', credentials: 'same-origin' });
      if (!res.ok) throw new Error('bad status');
      const data = await res.json().catch(function () { return null; });
      if (data && data.status && data.status !== 'ok') throw new Error('not ok');
      if (chip) {
        chip.className = 'status-chip is-online';
        chip.title = '打印服务运行正常';
      }
      if (text) text.textContent = '服务在线';
      if (banner) banner.hidden = true;
      return true;
    } catch (err) {
      if (chip) {
        chip.className = 'status-chip is-offline';
        chip.title = '无法连接打印服务';
      }
      if (text) text.textContent = '服务离线';
      if (banner) banner.hidden = false;
      if (!silent) toast('error', '服务离线', '无法连接打印服务，请确认服务已启动。');
      return false;
    }
  }

  /* ==========================================================
   * 6. 步骤导航
   * ========================================================== */

  function canStep2() {
    return printableFiles().length > 0
      || !!(state.previewFile && isPrintable(state.previewFile));
  }
  function canStep3() {
    return printableFiles().length > 0
      || !!(state.previewFile && isPrintable(state.previewFile));
  }

  function stepLockReason(step) {
    if (step === 2 && !canStep2()) return '请先在「上传文件」中上传至少一个可打印文件';
    if (step === 3 && !canStep3()) return '请先在「上传文件」中上传至少一个可打印文件';
    return '';
  }

  function updateStepbar() {
    const bar = $('stepbar');
    if (!bar) return;
    [1, 2, 3].forEach(function (n) {
      const btn = bar.querySelector('.step[data-step="' + n + '"]');
      if (!btn) return;
      const reasons = { 2: stepLockReason(2), 3: stepLockReason(3) };
      const locked = !!reasons[n];
      const active = state.step === n;
      const done = n < state.step && !locked;
      btn.classList.toggle('is-active', active);
      btn.classList.toggle('is-done', done && !active);
      btn.classList.toggle('is-locked', locked && !active);
      btn.setAttribute('aria-current', active ? 'step' : 'false');
      btn.setAttribute('aria-disabled', locked ? 'true' : 'false');
      if (locked && !active) btn.title = reasons[n];
      else if (n === 1) btn.title = '回到上传文件';
      else btn.title = '前往第 ' + n + ' 步';
    });
  }

  function hashStep() {
    const match = /#step=([123])\b/.exec(location.hash || '');
    return match ? Number(match[1]) : 0;
  }

  function showStep(step, options) {
    const opts = options || {};
    if (step === state.step && !opts.force) {
      if (opts.fromHash) return;
    }
    if (step === 2 && !canStep2()) {
      toast('warning', '还不能预览', '请先上传至少一个可打印文件。');
      return;
    }
    if (step === 3 && !canStep3()) {
      toast('warning', '还不能打印', '请先上传至少一个可打印文件。');
      return;
    }
    if (state.printing) {
      toast('info', '正在打印', '请等待本次打印任务完成。');
      return;
    }

    state.step = step;

    document.querySelectorAll('[data-step-panel]').forEach(function (panel) {
      const isActive = Number(panel.getAttribute('data-step-panel')) === step;
      panel.classList.toggle('is-active', isActive);
    });
    updateStepbar();

    if (!opts.fromHash) {
      try { history.replaceState(null, '', '#step=' + step); } catch (e) { /* ignore */ }
    }
    try { window.scrollTo(0, 0); } catch (e) { /* ignore */ }

    if (step === 2) {
      ensurePreviewSelection();
      refreshPreviewView();
    } else if (step === 3) {
      enterStep3();
    }
  }

  function bindStepbar() {
    const bar = $('stepbar');
    if (!bar) return;
    bar.addEventListener('click', function (event) {
      const btn = event.target.closest('.step[data-step]');
      if (!btn) return;
      const target = Number(btn.getAttribute('data-step'));
      const reason = stepLockReason(target);
      if (reason) {
        toast('info', '步骤未解锁', reason);
        return;
      }
      if (target === 3 && !state.focusedName) {
        const first = printableFiles()[0];
        if (first) setFocusFile(first.name, { silent: true });
      }
      showStep(target);
    });
  }

  /* ==========================================================
   * 7. 上传与文件列表
   * ========================================================== */

  function updateStep1Ctas() {
    const btn = $('step1NextBtn');
    const hint = $('step1NextHint');
    const printable = printableFiles();
    if (btn) btn.disabled = printable.length === 0;
    if (hint) {
      hint.classList.toggle('is-warn', printable.length === 0);
      hint.textContent = printable.length
        ? '已就绪 ' + printable.length + ' 个文件，可以继续预览与设置。'
        : '请先上传文件，才能继续预览。';
    }
    const countPill = $('fileCountPill');
    if (countPill) countPill.textContent = String(state.files.length);
    const footnote = $('filesFootnote');
    if (footnote) footnote.hidden = state.files.length === 0;
  }

  function renderFileRow(file) {
    const row = h('li', 'file-row');
    const meta = kindMeta(file.kind);
    const icon = h('span', 'file-icon', meta.emoji);
    icon.setAttribute('aria-hidden', 'true');

    const main = h('div', 'file-main');
    const name = h('p', 'file-name', displayName(file));
    name.title = displayName(file);
    const metaRow = h('div', 'file-meta');
    metaRow.appendChild(h('span', null, kindLabel(file.kind)));
    metaRow.appendChild(h('span', 'dot', '\u00B7'));
    metaRow.appendChild(h('span', null, formatSize(file.size)));
    const pages = Number(file.page_count);
    if (pages > 0) {
      metaRow.appendChild(h('span', 'dot', '\u00B7'));
      metaRow.appendChild(h('span', null, pages + ' 页'));
    }
    const badgeInfo = statusBadgeInfo(file);
    const badge = h('span', 'badge ' + badgeInfo.cls, badgeInfo.text);
    metaRow.appendChild(badge);
    append(main, name, metaRow);

    const btns = h('div', 'file-btns');
    const ready = isPrintable(file);
    const previewBtn = h('button', 'btn btn-outline btn-sm', '预览');
    previewBtn.type = 'button';
    previewBtn.disabled = !ready;
    if (!ready) previewBtn.title = '该文件不可打印，无法预览';
    previewBtn.addEventListener('click', function () {
      openPreviewByName(file.name);
    });
    const delBtn = h('button', 'btn btn-ghost btn-sm', '删除');
    delBtn.type = 'button';
    delBtn.addEventListener('click', function () { requestDeleteFile(file); });
    append(btns, previewBtn, delBtn);

    append(row, icon, main, btns);
    return row;
  }

  function renderFiles() {
    const list = $('sessionFileList');
    if (!list) return;
    clear(list);
    state.files.forEach(function (file) { list.appendChild(renderFileRow(file)); });
    const empty = $('filesEmpty');
    if (empty) empty.hidden = state.files.length > 0;
    updateStep1Ctas();
  }

  async function refreshFiles(options) {
    const opts = options || {};
    try {
      const data = await apiJson('/api/files');
      const files = Array.isArray(data) ? data : (data && Array.isArray(data.files) ? data.files : []);
      state.files = files.filter(function (f) { return f && f.name; });
      reconcileAfterFilesChange();
      renderFiles();
      updateStepbar();
      if (state.step === 3) syncStep3AfterFilesChange();
    } catch (err) {
      if (!opts.quiet) toast('error', '读取文件列表失败', errorMessage(err));
    }
  }

  function reconcileAfterFilesChange() {
    const byName = {};
    state.files.forEach(function (f) { byName[f.name] = f; });

    if (state.previewFile) {
      const updated = byName[state.previewFile.name];
      if (updated) {
        state.previewFile = updated;
      } else if (!isPrintable(state.previewFile)) {
        state.previewFile = null;
        releaseLayoutUrl();
      }
    }

    if (state.focusedName && !byName[state.focusedName]) state.focusedName = null;
    Object.keys(state.perFile).forEach(function (name) {
      if (!byName[name]) delete state.perFile[name];
    });
    if (!state.focusedName) {
      const first = printableFiles()[0];
      if (first) state.focusedName = first.name;
    }

    if (state.step === 2 && state.previewFile) renderPreviewSwitcher();
    if (state.step >= 2 && !canStep2()) {
      state.previewFile = null;
      releaseLayoutUrl();
      fallbackToUpload('会话中的文件已被清空，请重新上传。');
    }
  }

  function fallbackToUpload(message) {
    if (state.step === 1) {
      updateStepbar();
      if (message) toast('info', '请上传文件', message);
      return;
    }
    state.step = 1;
    document.querySelectorAll('[data-step-panel]').forEach(function (panel) {
      panel.classList.toggle('is-active', Number(panel.getAttribute('data-step-panel')) === 1);
    });
    updateStepbar();
    try { history.replaceState(null, '', '#step=1'); } catch (e) { /* ignore */ }
    try { window.scrollTo(0, 0); } catch (e) { /* ignore */ }
    if (message) toast('info', '已回到上传', message);
  }

  function openPreviewByName(name, fallbackFile) {
    const file = findFile(name) || fallbackFile || null;
    if (!file) {
      toast('error', '文件不存在', '请刷新文件列表后重试。');
      return;
    }
    if (!isPrintable(file)) {
      toast('warning', '无法预览', displayName(file) + ' 不可打印（转换失败或格式不支持）。');
      return;
    }
    setPreviewFile(file);
    showStep(2);
  }

  function buildConfirmFileNode(file) {
    const box = h('div', 'confirm-file');
    const icon = h('span', 'file-icon', kindMeta(file.kind).emoji);
    icon.setAttribute('aria-hidden', 'true');
    const main = h('div', 'file-main');
    main.appendChild(h('p', 'file-name', displayName(file)));
    main.appendChild(h('p', 'file-meta', kindLabel(file.kind) + ' \u00B7 ' + formatSize(file.size)));
    append(box, icon, main);
    return box;
  }

  async function requestDeleteFile(file) {
    if (!file) return;
    const ok = await confirmDialog({
      title: '删除文件',
      warning: '删除操作不可恢复，将同时删除源文件和转换后的 PDF 文件。',
      message: '确定要删除这个文件吗？',
      node: buildConfirmFileNode(file),
      confirmText: '删除',
      danger: true
    });
    if (!ok) return;
    try {
      await postJson('/delete_file', { name: file.name, sid: sidOf(file) || undefined });
      toast('success', '已删除', displayName(file));
      if (state.previewFile && state.previewFile.name === file.name) {
        state.previewFile = null;
        releaseLayoutUrl();
      }
      if (state.focusedName === file.name) state.focusedName = null;
      delete state.perFile[file.name];
      await refreshFiles({ quiet: true });
      if (state.isAdmin) refreshAdminFiles();
      if (state.step === 2) {
        if (canStep2()) ensurePreviewSelection();
        else fallbackToUpload('文件已删除，请上传新文件。');
      }
      if (state.step === 3) refreshLogs();
    } catch (err) {
      toast('error', '删除失败', errorMessage(err));
    }
  }

  /* ---------- 管理员：所有会话文件 ---------- */

  async function refreshAdminFiles() {
    if (!state.isAdmin) return;
    const wrap = $('adminSessionsWrap');
    try {
      const data = await apiJson('/api/files/all');
      const sessions = Array.isArray(data) ? [] : (data.sessions || []);
      state.adminSessions = Array.isArray(sessions) ? sessions : [];
      renderAdminSessions();
    } catch (err) {
      if (wrap) {
        clear(wrap);
        wrap.appendChild(h('p', 'admin-empty', '读取所有会话文件失败：' + errorMessage(err)));
      }
    }
  }

  function renderAdminSessions() {
    const wrap = $('adminSessionsWrap');
    if (!wrap) return;
    clear(wrap);
    const sessions = state.adminSessions || [];
    const countPill = $('adminSessionCount');
    if (countPill) countPill.textContent = String(sessions.length);
    if (!sessions.length) {
      wrap.appendChild(h('p', 'admin-empty', '暂无会话文件。'));
      return;
    }
    let rendered = 0;
    sessions.forEach(function (session) {
      const files = (session && Array.isArray(session.files)) ? session.files : [];
      if (!files.length) return;
      rendered++;
      const box = h('div', 'admin-session');
      const head = h('div', 'admin-session-head');
      const sid = String(session.sid || '');
      head.appendChild(h('code', null, sid ? sid.slice(0, 8) : '未知会话'));
      head.appendChild(h('span', null, files.length + ' 个文件'));
      box.appendChild(head);
      files.forEach(function (file) {
        if (!file || !file.name) return;
        file._sid = session.sid;
        const row = h('div', 'admin-file-row');
        const icon = h('span', 'file-icon', kindMeta(file.kind).emoji);
        icon.setAttribute('aria-hidden', 'true');
        const main = h('div', 'file-main');
        const nameEl = h('p', 'file-name', displayName(file));
        nameEl.title = displayName(file);
        main.appendChild(nameEl);
        main.appendChild(h('p', 'file-meta', kindLabel(file.kind) + ' \u00B7 ' + formatSize(file.size)));
        const btns = h('div', 'file-btns');
        const previewBtn = h('button', 'btn btn-outline btn-sm', '预览');
        previewBtn.type = 'button';
        previewBtn.disabled = !isPrintable(file);
        previewBtn.addEventListener('click', function () { openPreviewByName(file.name, file); });
        const delBtn = h('button', 'btn btn-ghost btn-sm', '删除');
        delBtn.type = 'button';
        delBtn.addEventListener('click', function () {
          requestDeleteFile(file).then(function () { refreshAdminFiles(); });
        });
        append(btns, previewBtn, delBtn);
        append(row, icon, main, btns);
        box.appendChild(row);
      });
      wrap.appendChild(box);
    });
    if (!rendered) wrap.appendChild(h('p', 'admin-empty', '暂无会话文件。'));
  }

  async function requestAdminClean() {
    if (!state.isAdmin) return;
    const ok = await confirmDialog({
      title: '清理全部会话文件',
      warning: '此操作会删除服务器上所有浏览器会话上传的文件（含转换后的 PDF），且不可恢复。',
      message: '确定要清理全部文件吗？',
      confirmText: '清理全部',
      danger: true
    });
    if (!ok) return;
    try {
      const data = await postJson('/api/admin/clean', {});
      toast('success', '已清理', (data && data.message) || '全部会话文件已清理');
      state.previewFile = null;
      state.focusedName = null;
      releaseLayoutUrl();
      state.perFile = {};
      await refreshFiles({ quiet: true });
      refreshAdminFiles();
      refreshLogs();
      if (state.step !== 1) fallbackToUpload('全部文件已清理。');
    } catch (err) {
      toast('error', '清理失败', errorMessage(err));
    }
  }

  async function requestClearMyFiles() {
    if (!state.files.length) {
      toast('info', '没有文件', '当前会话还没有上传文件。');
      return;
    }
    const count = state.files.length;
    const ok = await confirmDialog({
      title: '清空我的文件',
      warning: '将删除当前浏览器会话上传的全部文件（含转换后的 PDF），操作不可恢复。',
      message: '确定要清空这 ' + count + ' 个文件吗？',
      confirmText: '全部删除',
      danger: true
    });
    if (!ok) return;
    const files = state.files.slice();
    showBusy('正在清理文件…', '共 ' + files.length + ' 个文件');
    let okCount = 0;
    let failCount = 0;
    for (let i = 0; i < files.length; i++) {
      updateBusy((i + 1) / files.length, '正在删除 ' + (i + 1) + '/' + files.length + '：' + displayName(files[i]));
      try {
        await postJson('/delete_file', { name: files[i].name });
        okCount++;
      } catch (err) {
        failCount++;
      }
    }
    hideBusy();
    state.previewFile = null;
    state.focusedName = null;
    releaseLayoutUrl();
    state.perFile = {};
    await refreshFiles({ quiet: true });
    refreshAdminFiles();
    refreshLogs();
    if (state.step !== 1) fallbackToUpload('文件已清空。');
    if (failCount) toast('warning', '部分文件删除失败', '成功 ' + okCount + ' 个，失败 ' + failCount + ' 个。');
    else toast('success', '已清空', '已删除 ' + okCount + ' 个文件。');
  }

  /* ---------- 上传队列 ---------- */

  function renderUploadQueue() {
    const wrap = $('uploadQueueWrap');
    const list = $('uploadQueueList');
    const count = $('uploadQueueCount');
    if (!wrap || !list) return;
    clear(list);
    wrap.hidden = state.uploads.length === 0;
    if (count) count.textContent = String(state.uploads.length);
    state.uploads.forEach(function (item) { list.appendChild(renderUploadItem(item)); });
  }

  const UPLOAD_STATUS = {
    queued: '等待上传',
    uploading: '上传中',
    converting: '转换中…',
    done: '已就绪',
    failed: '上传失败'
  };

  function renderUploadItem(item) {
    const row = h('li', 'upload-item' + (item.status === 'failed' ? ' is-failed' : ''));
    const main = h('div', 'upload-main');
    const name = h('p', 'upload-name', item.name);
    name.title = item.name;
    const metaRow = h('div', 'upload-meta');
    const left = h('span', null, formatSize(item.size));
    const statusText = UPLOAD_STATUS[item.status] || item.status;
    const right = h('span', null,
      item.status === 'uploading' ? statusText + ' ' + item.progress + '%'
        : item.status === 'failed' ? (item.message || statusText)
          : statusText);
    append(metaRow, left, right);
    const progress = h('div', 'progress');
    const bar = h('div', 'progress-bar');
    bar.style.width = (item.status === 'done' ? 100 : item.progress) + '%';
    if (item.status === 'done') bar.classList.add('is-done');
    if (item.status === 'failed') bar.classList.add('is-error');
    progress.appendChild(bar);
    append(main, name, metaRow, progress);

    const actions = h('div', 'upload-actions');
    if (item.status === 'failed') {
      const retry = h('button', 'btn btn-outline btn-sm', '重试');
      retry.type = 'button';
      retry.addEventListener('click', function () {
        item.status = 'queued';
        item.progress = 0;
        item.message = '';
        startUpload(item);
      });
      const remove = h('button', 'btn btn-ghost btn-sm', '移除');
      remove.type = 'button';
      remove.addEventListener('click', function () { removeUploadItem(item); });
      append(actions, retry, remove);
    } else if (item.status === 'done') {
      const badge = h('span', 'badge badge-success', '已加入文件列表');
      const dismiss = h('button', 'btn btn-ghost btn-sm', '\u00D7');
      dismiss.type = 'button';
      dismiss.title = '移除这条上传记录';
      dismiss.addEventListener('click', function () { removeUploadItem(item); });
      append(actions, badge, dismiss);
    }
    append(row, main, actions);
    return row;
  }

  function removeUploadItem(item) {
    if (item.xhr && item.status !== 'done') {
      try { item.xhr.abort(); } catch (e) { /* ignore */ }
    }
    if (item.cleanupTimer) clearTimeout(item.cleanupTimer);
    state.uploads = state.uploads.filter(function (u) { return u !== item; });
    renderUploadQueue();
  }

  function scheduleUploadCleanup(item) {
    if (item.cleanupTimer) clearTimeout(item.cleanupTimer);
    item.cleanupTimer = setTimeout(function () {
      const latest = state.uploads.filter(function (u) { return u.status === 'done'; });
      if (latest.length === state.uploads.length) {
        state.uploads = [];
      } else {
        state.uploads = state.uploads.filter(function (u) { return u !== item; });
      }
      renderUploadQueue();
    }, 6000);
  }

  function succeedUpload(item, data) {
    item.status = 'done';
    item.progress = 100;
    item.message = data.message || '上传成功';
    renderUploadQueue();
    refreshFiles({ quiet: true });
    if (state.uploadBatchSize === 1) {
      toast('success', '上传成功', displayName({ display_name: data.display_name || item.name }) + ' \u00B7 ' + (data.status || '已就绪'));
    }
    scheduleUploadCleanup(item);
  }

  function failUpload(item, message) {
    item.status = 'failed';
    item.message = message || '上传失败';
    renderUploadQueue();
    toast('error', '上传失败', item.name + '：' + item.message);
  }

  function startUpload(item) {
    item.status = 'uploading';
    item.progress = 0;
    item.message = '';
    renderUploadQueue();

    const xhr = new XMLHttpRequest();
    item.xhr = xhr;
    try { xhr.open('POST', '/upload', true); } catch (err) { failUpload(item, '无法发起上传请求'); return; }
    xhr.withCredentials = true;

    xhr.upload.onprogress = function (event) {
      if (!event.lengthComputable) return;
      const pct = Math.min(99, Math.round((event.loaded / Math.max(1, event.total)) * 100));
      if (pct !== item.progress) {
        item.progress = pct;
        if (item.status === 'uploading') renderUploadQueue();
      }
      if (event.loaded >= event.total && item.status === 'uploading') {
        item.status = 'converting';
        renderUploadQueue();
      }
    };

    xhr.onload = function () {
      let data = null;
      try { data = JSON.parse(xhr.responseText || '{}'); } catch (err) { data = null; }
      if (xhr.status >= 200 && xhr.status < 300 && data && data.success !== false) {
        succeedUpload(item, data);
      } else {
        if (data && data.converted === false && data.name) {
        // 文件已上传但转换失败：仍刷新列表，让用户看到「转换失败」状态并可重试
        item.status = 'failed';
        item.message = data.message || '转换为 PDF 失败';
        renderUploadQueue();
        refreshFiles({ quiet: true });
        toast('warning', '文件已上传，但转换失败', item.name + '：' + item.message, 6000);
        return;
      }
      failUpload(item, (data && data.message) || ('上传失败（HTTP ' + xhr.status + '）'));
      }
    };
    xhr.onerror = function () { failUpload(item, '网络连接失败，上传未完成'); };
    xhr.ontimeout = function () { failUpload(item, '上传超时，请重试'); };

    const form = new FormData();
    form.append('file', item.file, item.name);
    try {
      xhr.send(form);
    } catch (err) {
      failUpload(item, '无法读取文件：' + errorMessage(err));
    }
  }

  function enqueueUploads(fileList) {
    const files = Array.prototype.slice.call(fileList || []).filter(function (f) {
      return f && typeof f.size === 'number';
    });
    if (!files.length) return;
    const limit = state.maxUploadMb * 1024 * 1024;
    let accepted = 0;
    files.forEach(function (file) {
      if (file.size > limit) {
        toast('error', '文件太大', file.name + ' 超过 ' + state.maxUploadMb + 'MB 上限，已跳过。');
        return;
      }
      if (file.size === 0) {
        toast('warning', '已跳过空文件', file.name);
        return;
      }
      const item = {
        id: uid(),
        file: file,
        name: file.name || '未命名文件',
        size: file.size,
        status: 'queued',
        progress: 0,
        message: '',
        xhr: null,
        cleanupTimer: null
      };
      state.uploads.push(item);
      accepted++;
      startUpload(item);
    });
    state.uploadBatchSize = accepted;
    renderUploadQueue();
    if (accepted) {
      const wrap = $('uploadQueueWrap');
      if (wrap) wrap.hidden = false;
      if (accepted > 1) toast('info', '开始上传', '共 ' + accepted + ' 个文件，正在并发上传…');
    }
  }

  function bindUploadUI() {
    const dropzone = $('dropzone');
    const fileInput = $('fileInput');
    const cameraInput = $('cameraInput');
    const pickBtn = $('pickFilesBtn');
    const cameraBtn = $('cameraBtn');

    if (fileInput) {
      fileInput.addEventListener('change', function () {
        enqueueUploads(fileInput.files);
        fileInput.value = '';
      });
    }
    if (cameraInput) {
      cameraInput.addEventListener('change', function () {
        enqueueUploads(cameraInput.files);
        cameraInput.value = '';
      });
    }
    if (pickBtn && fileInput) {
      pickBtn.addEventListener('click', function () { fileInput.click(); });
    }
    if (cameraBtn && cameraInput) {
      cameraBtn.addEventListener('click', function () { cameraInput.click(); });
    }
    if (dropzone && fileInput) {
      dropzone.addEventListener('click', function (event) {
        if (event.target.closest('button')) return;
        fileInput.click();
      });
      dropzone.addEventListener('keydown', function (event) {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          fileInput.click();
        }
      });
      let dragDepth = 0;
      dropzone.addEventListener('dragenter', function (event) {
        event.preventDefault();
        dragDepth++;
        dropzone.classList.add('is-dragover');
      });
      dropzone.addEventListener('dragover', function (event) {
        event.preventDefault();
        if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
        dropzone.classList.add('is-dragover');
      });
      dropzone.addEventListener('dragleave', function (event) {
        event.preventDefault();
        dragDepth = Math.max(0, dragDepth - 1);
        if (dragDepth === 0) dropzone.classList.remove('is-dragover');
      });
      dropzone.addEventListener('drop', function (event) {
        event.preventDefault();
        dragDepth = 0;
        dropzone.classList.remove('is-dragover');
        const dt = event.dataTransfer;
        if (dt && dt.files && dt.files.length) enqueueUploads(dt.files);
      });
    }

    document.addEventListener('dragover', function (event) { event.preventDefault(); });
    document.addEventListener('drop', function (event) {
      if (event.target && event.target.closest && event.target.closest('#dropzone')) return;
      event.preventDefault();
    });

    document.addEventListener('paste', function (event) {
      const target = event.target;
      if (target && target.closest && target.closest('input, textarea, select, [contenteditable="true"]')) return;
      const dt = event.clipboardData;
      if (!dt) return;
      const files = Array.prototype.slice.call(dt.files || []);
      if (!files.length) return;
      event.preventDefault();
      toast('info', '已粘贴 ' + files.length + ' 个文件', '正在加入上传队列…', 2600);
      enqueueUploads(files);
    });

    const refreshBtn = $('refreshFilesBtn');
    if (refreshBtn) {
      refreshBtn.addEventListener('click', function () {
        refreshFiles().then(function () {
          if (state.isAdmin) refreshAdminFiles();
          toast('success', '已刷新', '文件列表已更新。', 2200);
        });
      });
    }
    const step1Next = $('step1NextBtn');
    if (step1Next) {
      step1Next.addEventListener('click', function () {
        if (!canStep2()) {
          toast('warning', '还不能继续', '请先上传至少一个可打印文件。');
          return;
        }
        showStep(2);
      });
    }
    const retryHealth = $('retryHealthBtn');
    if (retryHealth) {
      retryHealth.addEventListener('click', function () { checkHealth(); });
    }
    const adminCard = $('adminCard');
    if (adminCard) {
      adminCard.addEventListener('toggle', function () {
        if (adminCard.open) refreshAdminFiles();
      });
    }
    const adminClean = $('adminCleanBtn');
    if (adminClean) adminClean.addEventListener('click', requestAdminClean);
  }

  /* ==========================================================
   * 8. 步骤 2：预览查看器
   * ========================================================== */

  function releaseLayoutUrl() {
    state.preview.layoutToken++;
    if (state.preview.layoutUrl) {
      try { URL.revokeObjectURL(state.preview.layoutUrl); } catch (e) { /* ignore */ }
      state.preview.layoutUrl = null;
    }
  }

  function setPreviewFile(file) {
    if (!file) return;
    if (state.previewFile && state.previewFile.name !== file.name) releaseLayoutUrl();
    state.previewFile = file;
    state.preview.page = 1;
    state.preview.failed = false;
    state.preview.needsReload = false;
    state.preview.pageCount = Number(file.page_count) > 0 ? Number(file.page_count) : 1;
    ensurePerFile(file.name);
    renderPreviewSwitcher();
    renderPreviewHeader();
    renderLayoutPanel();
    renderPreviewInfo();
    updatePagerUI();
    updatePaperSize({ resetView: true });
    loadPreviewContent({ immediate: true });
    resolvePageCount(file).then(function (count) {
      if (state.previewFile !== file) return;
      if (count > 0 && count !== state.preview.pageCount) {
        state.preview.pageCount = count;
        state.preview.page = clamp(state.preview.page, 1, count);
        updatePagerUI();
        renderPreviewHeader();
      }
    });
  }

  function ensurePreviewSelection() {
    const current = state.previewFile;
    if (current && isPrintable(current)) {
      const inList = state.files.find(function (f) { return f.name === current.name; });
      if (inList) state.previewFile = inList;
      return;
    }
    const first = printableFiles()[0] || null;
    if (first) setPreviewFile(first);
    else state.previewFile = null;
  }

  function renderPreviewSwitcher() {
    const wrap = $('previewSwitcher');
    const list = $('previewSwitcherList');
    if (!wrap || !list) return;
    const items = printableFiles().slice();
    if (state.previewFile && isPrintable(state.previewFile)
      && !items.some(function (f) { return f.name === state.previewFile.name; })) {
      items.push(state.previewFile);
    }
    clear(list);
    wrap.hidden = items.length <= 1;
    items.forEach(function (file) {
      const active = !!(state.previewFile && state.previewFile.name === file.name);
      const chip = h('button', 'file-chip' + (active ? ' is-active' : ''));
      chip.type = 'button';
      chip.setAttribute('role', 'tab');
      chip.setAttribute('aria-selected', active ? 'true' : 'false');
      chip.title = displayName(file);
      const emoji = h('span', 'chip-emoji', kindMeta(file.kind).emoji);
      emoji.setAttribute('aria-hidden', 'true');
      const name = h('span', 'chip-name', displayName(file));
      append(chip, emoji, name);
      chip.addEventListener('click', function () {
        if (state.previewFile && state.previewFile.name === file.name) return;
        setPreviewFile(file);
      });
      list.appendChild(chip);
    });
  }

  function renderPreviewHeader() {
    const title = $('previewTitle');
    const meta = $('previewMeta');
    const pageImg = $('pageImg');
    const file = state.previewFile;
    if (title) {
      title.textContent = file ? displayName(file) : '—';
      title.title = file ? displayName(file) : '';
    }
    if (meta) {
      if (!file) {
        meta.textContent = '';
      } else {
        const pageText = state.preview.pageCount > 1 ? '共 ' + state.preview.pageCount + ' 页' : '单页';
        meta.textContent = kindLabel(file.kind) + ' \u00B7 ' + formatSize(file.size) + ' \u00B7 ' + pageText;
      }
    }
    if (pageImg) pageImg.alt = file ? displayName(file) + ' 页面预览' : '页面预览';
    updateWysiwygNote();
  }

  function updateWysiwygNote() {
    const note = $('wysiwygNote');
    if (!note) return;
    const image = state.previewFile && isImage(state.previewFile);
    note.classList.toggle('is-plain', !image);
    clear(note);
    note.appendChild(h('span', null, image ? '当前预览 = 实际打印效果' : '页面预览 \u00B7 以打印机输出为准'));
  }

  function renderPreviewInfo() {
    const dl = $('previewInfo');
    if (!dl) return;
    clear(dl);
    const file = state.previewFile;
    if (!file) return;
    const rows = [
      ['文件名', displayName(file)],
      ['类型', kindLabel(file.kind)],
      ['大小', formatSize(file.size)],
      ['页数', state.preview.pageCount > 0 ? state.preview.pageCount + ' 页' : '—'],
      ['状态', statusBadgeInfo(file).text]
    ];
    if (isImage(file)) {
      const L = layoutFor(file);
      const fitText = { fill: '铺满页面', fit: '适应页面', actual: '原始大小' }[L.fit] || L.fit;
      rows.push(['图片布局', fitText + ' · 旋转 ' + L.rotate + '°']);
      rows.push(['纸张', L.paper + ' · ' + (L.orientation === 'landscape' ? '横向' : '纵向')]);
    }
    rows.forEach(function (pair) {
      dl.appendChild(h('dt', null, pair[0]));
      dl.appendChild(h('dd', null, pair[1]));
    });
  }

  function renderLayoutPanel() {
    const card = $('imageLayoutCard');
    const file = state.previewFile;
    const show = !!file && isImage(file);
    if (card) card.hidden = !show;
    if (!show) return;
    const L = layoutFor(file);
    document.querySelectorAll('#fitGroup [data-fit]').forEach(function (btn) {
      btn.classList.toggle('is-active', btn.getAttribute('data-fit') === L.fit);
    });
    document.querySelectorAll('#rotateGroup [data-rotate]').forEach(function (btn) {
      btn.classList.toggle('is-active', Number(btn.getAttribute('data-rotate')) === L.rotate);
    });
    document.querySelectorAll('#imageOrient [data-orient]').forEach(function (btn) {
      btn.classList.toggle('is-active', btn.getAttribute('data-orient') === L.orientation);
    });
    const range = $('marginRange');
    if (range) range.value = String(L.margin);
    const marginOut = $('marginValue');
    if (marginOut) marginOut.textContent = L.margin + ' mm';
    const paperSel = $('imagePaper');
    if (paperSel) paperSel.value = L.paper;
    const hint = $('fitHint');
    if (hint) hint.textContent = FIT_HINTS[L.fit] || '';
  }

  function updatePagerUI() {
    const pager = $('pager');
    const file = state.previewFile;
    const count = Math.max(1, state.preview.pageCount);
    const show = !!file && !isImage(file) && count > 1;
    if (pager) pager.hidden = !show;
    const countEl = $('pageCountText');
    if (countEl) countEl.textContent = String(count);
    const input = $('pageInput');
    if (input) {
      input.max = String(count);
      input.value = String(state.preview.page);
    }
    const prev = $('prevPageBtn');
    const next = $('nextPageBtn');
    if (prev) prev.disabled = state.preview.page <= 1;
    if (next) next.disabled = state.preview.page >= count;
  }

  function resolvePageCount(file) {
    if (!file) return Promise.resolve(1);
    if (isImage(file)) return Promise.resolve(1);
    const cached = Number(file.page_count);
    if (cached > 0) return Promise.resolve(cached);
    if (state.pageCounts[file.name]) return state.pageCounts[file.name];
    const promise = apiJson(withSid('/api/page_count/' + encodeURIComponent(file.name), file))
      .then(function (data) {
        const count = data && Number(data.page_count);
        return count > 0 ? count : 1;
      })
      .catch(function () { return 1; });
    state.pageCounts[file.name] = promise;
    return promise;
  }

  /* ---------- 页面尺寸与预览内容 ---------- */

  function isLandscapeOrientation(orientation) {
    return orientation === 'landscape' || orientation === 'reverse_landscape';
  }

  function updatePaperSize(options) {
    const opts = options || {};
    const file = state.previewFile;
    const paperEl = $('paper');
    if (!file || !paperEl) return;
    let dims;
    if (isImage(file)) {
      const L = layoutFor(file);
      dims = paperPixelSize(L.paper, L.orientation);
      paperEl.classList.add('is-layout-render');
    } else {
      const orient = isLandscapeOrientation(state.settings.orientation) ? 'landscape' : 'portrait';
      dims = paperPixelSize(state.settings.paper, orient);
      paperEl.classList.remove('is-layout-render');
    }
    const viewer = state.viewer;
    const changed = viewer.pageW !== dims.w || viewer.pageH !== dims.h;
    viewer.pageW = dims.w;
    viewer.pageH = dims.h;
    paperEl.style.width = dims.w + 'px';
    paperEl.style.height = dims.h + 'px';
    if (opts.resetView || changed) viewer.mode = 'fit';
    if (opts.applyView !== false) {
      requestAnimationFrame(function () {
        if (state.step === 2) fitViewIfNeeded();
      });
    }
  }

  function showPageState(text) {
    const stateEl = $('pageState');
    const textEl = $('pageStateText');
    const openBtn = $('openOriginalBtn');
    if (!stateEl) return;
    stateEl.hidden = false;
    stateEl.classList.remove('is-error');
    if (textEl) textEl.textContent = text || '正在生成预览…';
    if (openBtn) openBtn.hidden = true;
  }

  function hidePageState() {
    const stateEl = $('pageState');
    if (stateEl) stateEl.hidden = true;
  }

  function showPageError(text) {
    const stateEl = $('pageState');
    const textEl = $('pageStateText');
    const openBtn = $('openOriginalBtn');
    if (!stateEl) return;
    stateEl.hidden = false;
    stateEl.classList.add('is-error');
    if (textEl) textEl.textContent = text || '暂无法预览此文件。';
    if (openBtn) openBtn.hidden = !state.previewFile;
  }

  function loadPreviewContent(options) {
    const opts = options || {};
    const file = state.previewFile;
    if (!file) return;
    if (isImage(file)) {
      if (opts.immediate) {
        if (layoutDebounceTimer) { clearTimeout(layoutDebounceTimer); layoutDebounceTimer = null; }
        loadImageLayoutPreview();
      } else {
        scheduleLayoutPreview();
      }
      return;
    }
    renderPdfPage();
  }

  function renderPdfPage() {
    const file = state.previewFile;
    if (!file) return;
    const count = Math.max(1, state.preview.pageCount);
    const page = clamp(Number(state.preview.page) || 1, 1, count);
    state.preview.page = page;
    const token = ++state.preview.renderToken;
    showPageState('正在生成页面预览…');
    const pageImg = $('pageImg');
    if (pageImg) pageImg.hidden = true;
    const url = withSid('/api/render/' + encodeURIComponent(file.name) + '/' + page + '?dpi=120', file);
    const probe = new Image();
    probe.onload = function () {
      if (token !== state.preview.renderToken) return;
      state.preview.failed = false;
      state.preview.isLayout = false;
      const img = $('pageImg');
      if (!img) return;
      img.onload = null;
      img.onerror = null;
      img.src = url;
      img.hidden = false;
      hidePageState();
    };
    probe.onerror = function () {
      if (token !== state.preview.renderToken) return;
      state.preview.failed = true;
      state.preview.isLayout = false;
      const img = $('pageImg');
      if (img) img.hidden = true;
      showPageError('暂无法预览此页面，可直接进入打印设置。');
    };
    probe.src = url;
  }

  let layoutDebounceTimer = null;

  function scheduleLayoutPreview() {
    const file = state.previewFile;
    if (!file || !isImage(file)) return;
    showPageState('正在按布局生成预览…');
    if (layoutDebounceTimer) clearTimeout(layoutDebounceTimer);
    layoutDebounceTimer = setTimeout(function () {
      layoutDebounceTimer = null;
      loadImageLayoutPreview();
    }, 250);
  }

  function loadImageLayoutPreview() {
    const file = state.previewFile;
    if (!file || !isImage(file)) return;
    const L = layoutFor(file);
    const token = ++state.preview.layoutToken;
    showPageState('正在按布局生成预览…');
    const params = new URLSearchParams({
      fit: L.fit,
      rotate: String(L.rotate),
      margin: String(L.margin),
      paper: L.paper,
      page_orientation: L.orientation,
      dpi: '120'
    });
    if (sidOf(file)) params.set('sid', sidOf(file));
    fetch('/api/image_layout/' + encodeURIComponent(file.name) + '?' + params.toString(), {
      credentials: 'same-origin',
      cache: 'no-store'
    }).then(function (res) {
      if (!res.ok) throw new Error('布局预览生成失败（HTTP ' + res.status + '）');
      return res.blob();
    }).then(function (blob) {
      if (token !== state.preview.layoutToken) return;
      const url = URL.createObjectURL(blob);
      const oldUrl = state.preview.layoutUrl;
      state.preview.layoutUrl = url;
      state.preview.isLayout = true;
      state.preview.failed = false;
      const img = $('pageImg');
      if (!img) return;
      img.onload = function () {
        img.onload = null;
        if (token !== state.preview.layoutToken) return;
        img.hidden = false;
        hidePageState();
        if (oldUrl) { try { URL.revokeObjectURL(oldUrl); } catch (e) { /* ignore */ } }
      };
      img.onerror = function () {
        img.onerror = null;
        if (token !== state.preview.layoutToken) return;
        state.preview.failed = true;
        showPageError('布局预览生成失败，可直接进入打印设置。');
      };
      img.src = url;
    }).catch(function (err) {
      if (token !== state.preview.layoutToken) return;
      state.preview.failed = true;
      fallbackImageRenderPreview(err);
    });
  }

  /** 版式预览失败时回退到上传时生成的默认 PDF 预览，保证仍能看页面。 */
  function fallbackImageRenderPreview(err) {
    const file = state.previewFile;
    if (!file || !isImage(file)) {
      showPageError('布局预览生成失败：' + errorMessage(err));
      return;
    }
    const url = withSid('/api/render/' + encodeURIComponent(file.name) + '/1?dpi=120', file);
    const probe = new Image();
    probe.onload = function () {
      if (state.previewFile !== file) return;
      const img = $('pageImg');
      if (!img) return;
      img.onload = null;
      img.onerror = null;
      img.src = url;
      img.hidden = false;
      hidePageState();
      const note = $('wysiwygNote');
      if (note) {
        note.classList.add('is-plain');
        clear(note);
        note.appendChild(h('span', null, '预览已回退 · 打印仍按当前布局执行'));
      }
      toast('warning', '布局预览刷新失败', '已回退到默认预览；打印仍会按当前布局执行。', 4200);
    };
    probe.onerror = function () {
      if (state.previewFile !== file) return;
      showPageError('布局预览生成失败：' + errorMessage(err));
    };
    probe.src = url;
  }

  /* ---------- 缩放 / 平移 ---------- */

  function readViewerSize() {
    const stage = $('viewerStage');
    const v = state.viewer;
    if (stage) {
      v.stageW = stage.clientWidth;
      v.stageH = stage.clientHeight;
    }
    return { w: v.stageW, h: v.stageH };
  }

  function computeFitScale() {
    const v = state.viewer;
    if (v.stageW <= 0 || v.stageH <= 0 || v.pageW <= 0 || v.pageH <= 0) return 1;
    const pad = 28;
    const scale = Math.min((v.stageW - pad * 2) / v.pageW, (v.stageH - pad * 2) / v.pageH, 1);
    return clamp(scale, VIEWER_MIN, VIEWER_MAX);
  }

  function clampViewerPan() {
    const v = state.viewer;
    if (v.stageW <= 0 || v.stageH <= 0) return;
    const pw = v.pageW * v.scale;
    const ph = v.pageH * v.scale;
    const margin = 56;
    v.tx = clamp(v.tx, margin - pw, v.stageW - margin);
    v.ty = clamp(v.ty, margin - ph, v.stageH - margin);
  }

  function applyViewerTransform() {
    const v = state.viewer;
    const paper = $('paper');
    if (paper) {
      paper.style.transform = 'translate(' + v.tx.toFixed(2) + 'px, ' + v.ty.toFixed(2) + 'px) scale(' + v.scale.toFixed(4) + ')';
    }
    const pct = $('zoomPct');
    if (pct) pct.textContent = Math.round(v.scale * 100) + '%';
  }

  function centerPaper() {
    const v = state.viewer;
    v.tx = (v.stageW - v.pageW * v.scale) / 2;
    v.ty = (v.stageH - v.pageH * v.scale) / 2;
    clampViewerPan();
  }

  function fitView() {
    const size = readViewerSize();
    if (!size.w || !size.h) {
      state.viewer.mode = 'fit';
      return;
    }
    state.viewer.scale = computeFitScale();
    state.viewer.mode = 'fit';
    centerPaper();
    applyViewerTransform();
  }

  function fitViewIfNeeded() {
    if (state.viewer.mode === 'fit') {
      fitView();
    } else {
      readViewerSize();
      clampViewerPan();
      applyViewerTransform();
    }
  }

  function setViewerScaleAt(newScale, anchorX, anchorY) {
    const v = state.viewer;
    readViewerSize();
    if (v.stageW <= 0) return;
    const scale = clamp(newScale, VIEWER_MIN, VIEWER_MAX);
    const localX = (anchorX - v.tx) / v.scale;
    const localY = (anchorY - v.ty) / v.scale;
    v.scale = scale;
    v.tx = anchorX - localX * scale;
    v.ty = anchorY - localY * scale;
    v.mode = 'manual';
    clampViewerPan();
    applyViewerTransform();
    hideViewerHint();
  }

  function zoomViewerBy(factor) {
    const v = state.viewer;
    readViewerSize();
    setViewerScaleAt(v.scale * factor, v.stageW / 2, v.stageH / 2);
  }

  function actualSizeView() {
    const v = state.viewer;
    readViewerSize();
    setViewerScaleAt(1, v.stageW / 2, v.stageH / 2);
  }

  function toggleZoom() {
    const v = state.viewer;
    readViewerSize();
    if (v.stageW <= 0) return;
    const fit = computeFitScale();
    if (v.mode === 'fit' || Math.abs(v.scale - fit) < 0.02) {
      const target = clamp(Math.max(1, fit * 2), VIEWER_MIN, VIEWER_MAX);
      setViewerScaleAt(target, v.stageW / 2, v.stageH / 2);
    } else {
      fitView();
    }
  }

  function hideViewerHint() {
    const hint = $('viewerHint');
    if (hint) hint.classList.add('is-hidden');
  }

  function refreshPreviewView() {
    ensurePreviewSelection();
    if (!state.previewFile) {
      showPageError('没有可预览的文件，请先上传。');
      return;
    }
    renderPreviewSwitcher();
    renderPreviewHeader();
    renderLayoutPanel();
    renderPreviewInfo();
    updatePagerUI();
    updatePaperSize({ resetView: true, applyView: false });
    requestAnimationFrame(function () {
      readViewerSize();
      fitView();
    });
    if (state.preview.needsReload) {
      state.preview.needsReload = false;
      loadPreviewContent({ immediate: true });
    }
  }

  function goToPage(page) {
    const count = Math.max(1, state.preview.pageCount);
    const target = clamp(Math.round(Number(page) || 1), 1, count);
    if (target === state.preview.page) {
      updatePagerUI();
      return;
    }
    state.preview.page = target;
    updatePagerUI();
    renderPdfPage();
  }

  function bindViewer() {
    const viewer = $('viewer');
    const stage = $('viewerStage');
    if (!viewer || !stage) return;

    const pointers = new Map();
    let gesture = null;
    let lastTap = { time: 0, x: 0, y: 0 };

    function stagePoint(event) {
      const rect = stage.getBoundingClientRect();
      return { x: event.clientX - rect.left, y: event.clientY - rect.top };
    }

    function pointerPair() {
      const values = Array.from(pointers.values());
      return [values[0], values[1]];
    }

    function beginPinch() {
      const pair = pointerPair();
      if (pair.length < 2) { gesture = null; return; }
      const mid = { x: (pair[0].x + pair[1].x) / 2, y: (pair[0].y + pair[1].y) / 2 };
      const dist = Math.max(1, Math.hypot(pair[0].x - pair[1].x, pair[0].y - pair[1].y));
      const v = state.viewer;
      gesture = {
        type: 'pinch',
        d0: dist,
        s0: v.scale,
        t0x: v.tx,
        t0y: v.ty,
        mid0: mid
      };
    }

    stage.addEventListener('pointerdown', function (event) {
      if (event.pointerType === 'mouse' && event.button !== 0) return;
      const point = stagePoint(event);
      pointers.set(event.pointerId, point);
      try { stage.setPointerCapture(event.pointerId); } catch (e) { /* ignore */ }
      if (pointers.size === 1) {
        const v = state.viewer;
        gesture = {
          type: 'pan',
          startX: point.x,
          startY: point.y,
          tx0: v.tx,
          ty0: v.ty,
          moved: false
        };
      } else if (pointers.size === 2) {
        beginPinch();
      }
    });

    stage.addEventListener('pointermove', function (event) {
      if (!pointers.has(event.pointerId)) return;
      const point = stagePoint(event);
      pointers.set(event.pointerId, point);

      if (gesture && gesture.type === 'pinch' && pointers.size >= 2) {
        const pair = pointerPair();
        if (pair.length < 2) return;
        const mid = { x: (pair[0].x + pair[1].x) / 2, y: (pair[0].y + pair[1].y) / 2 };
        const dist = Math.max(1, Math.hypot(pair[0].x - pair[1].x, pair[0].y - pair[1].y));
        const v = state.viewer;
        const scale = clamp(gesture.s0 * (dist / gesture.d0), VIEWER_MIN, VIEWER_MAX);
        const localX = (gesture.mid0.x - gesture.t0x) / gesture.s0;
        const localY = (gesture.mid0.y - gesture.t0y) / gesture.s0;
        v.scale = scale;
        v.tx = mid.x - localX * scale;
        v.ty = mid.y - localY * scale;
        v.mode = 'manual';
        clampViewerPan();
        applyViewerTransform();
        hideViewerHint();
        return;
      }

      if (gesture && gesture.type === 'pan') {
        const dx = point.x - gesture.startX;
        const dy = point.y - gesture.startY;
        if (Math.abs(dx) + Math.abs(dy) > 3) {
          gesture.moved = true;
          viewer.classList.add('is-panning');
        }
        if (gesture.moved) {
          const v = state.viewer;
          v.tx = gesture.tx0 + dx;
          v.ty = gesture.ty0 + dy;
          v.mode = 'manual';
          clampViewerPan();
          applyViewerTransform();
          hideViewerHint();
        }
      }
    });

    function endPointer(event) {
      if (!pointers.has(event.pointerId)) return;
      const panMoved = !!(gesture && gesture.type === 'pan' && gesture.moved);
      pointers.delete(event.pointerId);
      try { stage.releasePointerCapture(event.pointerId); } catch (e) { /* ignore */ }

      if (pointers.size >= 2) {
        beginPinch();
        return;
      }
      if (pointers.size === 1) {
        const remaining = Array.from(pointers.values())[0];
        const v = state.viewer;
        gesture = {
          type: 'pan',
          startX: remaining.x,
          startY: remaining.y,
          tx0: v.tx,
          ty0: v.ty,
          moved: true
        };
        return;
      }

      gesture = null;
      viewer.classList.remove('is-panning');

      if (!panMoved) {
        const point = stagePoint(event);
        const now = Date.now();
        if (now - lastTap.time < 320 && Math.hypot(point.x - lastTap.x, point.y - lastTap.y) < 32) {
          lastTap = { time: 0, x: 0, y: 0 };
          toggleZoom();
        } else {
          lastTap = { time: now, x: point.x, y: point.y };
        }
      }
    }

    stage.addEventListener('pointerup', endPointer);
    stage.addEventListener('pointercancel', endPointer);

    viewer.addEventListener('wheel', function (event) {
      if (event.target && event.target.closest && event.target.closest('.viewer-toolbar')) return;
      event.preventDefault();
      readViewerSize();
      const point = stagePoint(event);
      const factor = Math.exp(-event.deltaY * (event.deltaMode === 1 ? 0.05 : 0.0015));
      setViewerScaleAt(state.viewer.scale * factor, point.x, point.y);
    }, { passive: false });

    if (typeof ResizeObserver === 'function') {
      const ro = new ResizeObserver(function () {
        if (state.step !== 2) return;
        readViewerSize();
        if (state.viewer.mode === 'fit') fitView();
        else { clampViewerPan(); applyViewerTransform(); }
      });
      ro.observe(stage);
    }
    window.addEventListener('resize', function () {
      if (state.step !== 2) return;
      readViewerSize();
      if (state.viewer.mode === 'fit') fitView();
      else { clampViewerPan(); applyViewerTransform(); }
    });
  }

  function bindPreviewUI() {
    const zoomOut = $('zoomOutBtn');
    const zoomIn = $('zoomInBtn');
    const fitBtn = $('fitBtn');
    const actualBtn = $('actualBtn');
    if (zoomOut) zoomOut.addEventListener('click', function () { zoomViewerBy(1 / ZOOM_STEP); });
    if (zoomIn) zoomIn.addEventListener('click', function () { zoomViewerBy(ZOOM_STEP); });
    if (fitBtn) fitBtn.addEventListener('click', function () { fitView(); hideViewerHint(); });
    if (actualBtn) actualBtn.addEventListener('click', function () { actualSizeView(); hideViewerHint(); });

    const prev = $('prevPageBtn');
    const next = $('nextPageBtn');
    const input = $('pageInput');
    if (prev) prev.addEventListener('click', function () { goToPage(state.preview.page - 1); });
    if (next) next.addEventListener('click', function () { goToPage(state.preview.page + 1); });
    if (input) {
      input.addEventListener('change', function () { goToPage(input.value); });
      input.addEventListener('keydown', function (event) {
        if (event.key === 'Enter') { event.preventDefault(); goToPage(input.value); }
      });
    }

    const openBtn = $('openOriginalBtn');
    if (openBtn) {
      openBtn.addEventListener('click', function () {
        const file = state.previewFile;
        if (!file) return;
        window.open(withSid('/preview/' + encodeURIComponent(file.name), file), '_blank', 'noopener');
      });
    }

    document.querySelectorAll('#fitGroup [data-fit]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        const file = state.previewFile;
        if (!file || !isImage(file)) return;
        const L = layoutFor(file);
        const fit = btn.getAttribute('data-fit');
        if (L.fit === fit) return;
        L.fit = fit;
        if (fit === 'fill' && L.margin === 5) L.margin = 0;
        if (fit !== 'fill' && L.margin === 0) L.margin = 5;
        renderLayoutPanel();
        renderPreviewInfo();
        scheduleLayoutPreview();
      });
    });

    document.querySelectorAll('#rotateGroup [data-rotate]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        const file = state.previewFile;
        if (!file || !isImage(file)) return;
        const L = layoutFor(file);
        const rotate = Number(btn.getAttribute('data-rotate'));
        if (L.rotate === rotate) return;
        L.rotate = rotate;
        renderLayoutPanel();
        renderPreviewInfo();
        scheduleLayoutPreview();
      });
    });

    const marginRange = $('marginRange');
    if (marginRange) {
      marginRange.addEventListener('input', function () {
        const file = state.previewFile;
        if (!file || !isImage(file)) return;
        const L = layoutFor(file);
        L.margin = clamp(Number(marginRange.value) || 0, 0, 20);
        const out = $('marginValue');
        if (out) out.textContent = L.margin + ' mm';
        scheduleLayoutPreview();
      });
      marginRange.addEventListener('change', function () {
        renderPreviewInfo();
      });
    }

    const imagePaper = $('imagePaper');
    if (imagePaper) {
      imagePaper.addEventListener('change', function () {
        const file = state.previewFile;
        if (!file || !isImage(file)) return;
        const L = layoutFor(file);
        L.paper = imagePaper.value;
        state.settings.paper = L.paper;
        const step3Paper = $('paperSelect');
        if (step3Paper) step3Paper.value = L.paper;
        updatePaperSize({ resetView: true });
        renderPreviewInfo();
        scheduleLayoutPreview();
      });
    }

    document.querySelectorAll('#imageOrient [data-orient]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        const file = state.previewFile;
        if (!file || !isImage(file)) return;
        const L = layoutFor(file);
        const orient = btn.getAttribute('data-orient');
        if (L.orientation === orient) return;
        L.orientation = orient;
        renderLayoutPanel();
        renderPreviewInfo();
        updatePaperSize({ resetView: true });
        scheduleLayoutPreview();
      });
    });

    const resetBtn = $('resetLayoutBtn');
    if (resetBtn) {
      resetBtn.addEventListener('click', function () {
        const file = state.previewFile;
        if (!file || !isImage(file)) return;
        ensurePerFile(file.name).layout = defaultLayout();
        renderLayoutPanel();
        renderPreviewInfo();
        updatePaperSize({ resetView: true });
        scheduleLayoutPreview();
        toast('info', '已恢复默认布局', '适应页面 · 5mm 边距 · A4 纵向');
      });
    }

    const step2Prev = $('step2PrevBtn');
    const step2Next = $('step2NextBtn');
    if (step2Prev) step2Prev.addEventListener('click', function () { showStep(1); });
    if (step2Next) {
      step2Next.addEventListener('click', function () {
        if (!canStep3()) {
          toast('warning', '还不能继续', '请先上传至少一个可打印文件。');
          return;
        }
        if (state.previewFile && isPrintable(state.previewFile)) {
          state.focusedName = state.previewFile.name;
        }
        showStep(3);
      });
    }
  }

  /* ==========================================================
   * 9. 步骤 3：打印设置
   * ========================================================== */

  function focusedFile() {
    return state.focusedName ? findFile(state.focusedName) : null;
  }

  function setSegmentedActive(containerId, attr, value) {
    const wrap = $(containerId);
    if (!wrap) return;
    wrap.querySelectorAll('[' + attr + ']').forEach(function (btn) {
      btn.classList.toggle('is-active', btn.getAttribute(attr) === String(value));
    });
  }

  function syncSettingsToUI() {
    const s = state.settings;
    const copies = $('copiesInput');
    if (copies) copies.value = String(s.copies);
    const paper = $('paperSelect');
    if (paper) paper.value = s.paper;
    setSegmentedActive('duplexGroup', 'data-duplex', s.duplex);
    setSegmentedActive('orientationGroup', 'data-orient', s.orientation);
    setSegmentedActive('qualityGroup', 'data-quality', s.quality);
    renderPrinterOptions();
  }

  function renderPrinterOptions() {
    const select = $('printerSelect');
    const help = $('printerHelp');
    if (!select) return;
    clear(select);
    const list = state.printers;
    if (!list.length) {
      const option = h('option', null, state.printersLoaded ? '未检测到打印机' : '正在读取打印机…');
      option.value = '';
      select.appendChild(option);
      select.disabled = true;
      state.settings.printer = '';
      if (help) {
        help.textContent = state.printersLoaded
          ? '未检测到可用打印机，请检查打印机电源与连接后点「刷新」。'
          : '正在读取打印机列表…';
      }
      return;
    }
    select.disabled = false;
    list.forEach(function (name) {
      const option = h('option', null, name);
      option.value = name;
      select.appendChild(option);
    });
    let chosen = state.settings.printer;
    if (!chosen || list.indexOf(chosen) < 0) {
      chosen = (state.defaultPrinter && list.indexOf(state.defaultPrinter) >= 0) ? state.defaultPrinter : list[0];
    }
    select.value = chosen;
    state.settings.printer = chosen;
    if (help) {
      const def = (state.defaultPrinter && list.indexOf(state.defaultPrinter) >= 0) ? state.defaultPrinter : list[0];
      help.textContent = '系统默认打印机：' + def + '，共 ' + list.length + ' 台可用。';
    }
  }

  async function refreshPrinters(options) {
    const opts = options || {};
    const btn = $('refreshPrintersBtn');
    const help = $('printerHelp');
    if (btn) { btn.disabled = true; btn.classList.add('is-loading'); }
    if (help) help.textContent = '正在读取打印机列表…';
    try {
      const data = await apiJson('/api/printers');
      const list = Array.isArray(data) ? data : (data && Array.isArray(data.printers) ? data.printers : []);
      state.printers = list.map(function (p) { return String(p); }).filter(Boolean);
      state.defaultPrinter = (data && (data.default || data.default_printer)) ? String(data.default || data.default_printer) : '';
      state.printersLoaded = true;
      renderPrinterOptions();
      updatePrintSummary();
      if (state.printers.length && !opts.quiet) toast('success', '打印机已刷新', '共 ' + state.printers.length + ' 台可用。', 2600);
    } catch (err) {
      state.printers = [];
      state.printersLoaded = false;
      renderPrinterOptions();
      if (help) help.textContent = '读取打印机失败：' + errorMessage(err) + '，请点「刷新」重试。';
      if (!opts.quiet) toast('error', '读取打印机失败', errorMessage(err));
    } finally {
      if (btn) { btn.disabled = false; btn.classList.remove('is-loading'); }
      updatePrintSummary();
    }
  }

  function bindSegmented(containerId, attr, handler) {
    const wrap = $(containerId);
    if (!wrap) return;
    wrap.addEventListener('click', function (event) {
      const btn = event.target.closest('[' + attr + ']');
      if (!btn) return;
      const value = btn.getAttribute(attr);
      setSegmentedActive(containerId, attr, value);
      handler(value);
    });
  }

  function enterStep3() {
    if (state.previewFile && isPrintable(state.previewFile)
      && (!state.focusedName || !findFile(state.focusedName))) {
      state.focusedName = state.previewFile.name;
    }
    if (!state.focusedName || !findFile(state.focusedName)) {
      const first = printableFiles()[0];
      state.focusedName = first ? first.name : null;
    }
    syncSettingsToUI();
    if (state.focusedName) setFocusFile(state.focusedName, { silent: true });
    else {
      renderPrintFileList();
      renderRangePanel();
      renderExcelPanel();
      updatePrintSummary();
    }
    if (!state.printersLoaded) refreshPrinters({ quiet: true });
    else renderPrinterOptions();
    refreshLogs();
  }

  function syncStep3AfterFilesChange() {
    if (state.focusedName && !findFile(state.focusedName)) state.focusedName = null;
    if (!state.focusedName) {
      const first = printableFiles()[0];
      if (first) state.focusedName = first.name;
    }
    updateSettingsFilePill();
    updateOrientationRow();
    renderPrintFileList();
    renderRangePanel();
    renderExcelPanel();
    updatePrintSummary();
  }

  function setFocusFile(name, options) {
    const opts = options || {};
    let file = findFile(name);
    if (!file || !isPrintable(file)) {
      const fallback = printableFiles()[0];
      if (!fallback) {
        state.focusedName = null;
        updateSettingsFilePill();
        return;
      }
      file = fallback;
    }
    state.focusedName = file.name;
    ensurePerFile(file.name);
    if (isImage(file)) {
      const L = layoutFor(file);
      state.settings.paper = L.paper;
      const paperSelect = $('paperSelect');
      if (paperSelect) paperSelect.value = L.paper;
    }
    updateSettingsFilePill();
    updateOrientationRow();
    renderPrintFileList();
    renderRangePanel();
    renderExcelPanel();
    updatePrintSummary();
    if (!opts.silent) hideViewerHint();
  }

  function updateSettingsFilePill() {
    const pill = $('settingsFilePill');
    if (!pill) return;
    const file = focusedFile();
    if (!file) {
      pill.textContent = '未选择文件';
      pill.title = '';
      return;
    }
    const name = displayName(file);
    pill.textContent = name.length > 18 ? name.slice(0, 17) + '…' : name;
    pill.title = name;
  }

  function updateOrientationRow() {
    const file = focusedFile();
    const row = $('orientationRow');
    const hint = $('imageOrientationHint');
    const image = !!file && isImage(file);
    if (row) row.hidden = image;
    if (hint) hint.hidden = !image;
  }

  function renderPrintFileList() {
    const list = $('printFileList');
    if (!list) return;
    clear(list);
    if (!state.files.length) {
      list.appendChild(h('li', 'print-files-empty', '还没有文件，请回到第 1 步上传。'));
    } else {
      state.files.forEach(function (file) {
        const ready = isPrintable(file);
        const pf = ensurePerFile(file.name);
        const row = h('li', 'print-file-row' + (state.focusedName === file.name ? ' is-focused' : ''));

        const checkWrap = h('label', 'print-file-check');
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.checked = !!ready && pf.selected;
        checkbox.disabled = !ready;
        checkbox.setAttribute('aria-label', '打印 ' + displayName(file));
        checkbox.addEventListener('change', function () {
          pf.selected = checkbox.checked;
          updatePrintSummary();
        });
        checkWrap.appendChild(checkbox);

        const main = h('div', 'print-file-main');
        main.setAttribute('role', 'button');
        main.tabIndex = 0;
        const nameRow = h('div', 'print-file-name');
        nameRow.appendChild(h('span', null, kindMeta(file.kind).emoji));
        const nameText = h('span', 'truncate', displayName(file));
        nameText.title = displayName(file);
        nameRow.appendChild(nameText);
        if (state.focusedName === file.name) nameRow.appendChild(h('span', 'focus-mark', '当前参数'));
        main.appendChild(nameRow);
        const pages = Number(file.page_count);
        let metaText = kindLabel(file.kind) + ' \u00B7 ' + (pages > 0 ? pages + ' 页' : '页数未知');
        if (!ready) metaText += ' \u00B7 不可打印';
        main.appendChild(h('div', 'print-file-meta', metaText));
        if (ready) {
          main.addEventListener('click', function () { setFocusFile(file.name); });
          main.addEventListener('keydown', function (event) {
            if (event.key === 'Enter' || event.key === ' ') {
              event.preventDefault();
              setFocusFile(file.name);
            }
          });
        } else {
          main.style.cursor = 'not-allowed';
        }

        const remove = h('button', 'print-file-remove', '\u00D7');
        remove.type = 'button';
        remove.title = '从本次打印中移除（不会删除文件）';
        remove.setAttribute('aria-label', '从本次打印中移除 ' + displayName(file));
        remove.addEventListener('click', function () {
          pf.selected = false;
          renderPrintFileList();
          updatePrintSummary();
          toast('info', '已从本次打印移除', displayName(file) + '，文件仍保留在会话中。', 2600);
        });

        append(row, checkWrap, main, remove);
        list.appendChild(row);
      });
    }
    const countPill = $('printFileCountPill');
    if (countPill) countPill.textContent = String(selectedPrintFiles().length);
  }

  function updatePrintSummary() {
    const selected = selectedPrintFiles();
    const summary = $('printSummary');
    const startBtn = $('startPrintBtn');
    const startText = $('startPrintBtnText');
    if (summary) {
      if (!state.printers.length) {
        summary.textContent = '未检测到打印机，请先刷新打印机列表。';
      } else if (!selected.length) {
        summary.textContent = '还没有勾选要打印的文件。';
      } else {
        summary.textContent = '将打印 ' + selected.length + ' 个文件'
          + (state.settings.copies > 1 ? '，每个 ' + state.settings.copies + ' 份' : '')
          + ' · ' + (state.settings.printer || '未选择打印机');
      }
    }
    if (startBtn) startBtn.disabled = state.printing || selected.length === 0 || !state.settings.printer;
    if (startText) {
      startText.textContent = state.printing
        ? '正在打印…'
        : (selected.length > 1 ? '开始打印（' + selected.length + ' 个文件）' : '开始打印');
    }
    const countPill = $('printFileCountPill');
    if (countPill) countPill.textContent = String(selected.length);
  }

  function setAllSelected(selected) {
    printableFiles().forEach(function (file) {
      ensurePerFile(file.name).selected = !!selected;
    });
    renderPrintFileList();
    updatePrintSummary();
    if (!selected) toast('info', '已全不选', '勾选文件后才能开始打印。', 2400);
  }

  /* ---------- 页码范围 ---------- */

  function pagesFromRange(expr, count) {
    const set = new Set();
    const max = Math.max(1, Number(count) || 1);
    const text = String(expr == null ? '' : expr).trim().toLowerCase();
    if (!text || text === 'all') {
      for (let i = 1; i <= max; i++) set.add(i);
      return set;
    }
    text.split(/[,，、\s]+/).forEach(function (part) {
      if (!part) return;
      const match = /^(\d+)(?:\s*[-~—]\s*(\d+))?$/.exec(part);
      if (!match) return;
      let a = parseInt(match[1], 10);
      let b = match[2] ? parseInt(match[2], 10) : a;
      if (!isFinite(a) || !isFinite(b)) return;
      if (a > b) { const tmp = a; a = b; b = tmp; }
      for (let i = Math.max(1, a); i <= Math.min(max, b); i++) set.add(i);
    });
    return set;
  }

  function formatPageSet(set, count) {
    const max = Math.max(1, Number(count) || 1);
    const arr = Array.from(set).filter(function (n) { return n >= 1 && n <= max; }).sort(function (a, b) { return a - b; });
    if (!arr.length) return 'all';
    if (arr.length === max) return 'all';
    const parts = [];
    let start = arr[0];
    let prev = arr[0];
    for (let i = 1; i < arr.length; i++) {
      const n = arr[i];
      if (n === prev + 1) { prev = n; continue; }
      parts.push(start === prev ? String(start) : start + '-' + prev);
      start = prev = n;
    }
    parts.push(start === prev ? String(start) : start + '-' + prev);
    return parts.join(',');
  }

  function currentPageCount(file) {
    if (!file) return 1;
    const n = Number(file.page_count);
    if (n > 0) return n;
    return 1;
  }

  function renderRangePanel() {
    const file = focusedFile();
    const pf = file ? ensurePerFile(file.name) : null;
    const label = $('rangeFileLabel');
    if (label) label.textContent = file ? '当前文件' : '未选择文件';
    const nameEl = $('rangeFileName');
    if (nameEl) nameEl.textContent = file ? displayName(file) : '—';
    const quick = $('rangeQuick');
    if (quick) {
      quick.querySelectorAll('[data-range]').forEach(function (btn) {
        btn.classList.toggle('is-active', !!pf && btn.getAttribute('data-range') === pf.rangeMode);
      });
    }
    const input = $('pageRangeInput');
    if (input) {
      input.value = pf ? (pf.pageRange === 'all' ? 'all' : (pf.pageRange || 'all')) : 'all';
      input.disabled = !pf || pf.rangeMode !== 'custom';
    }
    const applyBtn = $('applyRangeBtn');
    if (applyBtn) applyBtn.disabled = !pf || pf.rangeMode !== 'custom';
    renderPageGrid(file, pf);
    if (file) {
      resolvePageCount(file).then(function (count) {
        if (state.focusedName === file.name) renderPageGrid(file, pf, count);
      });
    }
  }

  function renderPageGrid(file, pf, countOverride) {
    const grid = $('pageGrid');
    if (!grid) return;
    clear(grid);
    const hint = $('pageGridHint');
    const count = Number(countOverride) || currentPageCount(file);
    const countEl = $('rangePageCount');
    if (countEl) countEl.textContent = String(count);
    if (!file || !pf) {
      if (hint) hint.hidden = true;
      return;
    }
    const selectedPages = pagesFromRange(pf.pageRange, count);
    const maxButtons = Math.min(count, 40);
    for (let i = 1; i <= maxButtons; i++) {
      const btn = h('button', 'page-num-btn', String(i));
      btn.type = 'button';
      btn.setAttribute('data-page', String(i));
      btn.setAttribute('aria-pressed', selectedPages.has(i) ? 'true' : 'false');
      btn.classList.toggle('is-active', selectedPages.has(i));
      grid.appendChild(btn);
    }
    if (hint) {
      if (count > 40) {
        hint.hidden = false;
        hint.textContent = '共 ' + count + ' 页，仅显示前 40 页，请用上方输入框指定其余页码。';
      } else {
        hint.hidden = true;
      }
    }
  }

  function applyCustomRange() {
    const file = focusedFile();
    if (!file) return;
    const pf = ensurePerFile(file.name);
    const input = $('pageRangeInput');
    if (!input) return;
    const raw = input.value.trim();
    const count = currentPageCount(file);
    if (!raw) {
      pf.pageRange = 'all';
      pf.rangeMode = 'custom';
      renderRangePanel();
      return;
    }
    const set = pagesFromRange(raw, count);
    if (!set.size) {
      toast('error', '页码范围格式不正确', '示例：1-5, 7, 10-12；也可以直接输入 all。');
      renderRangePanel();
      return;
    }
    const expr = formatPageSet(set, count);
    if (expr.length > MAX_PAGE_RANGE_LEN) {
      toast('error', '页码范围过长', '后端最多支持 100 个字符，请使用范围写法，例如：1-5, 8, 10-20。');
      renderRangePanel();
      return;
    }
    pf.rangeMode = 'custom';
    pf.pageRange = expr;
    renderRangePanel();
  }

  function applyRangeMode(mode) {
    const file = focusedFile();
    if (!file) {
      toast('warning', '未选择文件', '请先在待打印文件清单中选择一个文件。');
      return;
    }
    const pf = ensurePerFile(file.name);
    pf.rangeMode = mode;
    if (mode === 'all') {
      pf.pageRange = 'all';
      renderRangePanel();
      return;
    }
    resolvePageCount(file).then(function (count) {
      const total = Math.max(1, Number(count) || 1);
      if (mode === 'odd' || mode === 'even') {
        const set = new Set();
        for (let i = (mode === 'odd' ? 1 : 2); i <= total; i += 2) set.add(i);
        const expr = formatPageSet(set, total);
        if (expr.length > MAX_PAGE_RANGE_LEN) {
          pf.rangeMode = 'custom';
          pf.pageRange = '';
          if (state.focusedName === file.name) renderRangePanel();
          toast('warning', '页数较多', '共 ' + total + ' 页，奇数/偶数页无法用单行范围表示（后端上限 100 字符）；请在「自定义」中直接输入范围，例如 1-40。', 6500);
          return;
        }
        pf.pageRange = expr;
      } else {
        pf.pageRange = (pf.pageRange && pf.pageRange !== 'all') ? pf.pageRange : '';
      }
      if (state.focusedName === file.name) {
        renderRangePanel();
        if (mode === 'custom') {
          const input = $('pageRangeInput');
          if (input && !input.disabled) { input.value = pf.pageRange || ''; input.focus(); }
        }
      }
    });
  }

  function togglePageNumber(pageNum) {
    const file = focusedFile();
    if (!file) return;
    const pf = ensurePerFile(file.name);
    const count = currentPageCount(file);
    const set = pagesFromRange(pf.pageRange, count);
    if (set.has(pageNum)) set.delete(pageNum);
    else set.add(pageNum);
    pf.rangeMode = 'custom';
    pf.pageRange = formatPageSet(set, count);
    renderRangePanel();
  }

  /* ---------- Excel 工作表 / 打印区域 ---------- */

  function loadExcelSheets(file) {
    if (!file) return Promise.resolve([]);
    const cached = state.excelSheets[file.name];
    if (cached && cached.loaded) return Promise.resolve(cached.sheets);
    if (cached && cached.loading) return cached.promise;
    const entry = { loaded: false, loading: true, sheets: [], promise: null };
    entry.promise = apiJson(withSid('/api/excel_info/' + encodeURIComponent(file.name), file))
      .then(function (data) {
        const sheets = Array.isArray(data) ? data : (data && Array.isArray(data.sheets) ? data.sheets : []);
        entry.sheets = sheets.map(function (s) { return String(s); });
        entry.loaded = true;
        entry.loading = false;
        return entry.sheets;
      })
      .catch(function (err) {
        entry.loaded = true;
        entry.loading = false;
        entry.sheets = [];
        toast('error', '读取工作表失败', errorMessage(err));
        return [];
      });
    state.excelSheets[file.name] = entry;
    return entry.promise;
  }

  function renderExcelPanel() {
    const card = $('excelCard');
    const file = focusedFile();
    const show = !!file && isSheet(file);
    if (card) card.hidden = !show;
    if (!show) return;
    const label = $('excelFileLabel');
    if (label) label.textContent = displayName(file);
    const pf = ensurePerFile(file.name);
    const areaInput = $('printAreaInput');
    if (areaInput) areaInput.value = pf.printArea || '';
    const select = $('sheetSelect');
    loadExcelSheets(file).then(function (sheets) {
      if (!select) return;
      if (state.focusedName !== file.name) return;
      clear(select);
      if (!sheets || !sheets.length) {
        const option = h('option', null, '默认工作表');
        option.value = '';
        select.appendChild(option);
        select.disabled = true;
        pf.sheet = '';
        return;
      }
      select.disabled = false;
      sheets.forEach(function (name) {
        const option = h('option', null, name);
        option.value = name;
        select.appendChild(option);
      });
      if (pf.sheet && sheets.indexOf(pf.sheet) < 0) pf.sheet = '';
      if (!pf.sheet) pf.sheet = sheets[0];
      select.value = pf.sheet;
    });
  }

  function columnLetter(index) {
    let n = Number(index) + 1;
    let letters = '';
    while (n > 0) {
      const m = (n - 1) % 26;
      letters = String.fromCharCode(65 + m) + letters;
      n = Math.floor((n - 1) / 26);
    }
    return letters || 'A';
  }

  function selectionToA1(selection) {
    const parts = [];
    if (selection.start && selection.end) {
      const r1 = Math.min(selection.start.r, selection.end.r);
      const r2 = Math.max(selection.start.r, selection.end.r);
      const c1 = Math.min(selection.start.c, selection.end.c);
      const c2 = Math.max(selection.start.c, selection.end.c);
      if (r1 === r2 && c1 === c2) parts.push(columnLetter(c1) + (r1 + 1));
      else parts.push(columnLetter(c1) + (r1 + 1) + ':' + columnLetter(c2) + (r2 + 1));
    }
    Array.from(selection.multi).map(function (key) {
      return key.split(',').map(Number);
    }).sort(function (a, b) {
      return a[0] - b[0] || a[1] - b[1];
    }).forEach(function (pair) {
      parts.push(columnLetter(pair[1]) + (pair[0] + 1));
    });
    return parts.join(',');
  }

  function openExcelGridModal() {
    const file = focusedFile();
    if (!file || !isSheet(file)) {
      toast('warning', '没有 Excel 文件', '请先在待打印清单中选择一个 Excel 文件。');
      return;
    }
    const pf = ensurePerFile(file.name);
    const sheet = pf.sheet || '';
    const body = h('div');
    const info = h('p', 'excel-sel-info', '选区：未选择（在表格中拖拽或点击单元格）');
    const gridWrap = h('div', 'excel-grid-wrap');
    gridWrap.appendChild(h('div', 'excel-empty', '正在读取表格数据…'));
    const footHint = h('p', 'excel-foot-hint', '鼠标按住拖拽选择矩形区域；Shift+点击扩展；Ctrl / ⌘+点击可多选单元格；手机上依次点两个单元格确定范围。');
    append(body, info, gridWrap, footHint);

    const selection = { start: null, end: null, multi: new Set() };
    const stateHolder = { dragging: false };

    function updateInfo() {
      const expr = selectionToA1(selection);
      info.textContent = expr ? '选区：' + expr : '选区：未选择（在表格中拖拽或点击单元格）';
    }

    const modalPromise = showModal({
      title: '可视选择打印区域 · ' + displayName(file),
      body: body,
      size: 'lg',
      buttons: [
        { label: '取消', value: null, variant: 'outline' },
        {
          label: '确认选区',
          value: 'apply',
          variant: 'primary',
          onClick: function () {
            const expr = selectionToA1(selection);
            if (!expr) {
              toast('info', '请先选择区域', '在表格中拖拽或点击单元格后再确认。');
              return false;
            }
            pf.printArea = expr;
            const areaInput = $('printAreaInput');
            if (areaInput) areaInput.value = expr;
            toast('success', '已应用打印区域', expr, 2400);
            return true;
          }
        }
      ]
    });

    let onPointerUp = null;

    function bindGrid(rows, headers) {
      clear(gridWrap);
      const table = document.createElement('table');
      table.className = 'excel-grid-table';
      const thead = document.createElement('thead');
      const headRow = document.createElement('tr');
      headRow.appendChild(h('th', 'corner-header', ''));
      headers.forEach(function (_, index) {
        headRow.appendChild(h('th', null, columnLetter(index)));
      });
      thead.appendChild(headRow);
      table.appendChild(thead);

      const tbody = document.createElement('tbody');
      rows.forEach(function (row, rowIndex) {
        const tr = document.createElement('tr');
        tr.appendChild(h('td', 'row-header', String(rowIndex + 1)));
        for (let c = 0; c < headers.length; c++) {
          const raw = (row && c < row.length) ? row[c] : '';
          const td = h('td', null, raw === null || raw === undefined ? '' : String(raw));
          td.setAttribute('data-r', String(rowIndex));
          td.setAttribute('data-c', String(c));
          tr.appendChild(td);
        }
        tbody.appendChild(tr);
      });
      table.appendChild(tbody);
      gridWrap.appendChild(table);

      function cellAt(r, c) {
        return gridWrap.querySelector('td[data-r="' + r + '"][data-c="' + c + '"]');
      }

      function paint() {
        gridWrap.querySelectorAll('td.is-selected').forEach(function (td) { td.classList.remove('is-selected'); });
        if (selection.start && selection.end) {
          const r1 = Math.min(selection.start.r, selection.end.r);
          const r2 = Math.max(selection.start.r, selection.end.r);
          const c1 = Math.min(selection.start.c, selection.end.c);
          const c2 = Math.max(selection.start.c, selection.end.c);
          for (let r = r1; r <= r2; r++) {
            for (let c = c1; c <= c2; c++) {
              const cell = cellAt(r, c);
              if (cell) cell.classList.add('is-selected');
            }
          }
        }
        selection.multi.forEach(function (key) {
          const pair = key.split(',').map(Number);
          const cell = cellAt(pair[0], pair[1]);
          if (cell) cell.classList.add('is-selected');
        });
        updateInfo();
      }

      function setSingle(r, c) {
        selection.start = { r: r, c: c };
        selection.end = { r: r, c: c };
        selection.multi.clear();
        paint();
      }

      table.addEventListener('pointerdown', function (event) {
        const td = event.target.closest('td[data-r]');
        if (!td) return;
        const r = Number(td.getAttribute('data-r'));
        const c = Number(td.getAttribute('data-c'));
        if (event.pointerType === 'touch') {
          if (!selection.start) setSingle(r, c);
          else {
            selection.end = { r: r, c: c };
            selection.multi.clear();
            paint();
          }
          return;
        }
        if (event.shiftKey && selection.start) {
          selection.end = { r: r, c: c };
          paint();
          event.preventDefault();
          return;
        }
        if (event.ctrlKey || event.metaKey) {
          const key = r + ',' + c;
          if (selection.multi.has(key)) selection.multi.delete(key);
          else selection.multi.add(key);
          paint();
          event.preventDefault();
          return;
        }
        selection.start = { r: r, c: c };
        selection.end = { r: r, c: c };
        selection.multi.clear();
        stateHolder.dragging = true;
        paint();
        event.preventDefault();
      });

      table.addEventListener('pointermove', function (event) {
        if (!stateHolder.dragging) return;
        const el = document.elementFromPoint(event.clientX, event.clientY);
        const td = el && el.closest ? el.closest('td[data-r]') : null;
        if (!td) return;
        selection.end = {
          r: Number(td.getAttribute('data-r')),
          c: Number(td.getAttribute('data-c'))
        };
        paint();
      });

      onPointerUp = function () { stateHolder.dragging = false; };
      window.addEventListener('pointerup', onPointerUp);

      const clearSel = h('button', 'btn btn-outline btn-sm', '清除选区');
      clearSel.type = 'button';
      clearSel.addEventListener('click', function () {
        selection.start = null;
        selection.end = null;
        selection.multi.clear();
        paint();
      });
      const toolbar = h('div', 'excel-toolbar');
      toolbar.appendChild(info);
      toolbar.appendChild(clearSel);
      body.insertBefore(toolbar, gridWrap);
    }

    let previewUrl = '/api/excel_preview/' + encodeURIComponent(file.name)
      + (sheet ? '?sheet=' + encodeURIComponent(sheet) : '');
    previewUrl = withSid(previewUrl, file);
    apiJson(previewUrl).then(function (data) {
      const rows = (data && Array.isArray(data.rows)) ? data.rows : [];
      let headers = (data && Array.isArray(data.headers)) ? data.headers : [];
      if (!headers.length && rows.length) {
        headers = (rows[0] || []).map(function (_, index) { return columnLetter(index); });
      }
      if (!rows.length) {
        clear(gridWrap);
        gridWrap.appendChild(h('div', 'excel-empty', '没有可预览的表格数据（可能为空工作表）。'));
        return;
      }
      bindGrid(rows, headers);
    }).catch(function (err) {
      clear(gridWrap);
      gridWrap.appendChild(h('div', 'excel-empty', '读取表格数据失败：' + errorMessage(err)));
    });

    modalPromise.then(function () {
      if (onPointerUp) window.removeEventListener('pointerup', onPointerUp);
    });
  }

  /* ==========================================================
   * 10. 打印执行 / 结果 / 记录
   * ========================================================== */

  function showBusy(title, detail) {
    const overlay = $('printOverlay');
    if (!overlay) return;
    const titleEl = $('printOverlayTitle');
    const detailEl = $('printOverlayDetail');
    const bar = $('printProgressBar');
    if (titleEl) titleEl.textContent = title || '正在处理…';
    if (detailEl) detailEl.textContent = detail || '请稍候…';
    if (bar) {
      bar.style.width = '0%';
      bar.classList.remove('is-done', 'is-error');
    }
    overlay.hidden = false;
  }

  function updateBusy(ratio, detail) {
    const bar = $('printProgressBar');
    if (bar) bar.style.width = Math.round(clamp(Number(ratio) || 0, 0, 1) * 100) + '%';
    const detailEl = $('printOverlayDetail');
    if (detail && detailEl) detailEl.textContent = detail;
  }

  function hideBusy() {
    const overlay = $('printOverlay');
    if (overlay) overlay.hidden = true;
  }

  function buildPrintBody(file) {
    const pf = ensurePerFile(file.name);
    const s = state.settings;
    const body = {
      name: file.name,
      printer: s.printer,
      copies: s.copies,
      duplex: s.duplex,
      paper_size: s.paper,
      quality: s.quality,
      orientation: s.orientation,
      page_range: (pf.pageRange && String(pf.pageRange).trim()) ? String(pf.pageRange).trim() : 'all',
      sheet_name: null,
      print_area: null
    };
    if (isImage(file)) {
      const L = layoutFor(file);
      body.paper_size = L.paper;
      body.orientation = L.orientation;
      body.image = {
        fit: L.fit,
        rotate: L.rotate,
        margin: L.margin,
        paper: L.paper,
        page_orientation: L.orientation
      };
    }
    if (isSheet(file)) {
      body.sheet_name = pf.sheet || null;
      body.print_area = (pf.printArea || '').trim().toUpperCase() || null;
    }
    return body;
  }

  function formatPrintSuccess(data) {
    let message = (data && data.message) || '打印成功';
    let pagesText = '';
    if (data && data.pages !== undefined && data.pages !== null) {
      if (Array.isArray(data.pages)) pagesText = data.pages.join(', ');
      else if (Number(data.pages) > 0) pagesText = String(data.pages) + ' 页';
    }
    if (pagesText) message += '（' + pagesText + '）';
    const warnings = (data && Array.isArray(data.warnings)) ? data.warnings.filter(Boolean) : [];
    if (warnings.length) message += ' · 提示：' + warnings.join('；');
    return message;
  }

  async function startPrinting(explicitFiles) {
    if (state.printing) return;
    const targets = (Array.isArray(explicitFiles) && explicitFiles.length)
      ? explicitFiles.slice()
      : selectedPrintFiles();
    if (!targets.length) {
      toast('warning', '没有待打印文件', '请先在待打印文件清单中勾选文件。');
      return;
    }
    if (!state.settings.printer) {
      toast('error', '未选择打印机', '请先刷新打印机列表并选择一台打印机。');
      return;
    }

    state.printing = true;
    state.lastFailed = [];
    updatePrintSummary();
    const startBtn = $('startPrintBtn');
    if (startBtn) { startBtn.classList.add('is-loading'); startBtn.disabled = true; }
    showBusy('正在打印…', '共 ' + targets.length + ' 个文件');
    updateBusy(0, '正在提交第 1/' + targets.length + ' 个文件…');

    const results = [];
    for (let i = 0; i < targets.length; i++) {
      const file = targets[i];
      updateBusy(i / targets.length, '正在打印 ' + (i + 1) + '/' + targets.length + '：' + displayName(file));
      try {
        const data = await postJson('/print_single', buildPrintBody(file));
        results.push({ file: file, ok: true, message: formatPrintSuccess(data) });
      } catch (err) {
        results.push({ file: file, ok: false, message: errorMessage(err, '打印失败') });
      }
      updateBusy((i + 1) / targets.length, '已完成 ' + (i + 1) + '/' + targets.length);
    }

    hideBusy();
    state.printing = false;
    if (startBtn) startBtn.classList.remove('is-loading');
    state.lastResults = results;
    state.lastFailed = results.filter(function (r) { return !r.ok; }).map(function (r) { return r.file; });
    renderResults(results);
    refreshLogs();
    updatePrintSummary();

    const okCount = results.filter(function (r) { return r.ok; }).length;
    const failCount = results.length - okCount;
    if (failCount) {
      toast('warning', '打印完成', '成功 ' + okCount + ' 个，失败 ' + failCount + ' 个，可在结果卡片中重试。', 6000);
    } else {
      toast('success', '打印完成', '已成功提交 ' + okCount + ' 个文件到打印机。');
    }
  }

  function renderResults(results) {
    const card = $('resultCard');
    const list = $('resultList');
    const pill = $('resultPill');
    const retryBtn = $('retryFailedBtn');
    if (!card || !list) return;
    clear(list);
    const okCount = results.filter(function (r) { return r.ok; }).length;
    const failCount = results.length - okCount;
    card.hidden = false;
    card.classList.toggle('has-failures', failCount > 0);
    if (pill) {
      pill.textContent = '成功 ' + okCount + ' / 失败 ' + failCount;
      pill.className = 'pill' + (failCount ? '' : ' pill-soft');
    }
    results.forEach(function (result) {
      const row = h('div', 'result-row');
      const icon = h('span', 'result-icon', result.ok ? '\u2713' : '\u2715');
      icon.style.color = result.ok ? 'var(--green)' : 'var(--red)';
      icon.setAttribute('aria-hidden', 'true');
      const main = h('div', 'result-main');
      main.appendChild(h('div', 'result-name', displayName(result.file)));
      main.appendChild(h('div', 'result-msg', result.message));
      append(row, icon, main);
      list.appendChild(row);
    });
    if (retryBtn) retryBtn.hidden = failCount === 0;
    try { card.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); } catch (e) { /* ignore */ }
  }

  function isFailedLog(log) {
    if (!log) return true;
    const status = String(log.status || '').toLowerCase();
    const message = String(log.message || '');
    if (status.indexOf('fail') >= 0 || status.indexOf('error') >= 0 || status.indexOf('失败') >= 0) return true;
    return /失败|错误|✗|❌/.test(message);
  }

  function renderLogs() {
    const list = $('logsList');
    const empty = $('logsEmpty');
    if (!list) return;
    clear(list);
    const logs = Array.isArray(state.logs) ? state.logs : [];
    if (empty) empty.hidden = logs.length > 0;
    logs.forEach(function (log) {
      if (!log) return;
      const row = h('li', 'log-item' + (isFailedLog(log) ? ' is-failed' : ''));
      const dot = h('span', 'log-dot');
      dot.setAttribute('aria-hidden', 'true');
      const content = h('div', 'log-body');
      content.appendChild(h('div', 'log-msg', log.message || log.display_name || '打印记录'));
      const timeText = log.time ? String(log.time) : '';
      const display = log.display_name ? String(log.display_name) : '';
      const showName = display && String(log.message || '').indexOf(display) < 0;
      content.appendChild(h('div', 'log-time', (timeText ? timeText : '') + (showName ? (timeText ? ' · ' : '') + display : '')));
      append(row, dot, content);
      list.appendChild(row);
    });
  }

  async function refreshLogs() {
    try {
      const data = await apiJson('/api/logs?limit=20');
      const logs = Array.isArray(data) ? data : (data && Array.isArray(data.logs) ? data.logs : []);
      state.logs = logs;
      renderLogs();
    } catch (err) {
      const empty = $('logsEmpty');
      if (empty) {
        empty.hidden = false;
        const sub = empty.querySelector('.empty-sub');
        if (sub) sub.textContent = '读取打印记录失败：' + errorMessage(err);
      }
    }
  }

  /* ==========================================================
   * 11. 事件绑定 / 初始化
   * ========================================================== */

  function bindStep3UI() {
    const printerSelect = $('printerSelect');
    if (printerSelect) {
      printerSelect.addEventListener('change', function () {
        state.settings.printer = printerSelect.value;
        updatePrintSummary();
      });
    }
    const refreshPrintersBtn = $('refreshPrintersBtn');
    if (refreshPrintersBtn) refreshPrintersBtn.addEventListener('click', function () { refreshPrinters(); });

    const copies = $('copiesInput');
    function syncCopies() {
      if (!copies) return;
      const value = clamp(parseInt(copies.value, 10) || 1, 1, 10);
      copies.value = String(value);
      state.settings.copies = value;
      updatePrintSummary();
    }
    if (copies) {
      copies.addEventListener('change', syncCopies);
      copies.addEventListener('blur', syncCopies);
    }
    const copiesMinus = $('copiesMinus');
    const copiesPlus = $('copiesPlus');
    if (copiesMinus && copies) {
      copiesMinus.addEventListener('click', function () {
        copies.value = String(clamp((parseInt(copies.value, 10) || 1) - 1, 1, 10));
        syncCopies();
      });
    }
    if (copiesPlus && copies) {
      copiesPlus.addEventListener('click', function () {
        copies.value = String(clamp((parseInt(copies.value, 10) || 1) + 1, 1, 10));
        syncCopies();
      });
    }

    bindSegmented('duplexGroup', 'data-duplex', function (value) {
      state.settings.duplex = Number(value);
    });
    bindSegmented('qualityGroup', 'data-quality', function (value) {
      state.settings.quality = value;
    });
    bindSegmented('orientationGroup', 'data-orient', function (value) {
      state.settings.orientation = value;
      updatePrintSummary();
    });

    const paperSelect = $('paperSelect');
    if (paperSelect) {
      paperSelect.addEventListener('change', function () {
        state.settings.paper = paperSelect.value;
        const file = focusedFile();
        if (file && isImage(file)) {
          layoutFor(file).paper = paperSelect.value;
          state.preview.needsReload = true;
          if (state.previewFile && state.previewFile.name === file.name) {
            updatePaperSize({ resetView: true });
          }
        }
        updatePrintSummary();
      });
    }

    const rangeQuick = $('rangeQuick');
    if (rangeQuick) {
      rangeQuick.addEventListener('click', function (event) {
        const btn = event.target.closest('[data-range]');
        if (!btn) return;
        applyRangeMode(btn.getAttribute('data-range'));
      });
    }
    const rangeInput = $('pageRangeInput');
    if (rangeInput) {
      rangeInput.addEventListener('change', applyCustomRange);
      rangeInput.addEventListener('keydown', function (event) {
        if (event.key === 'Enter') { event.preventDefault(); applyCustomRange(); }
      });
    }
    const applyRangeBtn = $('applyRangeBtn');
    if (applyRangeBtn) applyRangeBtn.addEventListener('click', applyCustomRange);
    const pageGrid = $('pageGrid');
    if (pageGrid) {
      pageGrid.addEventListener('click', function (event) {
        const btn = event.target.closest('.page-num-btn');
        if (!btn) return;
        togglePageNumber(Number(btn.getAttribute('data-page')));
      });
    }

    const sheetSelect = $('sheetSelect');
    if (sheetSelect) {
      sheetSelect.addEventListener('change', function () {
        const file = focusedFile();
        if (!file) return;
        ensurePerFile(file.name).sheet = sheetSelect.value;
      });
    }
    const printAreaInput = $('printAreaInput');
    if (printAreaInput) {
      printAreaInput.addEventListener('change', function () {
        const file = focusedFile();
        if (!file) return;
        ensurePerFile(file.name).printArea = printAreaInput.value.trim();
      });
    }
    const openGridBtn = $('openGridBtn');
    if (openGridBtn) openGridBtn.addEventListener('click', openExcelGridModal);

    const selectAllBtn = $('selectAllBtn');
    if (selectAllBtn) selectAllBtn.addEventListener('click', function () { setAllSelected(true); });
    const selectNoneBtn = $('selectNoneBtn');
    if (selectNoneBtn) selectNoneBtn.addEventListener('click', function () { setAllSelected(false); });

    const startBtn = $('startPrintBtn');
    if (startBtn) startBtn.addEventListener('click', function () { startPrinting(); });
    const retryFailedBtn = $('retryFailedBtn');
    if (retryFailedBtn) {
      retryFailedBtn.addEventListener('click', function () {
        if (!state.lastFailed.length) return;
        startPrinting(state.lastFailed);
      });
    }
    const dismissResultBtn = $('dismissResultBtn');
    if (dismissResultBtn) {
      dismissResultBtn.addEventListener('click', function () {
        const card = $('resultCard');
        if (card) card.hidden = true;
      });
    }

    const refreshLogsBtn = $('refreshLogsBtn');
    if (refreshLogsBtn) refreshLogsBtn.addEventListener('click', function () { refreshLogs(); });
    const clearBtn = $('clearMyFilesBtn');
    if (clearBtn) clearBtn.addEventListener('click', requestClearMyFiles);

    const step3Prev = $('step3PrevBtn');
    if (step3Prev) step3Prev.addEventListener('click', function () { showStep(2); });
    const newOrder = $('newOrderBtn');
    if (newOrder) {
      newOrder.addEventListener('click', function () {
        showStep(1);
        toast('info', '继续添加文件', '上传新文件后可以直接进入打印。', 2600);
      });
    }
  }

  function openQrModal() {
    let objectUrl = null;
    showModal({
      title: '手机访问打印服务',
      body: h('div', 'qr-wrap', '正在生成二维码…'),
      buttons: [{ label: '关闭', value: null, variant: 'primary' }]
    }).then(function () {
      if (objectUrl) {
        try { URL.revokeObjectURL(objectUrl); } catch (e) { /* ignore */ }
        objectUrl = null;
      }
    });
    fetch('/api/qr', { credentials: 'same-origin', cache: 'no-store' }).then(function (res) {
      const contentType = (res.headers.get('Content-Type') || '').toLowerCase();
      if (!res.ok || contentType.indexOf('image/') !== 0) {
        return res.json().catch(function () { return null; }).then(function (data) {
          throw new Error((data && data.message) || '无法生成二维码');
        });
      }
      const qrUrl = res.headers.get('X-QR-URL') || '';
      return res.blob().then(function (blob) {
        const backdrop = $('modalBackdrop');
        if (!backdrop || backdrop.hidden) return;
        objectUrl = URL.createObjectURL(blob);
        const wrap = document.querySelector('#modalBody .qr-wrap');
        if (!wrap) return;
        clear(wrap);
        const img = h('img', 'qr-img');
        img.alt = '手机访问二维码';
        img.src = objectUrl;
        wrap.appendChild(img);
        wrap.appendChild(h('p', 'qr-url', qrUrl || '请用手机访问上面的服务地址'));
        wrap.appendChild(h('p', 'qr-tip', '让手机与电脑连接同一局域网（Wi-Fi），用手机相机扫码即可打开本服务，直接拍照上传并打印。'));
      });
    }).catch(function (err) {
      const wrap = document.querySelector('#modalBody .qr-wrap');
      if (wrap) {
        clear(wrap);
        wrap.appendChild(h('p', 'qr-error', '二维码生成失败：' + errorMessage(err)));
        wrap.appendChild(h('p', 'qr-tip', '也可以手动在手机浏览器输入上方「服务地址」。'));
      }
    });
  }

  function bindGlobalUI() {
    const qrBtn = $('qrBtn');
    if (qrBtn) qrBtn.addEventListener('click', openQrModal);
    const closeBtn = $('modalCloseBtn');
    if (closeBtn) closeBtn.addEventListener('click', function () { closeModal(null); });
    const backdrop = $('modalBackdrop');
    if (backdrop) {
      backdrop.addEventListener('click', function (event) {
        if (event.target === backdrop) closeModal(null);
      });
    }
    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape' && modalState.resolve) closeModal(null);
    });
    window.addEventListener('hashchange', function () {
      const target = hashStep();
      if (target && target !== state.step) showStep(target, { fromHash: true });
    });
    document.addEventListener('visibilitychange', function () {
      if (!document.hidden) checkHealth(true);
    });
    window.addEventListener('beforeunload', function () {
      if (state.preview.layoutUrl) {
        try { URL.revokeObjectURL(state.preview.layoutUrl); } catch (e) { /* ignore */ }
      }
    });
  }

  async function initApp() {
    applyIdentity();
    renderEnvChips();
    bindStepbar();
    bindUploadUI();
    bindViewer();
    bindPreviewUI();
    bindStep3UI();
    bindGlobalUI();
    updateStepbar();
    updateStep1Ctas();

    const healthPromise = checkHealth(true);
    await Promise.allSettled([
      loadSessionInfo(),
      refreshFiles({ quiet: true })
    ]);
    // 首次渲染
    renderFiles();
    updateStepbar();
    if (!document.hidden) checkHealth(true);
    state.healthTimer = setInterval(function () {
      if (!document.hidden) checkHealth(true);
    }, 45000);

    const firstPrintable = printableFiles()[0];
    if (firstPrintable) {
      ensurePerFile(firstPrintable.name);
      if (!state.focusedName) state.focusedName = firstPrintable.name;
    }

    const target = hashStep();
    if (target === 3 && canStep3()) {
      if (firstPrintable) setFocusFile(firstPrintable.name, { silent: true });
      showStep(3, { fromHash: true });
    } else if (target === 2 && canStep2()) {
      showStep(2, { fromHash: true });
    } else if (target) {
      showStep(1, { fromHash: true });
    }

    healthPromise.then(function (online) {
      if (!online) {
        toast('warning', '服务未连接', '无法连接打印服务，请确认服务已启动。', 6000);
      }
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    initApp().catch(function (err) {
      try {
        toast('error', '页面初始化失败', errorMessage(err) + ' 请刷新页面重试。');
      } catch (e) { /* ignore */ }
    });
  });

})();
