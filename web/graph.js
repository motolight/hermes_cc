import { statusLabel, timestamp } from './ui.js';

const WIDTH = 1000;
const HEIGHT = 575;
const animatedEvents = new Set();
const liveStates = new Set(['active', 'running', 'working', 'generating', 'tool', 'in_progress', 'queued', 'stalled']);
const eventIcons = { delegation: 'arrow', message: 'communications', tool: 'terminal', result: 'check', error: 'alert', approval: 'task' };
const isTopology = agent => agent.kind === 'pi-runtime' || agent.kind === 'pi-role';
const isExecutor = agent => agent.kind !== 'profile' && !isTopology(agent) &&
  (agent.active ?? liveStates.has(agent.status));
const isDefaultProfile = agent => agent.kind === 'profile' && (agent.profile || agent.name) === 'default';
const piRouteModes = [
  { mode: 'recent-pi', dataKey: 'recentPi', label: ['Latest observed Pi route', 'Последний маршрут Pi'],
    heading: ['Latest observed Pi route', 'Последний наблюдавшийся маршрут Pi'] },
  { mode: 'recent-pi-delegation', dataKey: 'recentPiDelegation', label: ['Latest Open Agents route', 'Последний маршрут Open Agents'] },
];

function values(entities, key) {
  return [...new Set(entities.map(item => item[key]).filter(Boolean).map(String))].sort();
}

function graphModel(ctx) {
  const f = ctx.filters('graph');
  const routeMode = piRouteModes.find(option => option.mode === f.mode);
  const route = routeMode && ctx.data[routeMode.dataKey];
  if (route?.nodes?.length) {
    const agents = route.nodes;
    const shown = agents.slice(0, 24);
    const chosen = agents.find(agent => agent.id === f.routeViews?.[f.mode]?.selected) || shown[0];
    if (!shown.includes(chosen)) shown[shown.length - 1] = chosen;
    return { agents, shown, chosen, edges: visibleEdges(shown, route.links), events: [], tasks: [], route, routeMode };
  }
  const live = ctx.data.agents.filter(agent => agent.kind === 'profile' || isExecutor(agent));
  const inventory = new Map((ctx.data.piAgents || []).filter(isTopology).map(agent => [agent.id, agent]));
  for (const agent of live) inventory.set(agent.id, agent);
  const communications = ctx.data.events.filter(event => event.channel === 'communication');
  const entities = [...live, ...ctx.data.tasks, ...communications];
  const matching = item => ['project', 'team', 'session'].every(key => !f[key] || item[key] === f[key]);
  const scoped = Boolean(f.project || f.team || f.session);
  const events = communications.filter(matching).sort((a, b) => (timestamp(b.at) || 0) - (timestamp(a.at) || 0));
  const tasks = ctx.data.tasks.filter(matching);
  const relevant = new Set([...events.flatMap(item => [item.from, item.to]), ...tasks.map(item => item.assignee)]);
  const links = [...ctx.data.links, ...events.filter(event => ['message', 'delegation', 'result'].includes(event.kind)),
    ...(ctx.data.piLinks || [])];
  const connected = new Map();
  for (const link of ctx.data.links) {
    if (link.from && link.to && link.from !== link.to) {
      connected.set(link.from, (connected.get(link.from) || 0) + 2);
      connected.set(link.to, (connected.get(link.to) || 0) + 1);
    }
  }
  const score = agent => (connected.get(agent.id) || 0) + Number(agent.kind === 'profile') * 2 + Number(liveStates.has(agent.status));
  const priority = agent => isDefaultProfile(agent) ? 2 : Number(isTopology(agent));
  const eligible = [...inventory.values()]
    .filter(agent => !scoped || matching(agent) || relevant.has(agent.id) || relevant.has(agent.profile))
    .sort((a, b) => priority(b) - priority(a) || score(b) - score(a) || a.name.localeCompare(b.name));
  const agents = eligible.filter(agent => !f.kind || (f.kind === 'profile' ? agent.kind === 'profile' : isExecutor(agent)));
  const chosen = agents.find(agent => agent.id === f.selected) || agents[0];
  const shown = agents.slice(0, 24);
  if (chosen && !shown.includes(chosen)) shown[shown.length - 1] = chosen;
  return { agents, shown, chosen, events, tasks, entities, edges: visibleEdges(shown, links) };
}

function visibleEdges(shown, links) {
  const population = new Set(shown.map(agent => agent.id));
  const edges = new Map();
  for (const link of links) {
    if (!population.has(link.from) || !population.has(link.to) || link.from === link.to) continue;
    const key = `${link.from}\u0000${link.to}`;
    if (!edges.has(key)) edges.set(key, link);
  }
  return [...edges.values()];
}

