import {store} from '../stores/entities.js';
import {session} from '../stores/session.js';
import {rekeyAPI} from '../api/entities.js';
import {table} from '../components/common/table.js';
import {esc} from '../utils/html.js';
import {message} from '../hooks/action.js';

const state = {runId: null, detail: null, read: null, failover: null, busy: false, render: null};
const RUN_BADGE = {rewriting: '改写中', retired: '已退役'};

async function refresh() {
  await load();
  if (state.render) await state.render();
}

async function act(fn, ok) {
  if (state.busy) return;
  state.busy = true;
  let error = null;
  try { await fn(); }
  catch (e) { error = e; }
  finally { state.busy = false; }
  await refresh();
  if (error) message(error.message, true); else message(ok);
}

async function load() {
  store.rekeyRuns = await rekeyAPI.runs();
  if (!store.rekeyRuns.length) { state.detail = null; return; }
  if (!state.runId || !store.rekeyRuns.some(r => r.id === state.runId)) state.runId = store.rekeyRuns[0].id;
  state.detail = await rekeyAPI.get(state.runId);
}

function keyLabel(d, id) {
  const k = (d.keys || []).find(k => k.id === id);
  return k ? `${k.label} (#${k.id})` : `#${id}`;
}

function progressBar(d) {
  const {rewritten, total} = d.progress;
  const pct = total ? Math.round(rewritten / total * 100) : 0;
  return `<div class="progress"><div style="width:${pct}%"></div></div>
    <p class="progress-note">已改页 <b>${rewritten}</b> / ${total} · 旧钥残留 <b>${d.old_key_pages}</b> 页 · tick ${d.run.tick} / 退役 ${d.run.retire_at_tick}</p>`;
}

function runsTable(runs) {
  return table(['编号', '演练', '状态', '进度', ''], runs.map(r =>
    [r.id, esc(r.label), RUN_BADGE[r.status] || r.status,
     `${r.tick}/${r.retire_at_tick} tick`,
     `<a href="#" data-run="${r.id}">${r.id === state.runId ? '当前查看' : '打开 →'}</a>`]));
}

function startForm(keys) {
  if (session.role === 'viewer') return '<p class="readonly">观察员只读；开始换钥需要操作员或管理员。</p>';
  const active = keys.filter(k => k.status === 'active');
  const opts = active.map(k => `<option value="${k.id}">${esc(k.label)}</option>`).join('');
  if (active.length < 2) return '<p class="notice">至少需要两把可用密钥。管理员可先在左侧登记新密钥。</p>';
  return `<form id="rekey-form"><label>演练名称<input name="label" value="晚班换钥" required maxlength="120"></label>
    <div class="fields"><label>旧钥<select name="old_key_id">${opts}</select></label>
    <label>新钥<select name="new_key_id">${opts}</select></label></div>
    <div class="fields"><label>页面数<input name="page_count" type="number" min="4" max="60" value="12"></label>
    <label>每批页数<input name="batch_size" type="number" min="1" max="20" value="4"></label></div>
    <label>退役 tick<input name="retire_at_tick" type="number" min="1" max="50" value="6"></label>
    <button type="submit">开始换钥 →</button></form>`;
}

function replicaRows(d) {
  return d.replicas.map(r => {
    const controls = session.role === 'admin'
      ? `<button class="mini" data-replica="${r.id}" data-field="available" data-value="${!r.available}">${r.available ? '置为不可达' : '恢复可达'}</button>
         <button class="mini" data-replica="${r.id}" data-field="new_key_ready" data-value="${!r.new_key_ready}">${r.new_key_ready ? '撤新钥' : '发新钥'}</button>`
      : '';
    return [esc(r.name), esc(r.role) + ' / 任期 ' + r.term, r.log_position,
      r.available ? '可达' : '不可达', r.new_key_ready ? '已就绪' : '未就绪',
      `${r.applied_version}/${d.page_total}`,
      r.covers_all_with_new_key ? '<b class="ok">可接班</b>' : '<span class="muted">不足以接班</span>',
      controls];
  });
}

function readResult(read) {
  if (!read) return '<p class="notice">选择节点与页面后读取，受阻时会在下方给出原因。</p>';
  return `<div class="notice ${read.allowed ? '' : 'error'}"><b>${read.allowed ? '可读' : '受阻'}</b>：${esc(read.reason)}<br>
    页 ${esc(read.page)} · 值 <code>${esc(read.value)}</code> · 版本 ${read.version} · ${esc(read.key)}（${read.key_role === 'new' ? '新钥' : '旧钥'}）</div>`;
}

