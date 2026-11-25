import { avatar, esc, icon, relTime, safeHref, statusChip } from './ui.js';
import { bindGraph, renderGraph } from './graph.js';
import { bindView, renderView } from './views.js';
import { emptyData, isAccessDenied, readHistory, readLive, readTaskDetail, sidecarError, webuiReturnHref } from './live.js';

const pages = {
  graph: ['Live Agent Graph', 'Граф агентов', 'Agent topology and recent handoffs', 'Связи агентов и недавние передачи задач'],
  team: ['Team Monitor', 'Монитор команды', 'Profiles, executors and subagents', 'Профили, исполнители и субагенты'],
  communications: ['Communications', 'Коммуникации', 'Delegations, tool events and results', 'Делегирование, вызовы инструментов и результаты'],
  missions: ['Mission Control', 'Управление задачами', 'Project boards from Hermes Kanban', 'Доски проектов из Hermes Kanban'],
  usage: ['Usage Analytics', 'Аналитика', 'Tokens, latency and request activity', 'Токены, задержки и активность запросов'],
  infrastructure: ['Infrastructure', 'Инфраструктура', 'Compute telemetry and service health', 'Телеметрия вычислений и состояние сервисов'],
};

const shell = document.querySelector('#hcc');
const inspector = document.querySelector('#hcc-inspector');
const readPref = key => {
  try { return localStorage.getItem(`hcc:${key}`); } catch { return null; }
};
const savePref = (key, value) => {
  try { localStorage.setItem(`hcc:${key}`, value); } catch { /* Storage is optional. */ }
};

const params = new URLSearchParams(location.search);
const embedded = parent !== window && location.protocol === 'about:';
const returnHref = webuiReturnHref(document.referrer);
const STALE_WINDOW = 45000;
const RETRY_DELAY = 2000;
const state = {
  view: 'graph', theme: readPref('theme') === 'light' ? 'light' : 'dark',
  lang: readPref('lang') === 'ru' ? 'ru' : 'en',
  demo: params.get('demo') === '1', data: emptyData(), filters: {}, histories: new Map(),
  updated: null, failure: null, expiry: null, polling: false, pending: false, cleanup: null, closed: false, epoch: 0,
};
let demoModule;
let returnFocus;
const t = (en, ru) => state.lang === 'ru' ? ru : en;
const fmt = (value, options) => typeof value === 'number' && Number.isFinite(value)
  ? new Intl.NumberFormat(state.lang, options).format(value)
  : '—';

function detail(title, html) {
  if (!inspector.open) returnFocus = document.activeElement;
  inspector.innerHTML = `<div class="hcc-dialog-head"><h2 id="hcc-inspector-title">${esc(title)}</h2><button type="button" class="hcc-icon" data-inspector-close aria-label="${t('Close', 'Закрыть')}">${icon('close')}</button></div><div class="hcc-dialog-body">${html}</div>`;
  inspector.querySelector('[data-inspector-close]').onclick = () => inspector.close();
  if (!inspector.open) inspector.showModal();
  return inspector;
}

function currentRange() {
  return state.filters[state.view]?.range || '24h';
}

function canRenderRefresh() {
  return !inspector.open && !document.fullscreenElement &&
    !document.activeElement?.matches('input,select,textarea') &&
    !document.querySelector('.hcc-graph-stage.is-panning');
}

async function loadHistory(range, force = false) {
  if (!state.demo && (!state.data.access || state.failure)) return null;
  const existing = state.histories.get(range);
  if (existing?.request) return existing.request;
  if (!force && existing && Date.now() - existing.time < 30000) return existing.value;
  const epoch = state.epoch;
  const pending = Promise.resolve().then(async () => {
    if (state.demo) return (demoModule ||= await import('./demo.js')).demoHistory(range);
    return readHistory(range);
  }).catch(error => {
    handleAccessDenial(error, epoch);
    return {range, error: true};
  }).then(value => {
    if (epoch !== state.epoch || state.closed) return null;
    state.histories.set(range, {value, time: Date.now()});
    if (['usage', 'infrastructure'].includes(state.view) && currentRange() === range) {
      if (canRenderRefresh()) renderContent(true);
      else state.pending = true;
    }
    return value;
  });
  state.histories.set(range, {...existing, request: pending});
  return pending;
}