export function placeAgents(agents, edges = []) {
  if (!agents.length) return [];
  if (agents.length === 1) return [{ x: 500, y: 285 }];
  if (agents.some(agent => agent.kind === 'pi' || isTopology(agent))) {
    const runtimeIds = new Set(agents.filter(agent => agent.kind === 'pi-runtime').map(agent => agent.id));
    const piIds = new Set(agents.filter(agent => agent.kind === 'pi').map(agent => agent.id));
    const runtimeRuns = new Set(edges.filter(edge => edge.kind === 'execution' &&
      runtimeIds.has(edge.from) && piIds.has(edge.to)).map(edge => edge.to));
    const childLevel = new Map();
    for (const {from, to, kind} of edges) {
      if (piIds.has(from) && kind !== 'message') childLevel.set(to, runtimeRuns.has(from) ? 4 : 3);
    }
    const levels = agents.map(agent => {
      if (agent.kind === 'profile') return 0;
      if (agent.kind === 'pi-runtime') return 2;
      if (agent.kind === 'pi') return runtimeRuns.has(agent.id) ? 3 : 2;
      if (agent.kind === 'pi-role') return 3;
      return childLevel.get(agent.id) || (agent.kind === 'subagent' ? 2 : 1);
    });
    const columns = new Map();
    for (const level of levels) columns.set(level, (columns.get(level) || 0) + 1);
    const ordered = [...columns.keys()].sort((a, b) => a - b);
    if (ordered.length > 1 && [...columns.values()].every(count => count <= 5)) {
      const rowByLevel = new Map();
      return levels.map(level => {
        const count = columns.get(level);
        const row = rowByLevel.get(level) || 0;
        rowByLevel.set(level, row + 1);
        return { x: 145 + ordered.indexOf(level) * 710 / (ordered.length - 1),
          y: 283 + (row - (count - 1) / 2) * Math.min(142, 426 / Math.max(1, count - 1)) };
      });
    }
  }
  if (agents.length <= 6) {
    const outer = [
      { x: 235, y: 142 }, { x: 765, y: 143 },
      { x: 207, y: 382 }, { x: 792, y: 373 }, { x: 502, y: 473 },
    ];
    return [{ x: 500, y: 281 }, ...outer.slice(0, agents.length - 1)];
  }
  if (agents.length <= 13) {
    const orbital = agents.slice(1).map((_, index) => {
      const theta = 2 * Math.PI * index / (agents.length - 1) - Math.PI / 2;
      return { x: 500 + 380 * Math.cos(theta), y: 280 + 193 * Math.sin(theta) };
    });
    return [{ x: 500, y: 274 }, ...orbital];
  }
  const rows = Math.ceil(agents.length / 5);
  return agents.map((_, index) => {
    const row = Math.floor(index / 5);
    const columns = Math.min(5, agents.length - row * 5);
    return { x: 500 + (index % 5 - (columns - 1) / 2) * 186, y: 280 + (row - (rows - 1) / 2) * 105 };
  });
}

function curve(a, b, trim = 0) {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const shift = Math.max(-36, Math.min(36, dx * -.13));
  const scale = trim / (Math.hypot(dx, dy) || 1);
  return `M ${a.x} ${a.y - 18} C ${a.x + dx * .37 + shift} ${a.y + dy * .08 - 18}, ${a.x + dx * .67 + shift} ${b.y - dy * .08 - 18}, ${b.x - dx * scale} ${b.y - dy * scale - 18}`;
}

function filterSelect(key, en, ru, options, ctx) {
  const { esc, t } = ctx;
  return `<select class="hcc-select" id="hcc-graph-${key}" data-graph-filter="${key}" aria-label="${t(en, ru)}"><option value="">${t(en, ru)}</option>${options.map(value => `<option value="${esc(value)}" ${ctx.filters('graph')[key] === value ? 'selected' : ''}>${esc(value)}</option>`).join('')}</select>`;
}