function failoverBlock() {
  const out = state.failover;
  if (!out) return '<p class="notice">切主先走多数派、隔离、任期与 RPO 规则，再要求候选人只靠新钥读全数据。</p>';
  const r = out.result;
  return `<section class="verdict"><div><p>切主结论</p><h2>${r.decision === 'approved' ? '允许切主' : r.decision === 'blocked' ? '安全阻断' : '计算失败'}</h2></div>
    <p>${r.winner ? '候选 ' + esc(r.winner) : esc(r.error || '看下方规则')}<br>新任期 ${r.new_term ?? '—'} · 多数派 ${r.reachable_votes ?? '—'}/${r.quorum ?? '—'}</p></section>
    ${table(['规则', '判定', '证据'], (r.checks || []).map(c => [esc(c.rule), c.pass ? '通过' : '阻断', esc(c.evidence)]))}
    ${table(['备库', '日志位置', '落后', '候选', '被排除原因'], (r.candidates || []).map(c =>
      [esc(c.name), c.log_position, c.lag, c.eligible ? '合格' : '不合格',
       c.disqualified ? '无法只靠新钥读全数据' : c.rpo_eligible ? '—' : 'RPO 超出允许损失']))}
    ${table(['备库', '新钥就绪', '已复制页', '旧钥残留页', '新钥全覆盖'], (r.new_key_coverage || []).map(c =>
      [esc(c.name), c.new_key_ready ? '是' : '否', `${c.applied_version}/${c.page_total}`, c.old_key_pages, c.eligible ? '是' : '否']))}`;
}