const ctx = {
  root: document, t, esc, fmt, icon, avatar, safeHref, detail,
  get lang() { return state.lang; },
  get data() { return {...state.data, history: state.histories.get(currentRange())?.value || null}; },
  get demo() { return state.demo; },
  get loading() { return !state.updated && !state.failure; },
  async taskDetail(id) {
    if (state.demo) return (demoModule ||= await import('./demo.js')).demoTaskDetail(id);
    if (!state.data.access || state.failure) throw new Error('Task detail unavailable');
    const epoch = state.epoch;
    let result;
    try {
      result = await readTaskDetail(id);
    } catch (error) {
      handleAccessDenial(error, epoch);
      throw error;
    }
    if (epoch !== state.epoch || !state.data.access) throw new Error('Task detail unavailable');
    return result;
  },
  loadHistory,
  filters(view) { return state.filters[view] ||= {}; },
  update: () => renderContent(true),
  status: value => statusChip(value, t),
  rel: stamp => relTime(stamp, state.lang),
  navigate,
};

function navigate(view) {
  if (!Object.hasOwn(pages, view)) return;
  state.view = view;
  renderHeader();
  renderContent();
}

function renderShell() {
  document.documentElement.lang = state.lang;
  document.documentElement.dataset.theme = state.theme;
  const exitLabel = t('Return to Hermes WebUI', 'Вернуться в Hermes WebUI');
  const exitControl = embedded
    ? `<button type="button" class="hcc-icon" id="hcc-exit" title="${exitLabel}" aria-label="${exitLabel}">${icon('close')}</button>`
    : returnHref ? `<a class="hcc-icon" href="${esc(returnHref)}" title="${exitLabel}" aria-label="${exitLabel}">${icon('close')}</a>` : '';
  shell.innerHTML = `
    <header class="hcc-masthead">
      <div class="hcc-brand"><span class="hcc-mark">${icon('logo', 23)}</span><div><strong>Hermes</strong><span>Command Center</span></div></div>
      <div class="hcc-head-tools">
        <button type="button" id="hcc-mode" class="hcc-mode"></button>
        <span class="hcc-divider"></span>
        <button type="button" class="hcc-icon" id="hcc-refresh" aria-label="${t('Refresh data', 'Обновить данные')}" title="${t('Refresh data', 'Обновить данные')}">${icon('refresh')}</button>
        <button type="button" class="hcc-icon" id="hcc-theme" aria-label="${t('Toggle theme', 'Переключить тему')}" title="${t('Toggle theme', 'Переключить тему')}">${icon(state.theme === 'dark' ? 'sun' : 'moon')}</button>
        <button type="button" class="hcc-icon hcc-lang" id="hcc-lang" aria-label="${t('Switch to Russian', 'Switch to English')}">${state.lang.toUpperCase()}</button>
        ${exitControl}
      </div>
    </header>
    <div class="hcc-layout">
      <aside class="hcc-nav" aria-label="${t('Navigation', 'Навигация')}">
        <nav id="hcc-pages"></nav>
        <div class="hcc-nav-foot" id="hcc-nav-foot"></div>
      </aside>
      <main class="hcc-main">
        <div class="hcc-pagehead" id="hcc-pagehead"></div>
        <div class="hcc-content"></div>
      </main>
    </div>`;
  shell.querySelector('#hcc-pages').onclick = e => {
    const button = e.target.closest('[data-page]');
    if (button) navigate(button.dataset.page);
  };
  shell.querySelector('#hcc-theme').onclick = () => {
    state.theme = state.theme === 'dark' ? 'light' : 'dark';
    savePref('theme', state.theme);
    renderShell();
  };
  shell.querySelector('#hcc-lang').onclick = () => {
    state.lang = state.lang === 'en' ? 'ru' : 'en';
    savePref('lang', state.lang);
    renderShell();
  };
  shell.querySelector('#hcc-mode').onclick = async () => {
    state.demo = !state.demo;
    state.epoch += 1;
    state.polling = false;
    state.updated = null;
    state.failure = null;
    clearTimeout(state.expiry);
    state.expiry = null;
    state.data = emptyData();
    state.filters = {};
    state.histories.clear();
    renderHeader();
    renderContent();
    await poll();
  };
  shell.querySelector('#hcc-refresh').onclick = async () => {
    await poll(true);
  };
  const exit = shell.querySelector('#hcc-exit');
  if (exit) exit.onclick = () => parent.postMessage({ type: 'hcc:close' }, new URL(document.baseURI).origin);
  renderHeader();
  renderContent();
}