function mapSvg(model, coords) {
  const paths = model.edges.map(edge => {
    const kind = model.route ? 'historical'
      : ['delegation', 'delegate', 'message', 'dependency', 'configured', 'integration'].includes(edge.kind) ? edge.kind : 'observed';
    return { kind, path: curve(coords.get(edge.from), coords.get(edge.to), kind === 'historical' ? 34 : 0) };
  });
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const packets = reduced ? [] : model.events.filter(event => event.id && !animatedEvents.has(event.id) &&
    Date.now() - timestamp(event.at) < 45000 && Date.now() >= timestamp(event.at) &&
    event.from !== event.to &&
    coords.has(event.from) && coords.has(event.to)).slice(0, 10);
  return `<svg class="hcc-world-svg" width="${WIDTH}" height="${HEIGHT}" viewBox="0 0 ${WIDTH} ${HEIGHT}" aria-hidden="true">
    <defs><pattern id="hcc-grid" width="76" height="76" x="6" y="16" patternUnits="userSpaceOnUse"><path class="grid-line" d="M76 0H0v76" fill="none"/></pattern>
      <radialGradient id="hcc-orbit-fade"><stop offset="0%" stop-color="white"/><stop offset="88%" stop-color="white" stop-opacity=".3"/><stop offset="100%" stop-color="white" stop-opacity="0"/></radialGradient>
      <mask id="hcc-field-mask"><ellipse cx="500" cy="285" rx="509" ry="306" fill="url(#hcc-orbit-fade)"/></mask>
      <marker id="hcc-route-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0L8 4L0 8Z" fill="var(--amber)"/></marker>
    </defs>
    <g class="hcc-orbits">
      <rect x="5" y="5" width="990" height="565" fill="url(#hcc-grid)" stroke="none" mask="url(#hcc-field-mask)"/>
      <ellipse cx="500" cy="267" rx="324" ry="201" class="orbit-dash"/>
      <ellipse cx="500" cy="267" rx="398" ry="240"/>
      <circle cx="500" cy="267" r="83" class="orbit-dash"/>
      <circle cx="500" cy="267" r="118" stroke-opacity=".48"/>
      <path d="M500 25v43m0 398v43M105 267h44m702 0h44"/>
    </g>
    <g class="hcc-edge-glow">${paths.filter(edge => !['configured', 'historical'].includes(edge.kind)).map(edge => `<path d="${edge.path}"/>`).join('')}</g>
    <g class="hcc-edges">${paths.map(edge => `<path d="${edge.path}" class="kind-${edge.kind}" data-graph-link="${edge.kind}"${edge.kind === 'historical' ? ' marker-end="url(#hcc-route-arrow)"' : ''}/>`).join('')}</g>
    ${packets.map((event, index) => `<circle class="hcc-packet" r="3.5" opacity="0" data-packet-index="${model.events.indexOf(event)}"><animate attributeName="opacity" values="0;1;1;0" keyTimes="0;0.12;0.82;1" begin="${index * .19}s" dur="1.9s" fill="remove"/><animateMotion path="${curve(coords.get(event.from), coords.get(event.to))}" begin="${index * .19}s" dur="1.9s" fill="remove"/></circle>`).join('')}
  </svg>`;
}

function graphTime(value, ctx, routeTime = false) {
  const time = timestamp(value);
  if (!Number.isFinite(time)) return '';
  const label = new Intl.DateTimeFormat(ctx.lang, {dateStyle: 'medium', timeStyle: 'medium'}).format(time);
  return `<time datetime="${new Date(time).toISOString()}"${routeTime ? ' data-pi-route-time' : ''}>${ctx.esc(label)}</time>`;
}

function selectedPanel(agent, model, ctx) {
  const { esc, t, icon, avatar, status } = ctx;
  if (!agent) return `<p class="hcc-empty-text">${t('Select a node to inspect it.', 'Выберите узел для просмотра.')}</p>`;
  const activity = model.events.find(event => event.from === agent.id || event.to === agent.id);
  const task = currentTask(agent, model.tasks);
  return `<div class="hcc-selected-agent">
    <div class="hcc-selected-summary">${avatar(agent, 48)}<div><strong>${esc(agent.name)}</strong><small>${esc(agent.role || agent.kind)}${agent.profile ? ` · ${esc(agent.profile)}` : ''}</small></div></div>
    <dl class="hcc-selected-meta">
      <div><dt>${model.route ? t('Recorded status', 'Статус в записи') : t('Status', 'Статус')}</dt><dd>${status(agent.status)}</dd></div>
      <div><dt>${t('Model', 'Модель')}</dt><dd>${esc(agent.model || '—')}</dd></div>
      <div><dt>${model.route ? t('Recorded task', 'Задача в записи') : t('Current task', 'Текущая задача')}</dt><dd>${esc(task || '—')}</dd></div>
      <div><dt>${t('Last action', 'Последнее действие')}</dt><dd>${esc(agent.lastAction || activity?.title || '—')}</dd></div>
      <div><dt>${model.route ? t('Recorded at', 'Время записи') : t('Last activity', 'Последняя активность')}</dt><dd>${model.route ? graphTime(agent.updatedAt || agent.startedAt, ctx) || '—' : esc(ctx.rel(agent.updatedAt || activity?.at))}</dd></div>
    </dl>
    <button type="button" class="hcc-quiet-button" data-graph-detail>${t('Inspect agent', 'Сведения об агенте')} ${icon('arrow', 14)}</button>
  </div>`;
}

function currentTask(agent, tasks) {
  return agent.task || tasks.find(task =>
    (agent.session && task.session === agent.session ||
      task.assignee === agent.id ||
      (agent.kind === 'profile' && agent.profile && task.assignee === agent.profile)) &&
    ['running', 'in_progress', 'blocked', 'review'].includes(task.status)
  )?.title;
}

