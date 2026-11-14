'use strict';
(() => {
  const names = [
    '设备与校准',
    '实验设计',
    '运行监控',
    '图像判相与复核',
    '相图与主动学习',
    '数据与报告',
  ];
  const keys = ['equipment', 'design', 'monitor', 'review', 'model', 'data'];
  const main = document.querySelector('main');
  const legacy = [...main.children];
  const ctx = {
    state: {},
    projectId: null,
    continuationRunId: null,
    continuationRequests: {},
    token: '',
    refreshing: false,
    pages: [],
    modules: [],
    node(tag, text, parent, attrs = {}) {
      const n = document.createElement(tag);
      if (text != null) n.textContent = text;
      Object.assign(n, attrs);
      if (parent) parent.append(n);
      return n;
    },
    project() {
      return (this.state.projects || []).find((p) => p.id === this.projectId);
    },
    samples() {
      return (this.state.samples || []).filter((s) => s.project_id === this.projectId);
    },
    continuationRuns() {
      return (this.state.continuation_runs || []).filter((r) => r.project_id === this.projectId);
    },
    continuationRun() {
      return this.continuationRuns().find((r) => r.id === this.continuationRunId);
    },
    runState(status) {
      return (
        {
          draft: '已建立，未启动',
          running: '自动研究中',
          waiting_review: '等待人工复核',
          paused: '已暂停续加',
          budget_complete: '已到预算',
          exhausted: '当前网格无可达点，已结束',
          stopped: '已停止',
          interrupted: '已中断，需处理',
          blocked: '条件不满足',
        }[status] ||
        status ||
        '未知'
      );
    },
    async continuationCommand(operation) {
      const run = this.continuationRun();
      if (!run) throw Error('先选择同瓶研究任务');
      const key = operation + ':' + run.id;
      this.continuationRequests[key] ||= crypto.randomUUID();
      const result = await this.api('continuation_' + operation, {
        run_id: run.id,
        request_id: this.continuationRequests[key],
      });
      delete this.continuationRequests[key];
      return result;
    },
    sampleName(s) {
      const current = (this.state.bottles || []).some((b) => b.sample_id === s.id);
      const historical = s.continuation_replaced_at || s.released_at;
      return `瓶${s.bottle || '待分配'}${current ? ' · 当前' : historical ? ' · 历史' : ''}${s.kind === 'continuation' ? ' · 补加后' : ''} · ${s.name || s.id.slice(0, 8)}`;
    },
    tell(message, error = false) {
      this.message.textContent = message;
      this.message.classList.toggle('error', error);
    },
    async request(path, body) {
      if (body && !this.token) this.token = (await (await fetch('/api/token')).json()).token;
      const r = await fetch(
        path,
        body
          ? {
              method: 'POST',
              headers: { 'Content-Type': 'application/json', 'X-Control-Token': this.token },
              body: JSON.stringify(body),
            }
          : { cache: 'no-store' },
      );
      const value = await r.json();
      if (!r.ok || value.ok === false || value.error) throw Error(value.error || '请求失败');
      return value;
    },
    api(op, body) {
      return this.request('/api/lab/' + op, body);
    },
    card(parent, title, note) {
      const section = this.node('section', null, parent, { className: 'card' });
      this.node('h2', title, section);
      if (note) this.node('p', note, section, { className: 'muted' });
      return section;
    },
    button(parent, text, action, id) {
      const b = this.node('button', text, parent, { type: 'button' });
      if (id) b.id = id;
      b.onclick = async () => {
        if (b.disabled) return;
        b.disabled = true;
        try {
          await action();
          await this.refresh();
        } catch (e) {
          this.tell(e.message, true);
        } finally {
          b.disabled = false;
        }
      };
      return b;
    },
    form(parent, fields, label, action, id) {
      const f = this.node('form', null, parent, { className: 'lab-form' });
      if (id) f.id = id;
      for (const field of fields) {
        const [name, title, value = '', options] = field;
        const l = this.node('label', title, f);
        const input = this.node(options ? 'select' : 'input', null, l, { name });
        if (options)
          for (const [v, text] of options) this.node('option', text, input, { value: v });
        input.value = value;
      }
      const b = this.node('button', label, f, { type: 'submit' });
      let pendingId = null,
        pendingFingerprint = null;
      f.onsubmit = async (e) => {
        e.preventDefault();
        if (b.disabled) return;
        b.disabled = true;
        const data = Object.fromEntries(
          [...f.elements]
            .filter((n) => n.name)
            .map((n) => [n.name, n.type === 'checkbox' ? n.checked : n.value]),
        );
        const fingerprint = JSON.stringify([
          this.projectId,
          document.getElementById('review-sample')?.value,
          document.getElementById('monitor-sample')?.value,
          data,
        ]);
        if (pendingFingerprint !== fingerprint) {
          pendingId = null;
          pendingFingerprint = fingerprint;
        }
        pendingId ||= crypto.randomUUID();
        try {
          await action(data, pendingId);
          pendingId = null;
          await this.refresh();
        } catch (err) {
          this.tell(err.message, true);
        } finally {
          b.disabled = false;
        }
      };
      return f;
    },
    table(parent, headings, rows) {
      parent.replaceChildren();
      const wrapper = this.node('div', null, parent, { className: 'table-scroll' });
      const t = this.node('table', null, wrapper);
      const header = this.node('tr', null, this.node('thead', null, t));
      headings.forEach((h) => this.node('th', h, header));
      const body = this.node('tbody', null, t);
      rows.forEach((row) => {
        const tr = this.node('tr', null, body);
        row.forEach((v) => this.node('td', v == null ? '—' : String(v), tr));
      });
    },
    options(select, values, empty = '请选择') {
      const old = select.value;
      select.replaceChildren();
      this.node('option', empty, select, { value: '' });
      for (const [value, text] of values) this.node('option', text, select, { value });
      if (old && [...select.options].some((o) => o.value === old)) select.value = old;
      else if (values.length) select.value = values[0][0];
    },
    selectSample(select) {
      this.options(
        select,
        this.samples().map((s) => [s.id, this.sampleName(s)]),
      );
    },
    fraction(v) {
      return Number.isFinite(v) ? (100 * v).toFixed(3) + '%' : '未知';
    },
    mass(v) {
      return Number.isFinite(v) ? v.toFixed(4) : '未知';
    },
    selectionReason(reason) {
      return (
        {
          reachable_boundary_coverage: '优先覆盖可达的相边界候选',
          reachable_two_phase_neighborhood: '探索可达的双相邻域',
          uniform_coverage: '均匀覆盖',
          uncertainty_and_coverage: '分类不确定与覆盖距离',
        }[reason] ||
        reason ||
        '见冻结选点记录'
      );
    },
    basis(v) {
      return { estimated: '估计', measured: '实测', unavailable: '无有效依据' }[v] || '未知';
    },
    stop() {
      document.getElementById('stopBtn').click();
    },
    chooseProject(id) {
      if (this.projectId === id) return;
      this.projectId = id;
      this.continuationRunId = null;
      this.modules.forEach((m) => m.projectChanged?.());
      this.render();
    },
    render() {
      if (!this.continuationRun() && this.continuationRuns().length)
        this.continuationRunId = this.continuationRuns().at(-1).id;
      this.options(
        this.projectSelect,
        (this.state.projects || []).map((p) => [
          p.id,
          `${p.name} · 盐${p.salt_port}口 · 第${p.version || 1}版`,
        ]),
      );
      this.projectSelect.value = this.projectId || '';
      const p = this.project();
      this.projectText.textContent = p
        ? `当前页面项目：${p.name}；盐${p.salt_port}口；设定${p.temperature_c}℃；手工实测温度${p.measured_temperature_c ?? '未记录'}℃。无恒温控制。`
        : '选择或创建项目。';
      this.modules.forEach((m) => m.render?.());
      const current = (this.state.samples || []).find(
        (s) => (s.execution_state || s.status) === 'preparing',
      );
      const stop = {
        requested: '已请求停止',
        confirmed: '已确认停止',
        unconfirmed: '无法确认停止',
      }[this.state.stop_status];
      const hardware = ['connectionStatus', 'pumpStatus', 'valveStatus']
        .map((id) => document.getElementById(id)?.textContent || '')
        .join(' | ');
      this.status.textContent = `${hardware} | 当前样品：${current ? this.sampleName(current) : '无'} | 批次：${this.state.active_project_id ? (this.state.projects || []).find((p) => p.id === this.state.active_project_id)?.name || this.state.active_project_id : '未启动'} | ${stop || (this.state.running ? '调度运行' : '调度暂停')} | 硬件急停：实体按钮`;
    },
    async refresh() {
      if (this.refreshing) return;
      this.refreshing = true;
      try {
        this.state = await this.api('state');
        if (!this.projectId && this.state.projects?.length)
          this.chooseProject(this.state.projects.at(-1).id);
        this.render();
        const camera = window.ATPSCamera?.status();
        if (camera) {
          const heartbeat = await this.api('camera', camera);
          if (camera.ready)
            for (const job of this.state.photo_jobs || []) {
              if (job.status !== 'due' || job.due_at * 1000 > Date.now()) continue;
              try {
                const frame = window.ATPSCamera.capture();
                await this.api('capture', {
                  job_id: job.id,
                  ...frame,
                  mapping_version: heartbeat.mapping_version ?? this.state.camera.mapping_version,
                });
              } catch (error) {
                this.tell(`本次拍照未完成：${error.message}；其他到期任务继续。`, true);
                try {
                  await this.api('photo_failure', { job_id: job.id, reason: error.message });
                } catch (_) {
                  /* A storage failure must not block other photographs or stop. */
                }
              }
            }
        }
      } catch (e) {
        this.tell(e.message, true);
      } finally {
        this.refreshing = false;
      }
    },
  };
  const nav = ctx.node('nav', null);
  main.before(nav);
  nav.id = 'lab-nav';
  names.forEach((name, i) => {
    const page = ctx.node('div', null, main, {
      className: 'lab-page',
      hidden: i !== 0,
      id: 'page-' + keys[i],
    });
    ctx.pages.push(page);
    const b = ctx.node('button', name, nav, { type: 'button' });
    b.setAttribute('aria-current', String(i === 0));
    b.onclick = () => {
      ctx.pages.forEach((p, j) => (p.hidden = i !== j));
      nav
        .querySelectorAll('button')
        .forEach((n, j) => n.setAttribute('aria-current', String(i === j)));
    };
  });
  legacy.forEach((n) => ctx.pages[n.classList.contains('imaging') ? 3 : 0].append(n));
  ctx.message = ctx.node('p', '', nav, { role: 'status', id: 'lab-message' });
  const projectBar = ctx.node('div', null);
  nav.after(projectBar);
  projectBar.className = 'lab-project';
  ctx
    .node('label', '当前项目', projectBar)
    .append((ctx.projectSelect = ctx.node('select', null, null, { id: 'lab-project-select' })));
  ctx.projectSelect.onchange = () => ctx.chooseProject(ctx.projectSelect.value);
  ctx.projectText = ctx.node('p', '', projectBar);
  const statusBar = ctx.node('div', null, document.body, { id: 'lab-status' });
  ctx.status = ctx.node('span', '', statusBar);
  // All software stop controls use the same priority handler installed by console.js.
  const stopButton = ctx.node('button', '立即停止 · 保留连接', statusBar, {
    type: 'button',
    id: 'lab-stop',
  });
  stopButton.onclick = () => ctx.stop();
  keys.forEach((key, i) => ctx.modules.push(window.ATPSLabPages[key](ctx, ctx.pages[i])));
  window.ATPSLab = ctx;
  window.addEventListener('atps:stop-status', (e) => {
    ctx.state.stop_status = e.detail;
    ctx.render();
  });
  ctx.refresh();
  setInterval(() => ctx.refresh(), 2000);
})();