function renderHeader(preserveNavigation = false) {
  if (!preserveNavigation) shell.querySelector('#hcc-pages').innerHTML = Object.entries(pages).map(([key, names]) =>
    `<button type="button" class="hcc-nav-item" data-page="${key}" aria-label="${t(names[0], names[1])}" title="${t(names[0], names[1])}" aria-current="${key === state.view ? 'page' : 'false'}">${icon(key, 17)}<span>${t(names[0], names[1])}</span></button>`
  ).join('');
  const mode = shell.querySelector('#hcc-mode');
  const sources = state.failure?.sources || state.data.sources;
  const stale = state.failure && state.data.access;
  const available = !state.failure && sources.some(source => ['available', 'partial'].includes(source.status));
  const syncStatus = state.demo ? t('Demo data', 'Демоданные')
    : stale ? t('Showing last received data', 'Показаны последние полученные данные')
    : !state.updated && !state.failure ? t('Connecting', 'Подключение')
    : !available ? t('Sources unavailable', 'Источники недоступны')
    : sources.some(source => source.status !== 'available') ? t('Sources partly available', 'Часть источников доступна')
    : t('Sources connected', 'Источники подключены');
  const syncTime = state.updated ? `${state.failure ? t('Last successful update', 'Последнее успешное обновление') : t('Synced', 'Обновлено')} ${esc(ctx.rel(state.updated))}`
    : state.failure ? t('Waiting for the next attempt', 'Ожидание следующей попытки') : t('Checking availability', 'Проверка доступности');
  mode.classList.toggle('is-demo', state.demo);
  mode.innerHTML = `<i></i>${state.demo ? 'DEMO' : 'LIVE'}${icon('chevron', 13)}`;
  mode.title = state.demo ? t('Switch to live sources', 'Переключиться на рабочие источники') : t('Switch to demo data', 'Переключиться на демоданные');
  const names = pages[state.view];
  shell.querySelector('#hcc-pagehead').innerHTML = `<div><h1>${t(names[0], names[1])}</h1><p>${t(names[2], names[3])}</p></div>
    <div class="hcc-sync${state.failure ? ' is-interrupted' : ''}"><span class="hcc-sync-dot ${stale ? 'is-stale' : available ? 'is-ready' : ''}"></span><div><strong>${syncStatus}</strong><small>${syncTime}</small></div></div>`;
  shell.querySelector('#hcc-nav-foot').innerHTML = `<button class="hcc-source-link" type="button" data-sources aria-label="${t('Data sources', 'Источники данных')}">${icon('terminal', 16)}<span>${t('Data sources', 'Источники данных')}</span>${icon('chevron', 13)}</button>`;
  shell.querySelector('[data-sources]').onclick = () => detail(t('Data sources', 'Источники данных'), sources.length
    ? sources.map(source => `<section class="hcc-source-row"><div><strong>${esc(source.label)}</strong><p>${esc(t(source.detail, source.detailRu || source.detail))}</p>
      ${source.lastSuccessAt ? `<p>${t('Last successful collection','Последний успешный сбор')} · ${esc(ctx.rel(source.lastSuccessAt))}</p>` : ''}
      ${source.missing?.length ? `<p>${t('Missing','Нет данных')} · ${esc(source.missing.join(', '))}</p>` : ''}
      ${source.limits?.length ? `<ul>${source.limits.map(limit => `<li>${esc(limit)}</li>`).join('')}</ul>` : ''}
      ${source.impact ? `<p>${esc(source.impact)}</p>` : ''}
      ${source.recommendation ? `<p>${esc(source.recommendation)}</p>` : ''}
      </div>${ctx.status(source.status)}</section>`).join('')
    : `<p class="hcc-muted">${t('No sources connected.', 'Источники не подключены.')}</p>`);
  const refresh = shell.querySelector('#hcc-refresh');
  refresh.classList.toggle('is-spinning', state.polling);
  refresh.setAttribute('aria-busy', String(state.polling));
}