function graphNavigation(ctx, mode, counts) {
  const {t, fmt, icon} = ctx;
  const f = ctx.filters('graph');
  return `<div class="hcc-graph-nav">
    <div class="hcc-segments" role="group" aria-label="${t('Graph mode','Режим графа')}">
      <button type="button" id="hcc-graph-mode-live" data-graph-mode="live" aria-pressed="${mode === 'live'}">${icon('graph', 16)}${t('Live topology','Живая карта')}</button>
      ${piRouteModes.filter(option => ctx.data[option.dataKey]?.nodes?.length).map(option => `<button type="button" id="hcc-graph-mode-${option.mode}" data-graph-mode="${option.mode}" aria-pressed="${mode === option.mode}">${icon('activity', 16)}${t(...option.label)}</button>`).join('')}
      <button type="button" id="hcc-graph-mode-history" data-graph-mode="history" aria-pressed="${mode === 'history'}">${icon('clock', 16)}${t('Run history','История запусков')} <small>${fmt(ctx.data.runs.length)}</small></button>
    </div>
    ${mode === 'live' ? `<div class="hcc-graph-counters">
      <button type="button" data-graph-kind="profile" aria-pressed="${f.kind === 'profile'}"><strong>${fmt(counts.profiles)}</strong> ${t('Profiles','Профили')}</button>
      <button type="button" data-graph-kind="executor" aria-pressed="${f.kind === 'executor'}"><strong>${fmt(counts.executors)}</strong> ${t('Active executors','Активные исполнители')}</button>
      <button type="button" data-graph-issues><strong>${fmt(ctx.data.issues.length)}</strong> ${t('Needs attention','Требуют внимания')}</button>
    </div>` : ''}
  </div>`;
}

function historyModel(ctx) {
  const f = ctx.filters('graph');
  const query = String(f.historyQuery || '').toLocaleLowerCase();
  const rows = ctx.data.runs.filter(row => !query || [row.name, row.task, row.profile, row.session, row.status, row.model]
    .some(value => String(value || '').toLocaleLowerCase().includes(query)));
  const page = Math.max(0, Math.min(Number(f.historyPage) || 0, Math.ceil(rows.length / 25) - 1));
  return {rows, page, pageRows: rows.slice(page * 25, (page + 1) * 25)};
}

function renderHistory(ctx) {
  const {t, esc, icon, status, rel, avatar} = ctx;
  const f = ctx.filters('graph');
  const model = historyModel(ctx);
  return `${graphNavigation(ctx, 'history')}<section class="hcc-panel hcc-graph-history">
    <header class="hcc-panel-title"><h2>${icon('clock', 16)}${t('Historical runs and sessions','Завершённые запуски и сессии')}</h2><span class="hcc-count">${ctx.fmt(model.rows.length)}</span></header>
    <div class="hcc-graph-history-search"><label for="hcc-history-search">${t('Search history','Поиск по истории')}</label><input id="hcc-history-search" type="search" value="${esc(f.historyQuery)}" placeholder="${t('Session, profile, model, task','Сессия, профиль, модель, задача')}"></div>
    ${model.pageRows.length ? model.pageRows.map((run, index) => `<button type="button" class="hcc-history-run" data-graph-run="${index}">
      ${avatar(run, 30)}<span><strong>${esc(run.task || run.name)}</strong><small>${esc([run.profile, run.model, run.session].filter(Boolean).join(' · '))}</small></span>
      ${status(run.status)}<span class="hcc-history-time">${esc(rel(run.updatedAt))}</span>${icon('arrow', 15)}
    </button>`).join('') : `<p class="hcc-empty">${t('No archived runs match the current search.','По текущему запросу в истории ничего не найдено.')}</p>`}
    <footer><button type="button" class="hcc-quiet-button" data-history-page="-1" ${model.page === 0 ? 'disabled' : ''}>${t('Previous','Назад')}</button><span>${model.rows.length ? model.page * 25 + 1 : 0}–${Math.min((model.page + 1) * 25, model.rows.length)} / ${model.rows.length}</span><button type="button" class="hcc-quiet-button" data-history-page="1" ${(model.page + 1) * 25 >= model.rows.length ? 'disabled' : ''}>${t('Next','Дальше')}</button></footer>
  </section>`;
}

function graphMessageCards(model, coords, ctx) {
  const newest = new Set();
  return model.events.filter(event => {
    if (!event.body || !coords.has(event.from) || !coords.has(event.to) || event.from === event.to || newest.has(event.to)) return false;
    const age = Date.now() - timestamp(event.at);
    if (!Number.isFinite(age) || age < 0 || age > 300000) return false;
    newest.add(event.to);
    return true;
  }).slice(0, 3).map(event => {
    const target = coords.get(event.to);
    const x = Math.max(109, Math.min(890, target.x + 118));
    const y = Math.max(44, Math.min(480, target.y - 72));
    return `<button type="button" class="hcc-map-message" style="left:${x}px;top:${y}px" data-graph-event="${model.events.indexOf(event)}">
      <span>${ctx.icon('communications', 13)} ${ctx.esc(event.title)} <small>${ctx.esc(ctx.rel(event.at))}</small></span>
      <strong>${ctx.esc(event.body)}</strong>
    </button>`;
  }).join('');
}