function detailPanel(d) {
  if (!d) return '<section class="empty"><h2>还没有换钥演练</h2><p>先在上方开始一次换钥，随后可逐页写入、分批推进并查看读值。</p></section>';
  const run = d.run;
  const pageOptions = d.pages.map(p => `<option value="${p.id}">${esc(p.label)} · v${p.version} · ${p.key_id === run.new_key_id ? '新钥' : '旧钥'}</option>`).join('');
  const nodeOptions = d.replicas.map(r => `<option value="${r.id}">${esc(r.name)}</option>`).join('');
  const last = d.batches[d.batches.length - 1];
  return `<section class="verdict"><div><p>换钥状态</p><h2>${d.retired ? '已退役' : '改写中'}</h2></div>
    <p>旧钥 ${esc(keyLabel(d, run.old_key_id))} → 新钥 ${esc(keyLabel(d, run.new_key_id))}<br>${last ? `最近批次 #${last.batch_no}：${esc(last.status)}` : '尚未装载批次'}</p></section>
    ${progressBar(d)}
    <div class="toolbar">
      <button data-act="advance" ${d.retired ? 'disabled' : ''}>推进一批</button>
      <button class="secondary" data-act="prepare" ${d.retired ? 'disabled' : ''}>装载下一批（不改页）</button>
      <button class="secondary" data-act="interrupt" ${d.retired ? 'disabled' : ''}>模拟改页中断</button>
      <button class="secondary" data-act="retry" ${!last || d.retired ? 'disabled' : ''}>重试最近批次</button>
      <button class="secondary" data-act="catchup">备库追平</button>
      <button class="secondary" data-act="retire" ${d.retired || session.role !== 'admin' ? 'disabled' : ''}>立即退役</button>
      <button class="secondary" data-act="export">导出 JSON</button>
    </div>
    <h2>备库与接班资格</h2>
    ${table(['备库', '角色/任期', '日志位置', '可达', '新钥', '已复制页', '新钥全覆盖', '调整'], replicaRows(d))}
    <h2>改页批次</h2>
    ${table(['批次', '状态', '条目'], d.batches.map(b => [b.batch_no, esc(b.status),
      b.items.map(i => `${esc(i.page)}:${esc(i.status)}`).join(' · ')]))}
    <section class="split"><div><h2>写一页（立即用新钥）</h2>
      <form id="write-form"><label>页面<select name="page_id">${pageOptions}</select></label>
      <label>新值<input name="value" value="updated" required maxlength="500"></label>
      <button type="submit">写入</button></form></div>
      <div><h2>读一页（看值与受阻原因）</h2>
      <form id="read-form"><label>读取节点<select name="node_id">${nodeOptions}</select></label>
      <label>页面<select name="page_id">${pageOptions}</select></label>
      <label class="check"><input name="recovery" type="checkbox"> 故障恢复通道（保留旧钥）</label>
      <button type="submit">读取</button></form></div></section>
    ${readResult(state.read)}
    <h2>切主门禁（沿用原选主规则）</h2>
    <form id="failover-form" class="inline-form">
      <label class="check"><input name="fail_primary" type="checkbox" checked> 主节点故障</label>
      <label class="check"><input name="fence_primary" type="checkbox" checked> 旧主隔离证明</label>
      <label>允许日志损失<input name="max_data_loss" type="number" min="0" value="1000"></label>
      <label>任期门槛<input name="required_term" type="number" min="1" value="7"></label>
      <button type="submit">运行切主演练</button></form>
    ${failoverBlock()}`;
}

export function rekeyPage() {
  const keys = store.rekeyKeys || [], runs = store.rekeyRuns || [];
  return `<section class="split"><div><h2>密钥环</h2>
    ${table(['密钥', '状态', '保留供恢复'], keys.map(k => [esc(k.label), esc(k.status), k.retain_for_recovery ? '是' : '否']))}
    ${session.role === 'admin' ? `<form id="key-form" class="inline-form"><label>新密钥名<input name="label" value="k-next" required minlength="2"></label>
      <label class="check"><input name="retain_for_recovery" type="checkbox" checked> 保留旧钥供恢复</label>
      <button type="submit">登记密钥</button></form>` : ''}
    </div><div><h2>开始换钥演练</h2>${startForm(keys)}</div></section>
    <h2>演练记录</h2>${runsTable(runs)}
    ${detailPanel(state.detail)}`;
}

export function bindRekey(render) {
  state.render = render;
  document.querySelectorAll('[data-run]').forEach(el => el.onclick = async e => {
    e.preventDefault(); state.runId = Number(el.dataset.run); state.read = null; state.failover = null; await refresh();
  });
  document.querySelectorAll('[data-act]').forEach(el => el.onclick = () => {
    const d = state.detail; if (!d) return;
    const runId = d.run.id, last = d.batches[d.batches.length - 1];
    const actions = {
      advance: [() => rekeyAPI.advance(runId), '已推进一批'],
      prepare: [() => rekeyAPI.prepare(runId), '已装载下一批'],
      interrupt: [() => rekeyAPI.interrupt(runId), '已模拟中断，可重试'],
      retry: [() => rekeyAPI.retry(runId, last.batch_no), '已重放批次'],
      catchup: [() => rekeyAPI.catchup(runId), '备库已追平'],
      retire: [() => rekeyAPI.retire(runId), '已退役，旧钥不再对外'],
      export: [() => downloadRun(runId), '导出完成'],
    };
    const [fn, ok] = actions[el.dataset.act] || [];
    if (fn) act(fn, ok);
  });
  document.querySelectorAll('[data-replica]').forEach(el => el.onclick = () => act(
    () => rekeyAPI.replica(state.detail.run.id, Number(el.dataset.replica),
                           {[el.dataset.field]: el.dataset.value === 'true'}), '备库状态已更新'));
  document.querySelector('#key-form')?.addEventListener('submit', async e => {
    e.preventDefault(); const f = e.target;
    if (state.busy) return; state.busy = true;
    let error = null;
    try {
      await rekeyAPI.createKey({label: f.elements.label.value,
                                retain_for_recovery: f.elements.retain_for_recovery.checked});
      store.rekeyKeys = await rekeyAPI.keys();
    } catch (err) { error = err; }
    finally { state.busy = false; }
    if (state.render) await state.render();
    message(error ? error.message : '密钥已登记', !!error);
  });
  document.querySelector('#rekey-form')?.addEventListener('submit', async e => {
    e.preventDefault(); const f = e.target;
    if (state.busy) return; state.busy = true;
    let error = null;
    try {
      const d = Object.fromEntries(new FormData(f));
      for (const k of ['old_key_id', 'new_key_id', 'page_count', 'batch_size', 'retire_at_tick']) d[k] = Number(d[k]);
      const snap = await rekeyAPI.create(d); state.runId = snap.run.id; await load();
    } catch (err) { error = err; }
    finally { state.busy = false; }
    if (state.render) await state.render();
    message(error ? error.message : '换钥已开始', !!error);
  });
  document.querySelector('#write-form')?.addEventListener('submit', e => {
    e.preventDefault(); const d = Object.fromEntries(new FormData(e.target));
    act(() => rekeyAPI.write(state.detail.run.id, Number(d.page_id), d.value), '已用新钥写入');
  });
  document.querySelector('#read-form')?.addEventListener('submit', async e => {
    e.preventDefault(); const f = e.target, d = Object.fromEntries(new FormData(f));
    try {
      state.read = await rekeyAPI.read(state.detail.run.id, Number(d.node_id),
                                       Number(d.page_id), f.elements.recovery.checked);
      await refresh();
    } catch (err) { message(err.message, true); }
  });
  document.querySelector('#failover-form')?.addEventListener('submit', async e => {
    e.preventDefault(); const f = e.target;
    const data = {fail_primary: f.elements.fail_primary.checked,
                  fence_primary: f.elements.fence_primary.checked,
                  max_data_loss: Number(f.elements.max_data_loss.value),
                  required_term: Number(f.elements.required_term.value)};
    try { state.failover = await rekeyAPI.failover(state.detail.run.id, data); await refresh(); }
    catch (err) { message(err.message, true); }
  });
}

async function downloadRun(runId) {
  const r = await fetch(rekeyAPI.export(runId), {headers: {Authorization: 'Bearer ' + session.token}});
  if (!r.ok) throw new Error('导出失败');
  const u = URL.createObjectURL(await r.blob());
  const a = document.createElement('a'); a.href = u; a.download = `rekey-${runId}.json`; a.click();
  setTimeout(() => URL.revokeObjectURL(u), 1000);
}

export async function loadRekey() {
  store.rekeyKeys = await rekeyAPI.keys();
  await load();
}