function renderContent(preserveScroll = false) {
  state.cleanup?.();
  state.cleanup = null;
  const content = shell.querySelector('.hcc-content');
  const scroll = preserveScroll ? content.scrollTop : 0;
  const chromeFocus = content.contains(document.activeElement) ? document.activeElement.id : '';
  content.dataset.page = state.view;
  content.innerHTML = state.view === 'graph' ? renderGraph(ctx) : renderView(state.view, ctx);
  state.cleanup = state.view === 'graph' ? bindGraph(ctx) : bindView(state.view, ctx);
  content.scrollTop = scroll;
  if (chromeFocus) document.getElementById(chromeFocus)?.focus({ preventScroll: true });
}

function discardCachedLive() {
  clearTimeout(state.expiry);
  state.expiry = null;
  state.data = state.failure;
  state.epoch += 1;
  state.polling = false;
  state.filters = {};
  state.histories.clear();
  state.pending = false;
  returnFocus = null;
  if (inspector.open) inspector.close();
  inspector.replaceChildren();
  renderHeader();
  renderContent(true);
}

function handleAccessDenial(error, epoch) {
  if (state.closed || state.demo || state.epoch !== epoch || !state.data.access || !isAccessDenied(error)) return;
  state.failure = {...emptyData(), sources: [sidecarError(error)]};
  discardCachedLive();
}

function expireCachedLive() {
  if (state.failure && state.data.access && Date.now() - state.updated >= STALE_WINDOW) discardCachedLive();
}

async function poll(force = false) {
  expireCachedLive();
  if (state.closed || state.polling || (!force && document.hidden)) return;
  const epoch = state.epoch;
  state.polling = true;
  shell.querySelector('#hcc-refresh')?.classList.add('is-spinning');
  let next;
  try {
    if (state.demo) {
      demoModule ||= await import('./demo.js');
      next = demoModule.demoData();
    } else {
      next = await readLive();
    }
  } catch {
    next = { ...emptyData(), sources: [{ id: 'webui', label: 'Hermes WebUI', status: 'unavailable' }] };
  }
  if (epoch !== state.epoch || state.closed) return;
  state.polling = false;
  if (state.demo || next.access) {
    clearTimeout(state.expiry);
    state.expiry = null;
    state.failure = null;
    state.data = next;
    state.updated = Date.now();
  } else {
    const firstFailure = !state.failure;
    state.failure = next;
    if (firstFailure && next.retryable) setTimeout(() => {
      if (state.failure === next) poll();
    }, RETRY_DELAY);
    if (state.data.access) {
      const remaining = STALE_WINDOW - (Date.now() - state.updated);
      if (!next.retryable || remaining <= 0) {
        discardCachedLive();
        return;
      }
      state.expiry ??= setTimeout(expireCachedLive, remaining);
    } else {
      state.data = next;
    }
  }
  renderHeader(Boolean(shell.querySelector('#hcc-pages :focus')));
  if (['usage', 'infrastructure'].includes(state.view)) loadHistory(currentRange());
  if (!canRenderRefresh()) {
    state.pending = true;
    return;
  }
  state.pending = false;
  renderContent(true);
}

inspector.addEventListener('close', () => {
  returnFocus?.focus();
  returnFocus = null;
  if (state.pending) {
    state.pending = false;
    renderContent(true);
  }
});
document.addEventListener('focusout', () => setTimeout(() => {
  if (state.pending && !inspector.open && !document.activeElement?.matches('input,select,textarea')) {
    state.pending = false;
    renderContent(true);
  }
}, 0));
document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });
window.addEventListener('pagehide', () => { state.closed = true; state.cleanup?.(); });
window.addEventListener('pageshow', e => { if (e.persisted) { state.closed = false; state.polling = false; poll(true); } });

renderShell();
poll();
setInterval(poll, 12000);