export function renderGraph(ctx) {
  const { esc, t, icon, avatar, rel, status } = ctx;
  const f = ctx.filters('graph');
  if (f.mode === 'history') return renderHistory(ctx);
  const model = graphModel(ctx);
  const graphView = model.route ? f.routeViews?.[f.mode] : f;
  const coords = new Map(placeAgents(model.shown, model.edges).map((pos, i) => [model.shown[i].id, pos]));
  const active = model.agents.filter(isExecutor).length;
  const profiles = ctx.data.agents.filter(agent => agent.kind === 'profile').length;
  const names = new Map(model.shown.map(agent => [agent.id, agent.name]));
  const heading = (name, glyph, right = '') => `<header class="hcc-panel-title"><h2>${icon(glyph, 15)}${name}</h2>${right}</header>`;
  const tool = (action, glyph, en, ru) => `<button type="button" class="hcc-icon" data-graph-action="${action}" aria-label="${t(en, ru)}" title="${t(en, ru)}">${icon(glyph, 15)}</button>`;
  const legend = (kind, en, ru) => `<span class="kind-${kind}"><i></i>${t(en, ru)}</span>`;
  return `
    ${graphNavigation(ctx, model.routeMode?.mode || 'live', {profiles, executors: ctx.data.agents.filter(isExecutor).length})}
    ${model.route ? '' : `<div class="hcc-graph-tools">
      <span>${icon('filter', 15)}${t('Scope', 'Фильтры')}</span>
      ${filterSelect('project', 'All projects', 'Все проекты', values(model.entities, 'project'), ctx)}
      ${filterSelect('team', 'All teams', 'Все команды', values(model.entities, 'team'), ctx)}
      ${filterSelect('session', 'All sessions', 'Все сессии', values(model.entities, 'session'), ctx)}
      <div class="hcc-legend">
        ${legend('observed', 'Observed relationship', 'Подтверждённая связь')}
        ${legend('delegation', 'Delegation', 'Делегирование')}
        ${legend('message', 'Message', 'Сообщение')}
        ${model.edges.some(edge => edge.kind === 'integration') ? legend('integration', 'Verified integration', 'Проверенная интеграция') : ''}
        ${model.edges.some(edge => edge.kind === 'configured') ? legend('configured', 'Configured role', 'Роль из конфигурации') : ''}
      </div>
    </div>`}
    <div class="hcc-graph-layout" style="--side-width:${Number(graphView?.sideWidth) || 288}px">
      <section class="hcc-panel hcc-graph-card${model.route ? ' is-historical' : ''}">
        ${model.route ? heading(t(...(model.routeMode.heading || model.routeMode.label)), 'clock',
          `<span class="hcc-graph-subline hcc-route-time">${t('Historical', 'Архивная запись')} ${graphTime(model.route.at, ctx, true)}</span>`)
          : heading(t('Agent topology', 'Топология агентов'), 'graph', `<span class="hcc-graph-subline hcc-live${active ? ' has-active' : ''}"><i></i><span data-graph-active-count="${active}">${ctx.fmt(active)} ${t('active', 'активно')}</span> · ${ctx.fmt(model.agents.length)} ${t('total', 'всего')}</span>`)}
        <div class="hcc-graph-stage" tabindex="0" aria-label="${model.route
          ? t('Historical route. Use arrow keys to pan.', 'Архивный маршрут. Стрелки перемещают граф.')
          : t('Interactive topology. Use arrow keys to pan.', 'Интерактивная топология. Стрелки перемещают граф.')}">
          ${model.route && model.edges.length ? `<ul class="hcc-route-links" aria-label="${t('Recorded links', 'Связи в записи')}">${model.edges.map(edge => `<li>${esc(names.get(edge.from))} ${t('to', '→')} ${esc(names.get(edge.to))}</li>`).join('')}</ul>` : ''}
          <div class="hcc-world">
            ${mapSvg(model, coords)}
            ${model.shown.map((agent, index) => {
              const pos = coords.get(agent.id);
              const task = currentTask(agent, model.tasks);
              const label = String(agent.status || '').replace(/[^a-z_]/g, '');
              const description = model.route ? statusLabel(agent.status, t)
                : agent.kind === 'pi-role' ? t('Configured role', 'Роль из конфигурации')
                : agent.role || agent.kind || '—';
              return `<button type="button" class="hcc-node ${index === 0 ? 'is-core' : ''} ${agent.id === model.chosen?.id ? 'is-selected' : ''}" style="left:${pos.x}px;top:${pos.y}px" data-graph-agent="${esc(agent.id)}" data-graph-node-kind="${esc(agent.kind)}" aria-pressed="${agent.id === model.chosen?.id}">
                ${avatar(agent, index === 0 ? 72 : 50)}<span class="node-status state-${esc(label)}"></span>
                <span class="node-label"><strong>${esc(agent.name)}</strong><small>${esc(description)}${!model.route && !ctx.demo && agent.kind !== 'profile' && !isTopology(agent) && agent.profile ? ` · ${esc(agent.profile)}` : ''}</small></span>
                ${task ? `<span class="node-task">${esc(task)}</span>` : ''}
              </button>`;
            }).join('')}
            ${graphMessageCards(model, coords, ctx)}
          </div>
          ${model.agents.length ? '' : `<div class="hcc-stage-empty">${icon('graph', 33)}<strong>${ctx.loading ? t('Reading the agent inventory','Получаем список исполнителей') : t('No agents in this view', 'В этом представлении нет агентов')}</strong><span>${ctx.loading ? t('Waiting for the authenticated WebUI proxy.','Ожидаем ответ серверного прокси WebUI.') : t('Check source availability or clear the filters.', 'Проверьте доступность источников или сбросьте фильтры.')}</span></div>`}
          ${model.agents.length > model.shown.length ? `<span class="hcc-stage-note">${model.shown.length} / ${model.agents.length} ${t('shown', 'показано')}</span>` : ''}
          <div class="hcc-stage-tools">${tool('out','minus','Zoom out','Уменьшить')}<output aria-live="off">100%</output>${tool('in','plus','Zoom in','Увеличить')}${tool('fit','fit','Fit topology','Вписать граф')}${tool('full','expand','Fullscreen','Полноэкранный режим')}</div>
        </div>
      </section>
      <div class="hcc-resizer" role="separator" aria-orientation="vertical" aria-label="${t('Resize inspector', 'Изменить ширину панели')}" aria-valuemin="220" aria-valuemax="440" aria-valuenow="${Number(graphView?.sideWidth) || 288}" tabindex="0"></div>
      <aside class="hcc-graph-side">
        <section class="hcc-panel">${heading(t('Agent inspector', 'Карточка агента'), 'activity')}${selectedPanel(model.chosen, model, ctx)}</section>
        <section class="hcc-panel">${heading(model.route ? t('Observed participants', 'Участники маршрута') : t('Agents in scope', 'Агенты в выборке'), 'team', `<span class="hcc-count">${model.agents.length}</span>`)}
          ${model.agents.length ? `<div class="hcc-agent-rows">${model.agents.map(agent => `<button type="button" class="hcc-agent-row" data-graph-agent="${esc(agent.id)}">${avatar(agent, 25)}<span><strong>${esc(agent.name)}</strong><small>${esc(agent.role || agent.kind || '—')}</small></span>${status(agent.status)}</button>`).join('')}</div>` : `<p class="hcc-empty-text">${t('No matching agents.', 'Подходящих агентов нет.')}</p>`}
        </section>
      </aside>
    </div>
    ${model.route ? '' : `<section class="hcc-panel hcc-recent">
      ${heading(t('Recent handoffs', 'Недавние передачи'), 'activity', `<button type="button" class="hcc-quiet-button" data-graph-activity>${t('Open feed', 'Открыть ленту')} ${icon('arrow', 13)}</button>`)}
      ${model.events.length ? `<div class="hcc-event-list">${model.events.slice(0, 3).map((event, i) => `<button type="button" data-graph-event="${i}">
        <span class="hcc-event-icon">${icon(eventIcons[event.kind] || 'activity', 15)}</span>
        <span><strong>${esc(event.title || event.kind)}</strong><small>${esc(rel(event.at))}${event.body ? ` · ${esc(event.body)}` : ''}</small></span>
      </button>`).join('')}</div>` : `<p class="hcc-empty-text">${t('No events from the current sources and filters.', 'В текущих источниках и фильтрах нет событий.')}</p>`}
    </section>`}`;
}

export function bindGraph(ctx) {
  const container = ctx.root.querySelector('.hcc-content');
  const filters = ctx.filters('graph');
  const { esc, t, detail } = ctx;
  container.querySelectorAll('[data-graph-mode]').forEach(button => {
    button.onclick = () => { filters.mode = button.dataset.graphMode; ctx.update(); };
  });
  if (filters.mode === 'history') {
    const history = historyModel(ctx);
    const search = container.querySelector('#hcc-history-search');
    const updateSearch = event => {
      if (event.isComposing) return;
      filters.historyQuery = search.value;
      filters.historyPage = 0;
      ctx.update();
    };
    search.oninput = updateSearch;
    search.oncompositionend = updateSearch;
    container.querySelectorAll('[data-history-page]').forEach(button => {
      button.onclick = () => { filters.historyPage = history.page + Number(button.dataset.historyPage); ctx.update(); };
    });
    container.querySelectorAll('[data-graph-run]').forEach(button => {
      button.onclick = () => {
        const run = history.pageRows[Number(button.dataset.graphRun)];
        const href = ctx.safeHref(run.href);
        detail(run.task || run.name, `<dl><dt>${t('Profile','Профиль')}</dt><dd>${esc(run.profile || '—')}</dd>
          <dt>${t('Session','Сессия')}</dt><dd>${esc(run.session || '—')}</dd><dt>${t('Status','Статус')}</dt><dd>${ctx.status(run.status)}</dd>
          <dt>${t('Last activity','Последнее действие')}</dt><dd>${esc(ctx.rel(run.updatedAt))}</dd></dl>
          ${href ? `<a href="${esc(href)}" target="_blank" rel="noopener noreferrer">${t('Open native session','Открыть сессию')}</a>` : ''}`);
      };
    });
    return;
  }
  const model = graphModel(ctx);
  const f = model.route ? ((filters.routeViews ||= {})[filters.mode] ||= {}) : filters;
  container.querySelectorAll('[data-graph-kind]').forEach(button => {
    button.onclick = () => { f.kind = f.kind === button.dataset.graphKind ? '' : button.dataset.graphKind; delete f.zoom; ctx.update(); };
  });
  const issues = container.querySelector('[data-graph-issues]');
  if (issues) issues.onclick = () => {
    ctx.filters('team').teamScope = 'issues';
    ctx.navigate('team');
  };
  const stage = container.querySelector('.hcc-graph-stage');
  const graphCard = container.querySelector('.hcc-graph-card');
  const layout = container.querySelector('.hcc-graph-layout');
  const separator = container.querySelector('.hcc-resizer');
  const world = stage.querySelector('.hcc-world');
  let zoom = f.zoom || 1;
  let x = f.x || 0;
  let y = f.y || 0;
  let drag;
  let resize;
  const pointers = new Map();
  let pinch;

  function paint() {
    world.style.transform = `translate(${x}px, ${y}px) scale(${zoom})`;
    stage.querySelector('output').textContent = `${Math.round(zoom * 100)}%`;
  }
  function remember() { Object.assign(f, { zoom, x, y }); }
  function fit() {
    zoom = Math.min(stage.clientWidth / WIDTH, stage.clientHeight / HEIGHT, 1.5);
    x = (stage.clientWidth - WIDTH * zoom) / 2;
    y = (stage.clientHeight - HEIGHT * zoom) / 2;
    paint();
  }
  function zoomAt(scale, cx, cy) {
    const next = Math.max(.2, Math.min(3, scale));
    x = cx - (cx - x) * next / zoom;
    y = cy - (cy - y) * next / zoom;
    zoom = next;
    remember();
    paint();
  }
  if (f.zoom) paint(); else fit();
  const observer = new ResizeObserver(() => { if (!f.zoom) fit(); });
  observer.observe(stage);
  for (const packet of stage.querySelectorAll('[data-packet-index]')) animatedEvents.add(model.events[Number(packet.dataset.packetIndex)].id);
  if (animatedEvents.size > 1500) {
    for (const eventId of [...animatedEvents].slice(0, 500)) animatedEvents.delete(eventId);
  }

  container.querySelectorAll('[data-graph-filter]').forEach(select => {
    select.onchange = () => {
      f[select.dataset.graphFilter] = select.value;
      delete f.zoom;
      ctx.update();
    };
  });
  container.querySelectorAll('[data-graph-agent]').forEach(button => {
    button.onclick = () => {
      f.selected = button.dataset.graphAgent;
      ctx.update();
      const updated = [...ctx.root.querySelectorAll('[data-graph-agent]')].find(node => node.dataset.graphAgent === f.selected);
      updated?.focus({ preventScroll: true });
    };
  });
  const inspect = container.querySelector('[data-graph-detail]');
  if (inspect && model.chosen) inspect.onclick = () => {
    const agent = model.chosen;
    const href = ctx.safeHref(agent.href);
    const related = model.events.filter(event => event.from === agent.id || event.to === agent.id).slice(0, 8);
    detail(agent.name, `
      <dl><dt>${t('Role', 'Роль')}</dt><dd>${esc(agent.role || agent.kind || '—')}</dd>
      <dt>${t('Profile', 'Профиль')}</dt><dd>${esc(agent.profile || '—')}</dd>
      <dt>${t('Model', 'Модель')}</dt><dd>${esc(agent.model || '—')}</dd>
      <dt>${model.route ? t('Recorded status', 'Статус в записи') : t('Status', 'Статус')}</dt><dd>${ctx.status(agent.status)}</dd>
      ${model.route ? `<dt>${t('Recorded at', 'Время записи')}</dt><dd>${graphTime(agent.updatedAt || agent.startedAt, ctx) || '—'}</dd>` : ''}
      <dt>${t('Session', 'Сессия')}</dt><dd>${esc(agent.session || '—')}</dd></dl>
      ${agent.detail ? `<p>${esc(agent.detail)}</p>` : ''}
      ${href ? `<a href="${esc(href)}" target="_blank" rel="noopener noreferrer">${t('Open native session', 'Открыть сессию')}</a>` : ''}
      ${model.route ? '' : `<h3>${t('Recent activity', 'Недавняя активность')}</h3>
      ${related.length ? related.map(event => `<div class="hcc-source-row"><strong>${esc(event.title || event.kind)}</strong><small>${esc(ctx.rel(event.at))}</small></div>`).join('') : `<p class="hcc-muted">${t('No recent events.', 'Недавних событий нет.')}</p>`}`}`);
  };
  const activity = container.querySelector('[data-graph-activity]');
  if (activity) activity.onclick = () => ctx.navigate('communications');
  container.querySelectorAll('[data-graph-event]').forEach(button => {
    button.onclick = () => {
      const event = model.events[Number(button.dataset.graphEvent)];
      detail(event.title || event.kind, `<dl><dt>${t('From', 'От')}</dt><dd>${esc(model.agents.find(agent => agent.id === event.from)?.name || event.from || '—')}</dd><dt>${t('To', 'Кому')}</dt><dd>${esc(model.agents.find(agent => agent.id === event.to)?.name || event.to || '—')}</dd><dt>${t('Time', 'Время')}</dt><dd>${graphTime(event.at, ctx) || '—'}</dd></dl>${event.body || event.detail ? `<p class="hcc-message-body">${esc(event.body || event.detail)}</p>` : ''}`);
    };
  });
  stage.querySelectorAll('[data-graph-action]').forEach(button => {
    button.onclick = async () => {
      switch (button.dataset.graphAction) {
        case 'fit': delete f.zoom; fit(); break;
        case 'in': zoomAt(zoom * 1.22, stage.clientWidth / 2, stage.clientHeight / 2); break;
        case 'out': zoomAt(zoom / 1.22, stage.clientWidth / 2, stage.clientHeight / 2); break;
        case 'full':
          try {
            if (document.fullscreenElement) await document.exitFullscreen();
            else await graphCard.requestFullscreen();
          } catch {
            detail(t('Fullscreen', 'Полноэкранный режим'), `<p>${t('Fullscreen is unavailable in this browser context.', 'Полноэкранный режим недоступен в этом контексте браузера.')}</p>`);
          }
          delete f.zoom;
          requestAnimationFrame(fit);
          break;
      }
    };
  });
  stage.onwheel = event => {
    if (event.ctrlKey || event.target.closest('.hcc-stage-tools')) return;
    event.preventDefault();
    const box = stage.getBoundingClientRect();
    zoomAt(zoom * Math.exp(-event.deltaY * .0019), event.clientX - box.left, event.clientY - box.top);
  };
  stage.onkeydown = event => {
    const directions = { ArrowLeft: [28, 0], ArrowRight: [-28, 0], ArrowUp: [0, 28], ArrowDown: [0, -28] };
    if (event.target !== stage || !Object.hasOwn(directions, event.key)) return;
    event.preventDefault();
    const [dx, dy] = directions[event.key];
    x += dx;
    y += dy;
    remember();
    paint();
  };
  stage.onpointerdown = event => {
    if (event.button !== 0 || event.target.closest('button,.hcc-stage-tools')) return;
    pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    stage.setPointerCapture(event.pointerId);
    stage.classList.add('is-panning');
    if (pointers.size === 1) drag = { px: event.clientX, py: event.clientY, x, y };
  };
  stage.onpointermove = event => {
    if (!pointers.has(event.pointerId)) return;
    pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (pointers.size >= 2) {
      const [a, b] = pointers.values();
      const distance = Math.hypot(a.x - b.x, a.y - b.y);
      const box = stage.getBoundingClientRect();
      if (pinch && distance) zoomAt(zoom * distance / pinch, (a.x + b.x) / 2 - box.left, (a.y + b.y) / 2 - box.top);
      pinch = distance;
    } else if (drag) {
      x = drag.x + event.clientX - drag.px;
      y = drag.y + event.clientY - drag.py;
      remember();
      paint();
    }
  };
  stage.onpointerup = stage.onpointercancel = event => {
    pointers.delete(event.pointerId);
    pinch = null;
    const remaining = pointers.values().next().value;
    drag = remaining ? { px: remaining.x, py: remaining.y, x, y } : null;
    if (!remaining) stage.classList.remove('is-panning');
  };

  function resizeTo(width) {
    f.sideWidth = Math.max(220, Math.min(440, width));
    layout.style.setProperty('--side-width', `${f.sideWidth}px`);
    separator.setAttribute('aria-valuenow', f.sideWidth);
  }
  separator.onpointerdown = event => {
    resize = { x: event.clientX, width: f.sideWidth || 288 };
    separator.setPointerCapture(event.pointerId);
  };
  separator.onpointermove = event => { if (resize) resizeTo(resize.width + resize.x - event.clientX); };
  separator.onpointerup = separator.onpointercancel = () => { resize = null; };
  separator.onkeydown = event => {
    if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
    event.preventDefault();
    resizeTo((f.sideWidth || 288) + (event.key === 'ArrowLeft' ? 20 : -20));
  };
  return () => observer.disconnect();
}
