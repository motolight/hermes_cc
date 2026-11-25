const list = (value) => Array.isArray(value) ? value : [];
const valueOf = (value) => typeof value === 'number' && Number.isFinite(value) ? value : null;
const timestamp = (value) => value == null || value === '' ? NaN : new Date(value).getTime();
const latest = (rows, key) => [...rows].sort((a, b) => (timestamp(b[key]) || 0) - (timestamp(a[key]) || 0));
const states = {
  running: ['Running', 'Работает'], active: ['Active', 'Активен'], busy: ['Busy', 'Занят'],
  waiting: ['Waiting', 'Ожидает'], queued: ['Queued', 'В очереди'], pending: ['Pending', 'Ожидает'],
  blocked: ['Blocked', 'Заблокировано'], failed: ['Failed', 'Ошибка'], error: ['Error', 'Ошибка'],
  done: ['Done', 'Готово'], completed: ['Completed', 'Завершено'], succeeded: ['Succeeded', 'Успешно'],
  idle: ['Idle', 'Свободен'], offline: ['Offline', 'Не в сети'], unavailable: ['Unavailable', 'Недоступно'],
  ready: ['Ready', 'Готов'], online: ['Online', 'В сети'], ok: ['Available', 'Доступно'],
  connected: ['Connected', 'Подключено'], unknown: ['Unknown', 'Неизвестно'],
  configured: ['Configured', 'Настроена'], observed: ['Observed', 'Наблюдалось'],
  todo: ['To do', 'К выполнению'], backlog: ['Backlog', 'Бэклог'], in_progress: ['In progress', 'В работе'],
  cancelled: ['Cancelled', 'Отменено'], canceled: ['Canceled', 'Отменено'],
  working: ['Working', 'В работе'], generating: ['Generating', 'Генерация'], tool: ['Using tool', 'Использует инструмент'],
  stalled: ['Possibly stalled', 'Возможное зависание'], ended: ['Ended', 'Завершено'], paused: ['Paused', 'Приостановлено'],
  interrupted: ['Interrupted', 'Прервано'], review: ['In review', 'На проверке'], complete: ['Completed', 'Завершено'],
  triage: ['Triage', 'На разборе'], scheduled: ['Scheduled', 'Запланировано'], archived: ['Archived', 'В архиве'],
};
const kinds = {
  message: ['Message', 'Сообщение'], delegation: ['Delegation', 'Делегирование'], delegate: ['Delegation', 'Делегирование'],
  handoff: ['Handoff', 'Передача задачи'], status: ['Status change', 'Изменение статуса'], task: ['Task', 'Задача'],
  event: ['Event', 'Событие'], start: ['Started', 'Запуск'], finish: ['Finished', 'Завершение'], result: ['Result', 'Результат'],
  error: ['Error', 'Ошибка'], context: ['Context', 'Контекст'], artifact: ['Artifact', 'Артефакт'],
  log: ['Log entry', 'Запись журнала'], stream: ['Session event', 'Событие сессии'], agent: ['Agent', 'Агент'],
  main: ['Coordinator', 'Координатор'], coordinator: ['Coordinator', 'Координатор'],
  worker: ['Worker', 'Исполнитель'], subagent: ['Subagent', 'Субагент'],
  profile: ['Profile', 'Профиль'], session: ['Session', 'Сессия'], execution: ['Execution', 'Выполнение'],
  executor: ['Executor', 'Исполнитель'], pi: ['Pi Bridge', 'Pi Bridge'], feedback: ['Feedback', 'Обратная связь'],
  pi_runtime: ['Pi runtime', 'Среда Pi'], pi_role: ['Open Agents role', 'Роль Open Agents'],
  tool: ['Tool event', 'Событие инструмента'], tool_call: ['Tool call', 'Вызов инструмента'],
  tool_result: ['Tool result', 'Результат инструмента'],
  approval: ['Approval', 'Согласование'],
};

function label(ctx, value, dictionary = states) {
  const key = String(value || '').toLowerCase().replace(/[ -]/g, '_');
  return Object.hasOwn(dictionary, key) ? ctx.t(...dictionary[key]) : (value || ctx.t('Unknown', 'Неизвестно'));
}

function num(ctx, value, unit = '', digits = 0) {
  if (valueOf(value) === null) return '—';
  const options = {maximumFractionDigits: digits};
  const formatted = ctx.fmt ? ctx.fmt(value, options) : new Intl.NumberFormat(ctx.t('en-US', 'ru-RU'), options).format(value);
  return `${formatted}${unit ? ` ${unit}` : ''}`;
}

function shortDate(ctx, value, withDate = false) {
  if (!Number.isFinite(timestamp(value))) return '—';
  const options = {hour: '2-digit', minute: '2-digit'};
  if (withDate) Object.assign(options, {month: 'short', day: 'numeric'});
  return new Intl.DateTimeFormat(ctx.t('en-US', 'ru-RU'), options).format(new Date(value));
}

function dateLabel(ctx, value) {
  return Number.isFinite(timestamp(value))
    ? new Intl.DateTimeFormat(ctx.t('en-US', 'ru-RU'), {weekday: 'long', month: 'long', day: 'numeric'}).format(new Date(value))
    : ctx.t('Time unavailable', 'Время неизвестно');
}

function display(ctx, value) {
  return value == null || value === '' ? '—' : ctx.esc(String(value));
}

function safeLink(value) {
  if (typeof value !== 'string') return '';
  const href = value.trim();
  if (/[\u0000-\u001f\u007f\\]/.test(href)) return '';
  try {
    if (/^\/(?![/\\])/.test(href)) {
      const local = 'https://local.invalid';
      const parsed = new URL(href, local);
      return parsed.origin === local && !parsed.username && !parsed.password ? href : '';
    }
    const parsed = new URL(href);
    return ['http:', 'https:'].includes(parsed.protocol) && !parsed.username && !parsed.password ? parsed.href : '';
  } catch {
    return '';
  }
}

function link(ctx, value, text) {
  const href = safeLink(value);
  return href ? `<a class="hv-link" href="${ctx.esc(href)}" target="_blank" rel="noopener noreferrer">${ctx.esc(text)} <span aria-hidden="true">↗</span></a>` : '';
}

function textBlock(ctx, value) {
  if (value == null || value === '') return '';
  const text = typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value);
  return `<p class="hv-detail-copy">${ctx.esc(text)}</p>`;
}

function status(ctx, value) {
  return ctx.status(value || 'unknown');
}

function findAgent(ctx, id) {
  return [...list(ctx.data.agents), ...list(ctx.data.runs)].find((agent) => agent.id === id || agent.name === id);
}

function agentName(ctx, value) {
  return findAgent(ctx, value)?.name || value || '';
}

function empty(ctx, title, description) {
  return `<div class="hv-empty"><span class="hv-empty-icon" aria-hidden="true">◇</span><strong>${ctx.esc(title)}</strong><p>${ctx.esc(description)}</p></div>`;
}

function stat(ctx, name, number, note, tone = '', choice = null) {
  const tag = choice ? 'button' : 'div';
  const attrs = choice ? ` type="button" id="hv-${choice.key}-${choice.value}" data-hv-choice="${choice.key}" data-hv-value="${choice.value}" aria-pressed="${choice.selected}"` : '';
  return `<${tag} class="hv-stat ${tone ? `hv-tone-${tone}` : ''}"${attrs}><span>${ctx.esc(name)}</span><strong>${ctx.esc(number)}</strong><small>${ctx.esc(note)}</small></${tag}>`;
}

function toggle(ctx, key, title, checked, value = '') {
  const id = value ? `hv-${key}-${value}` : `hv-${key}`;
  return `<label class="hv-toggle"><input type="checkbox" id="${ctx.esc(id)}" data-hv-toggle="${key}" value="${ctx.esc(value)}"${checked ? ' checked' : ''}><span>${ctx.esc(title)}</span></label>`;
}

function segment(ctx, key, current, options, title) {
  return `<div class="hv-segment" role="group" aria-label="${ctx.esc(title)}">${options.map(([value, name]) =>
    `<button type="button" id="hv-${key}-${value}" data-hv-choice="${key}" data-hv-value="${value}" aria-pressed="${current === value}">${ctx.esc(name)}</button>`).join('')}</div>`;
}

function panel(ctx, title, body, detail = '') {
  return `<section class="hv-panel"><header class="hv-panel-head"><h2>${ctx.esc(title)}</h2>${detail ? `<span>${ctx.esc(detail)}</span>` : ''}</header>${body}</section>`;
}

function tags(ctx, values) {
  return values.filter((value) => value != null && value !== '').map((value) => `<span class="hv-tag">${ctx.esc(String(value))}</span>`).join('');
}

function optionsFor(rows, key, format = (value) => value) {
  return [...new Set(rows.map((row) => row[key]).filter((value) => value != null && value !== ''))]
    .map((value) => [String(value), format(value)])
    .sort((a, b) => String(a[1]).localeCompare(String(b[1])));
}

function select(ctx, filter, key, name, options, all = ctx.t('All', 'Все')) {
  const rows = filter[key] && !options.some(([value]) => value === filter[key])
    ? [[filter[key], filter[key]], ...options] : options;
  return `<label class="hv-select"><span>${ctx.esc(name)}</span><select id="hv-filter-${key}" data-hv-filter="${key}" aria-label="${ctx.esc(name)}">${all ? `<option value="">${ctx.esc(all)}</option>` : ''}${rows.map(([value, title]) => `<option value="${ctx.esc(value)}" ${filter[key] === value ? 'selected' : ''}>${ctx.esc(title)}</option>`).join('')}</select></label>`;
}

function toolbar(ctx, filter, placeholder, controls) {
  return `<div class="hv-toolbar"><label class="hv-search"><span aria-hidden="true">${ctx.icon?.('search', 17) || '⌕'}</span><input type="search" id="hv-filter-query" data-hv-filter="query" value="${ctx.esc(filter.query || '')}" placeholder="${ctx.esc(placeholder)}" aria-label="${ctx.esc(placeholder)}"></label>${controls.join('')}</div>`;
}

function match(row, filter, keys, searchable) {
  const query = (filter.query || '').trim().toLocaleLowerCase();
  return keys.every((key) => !filter[key] || String(row[key]) === filter[key])
    && (!query || searchable.some((value) => String(value ?? '').toLocaleLowerCase().includes(query)));
}

function dataEmpty(ctx, rows, title, explanation) {
  if (rows.length) return empty(ctx, ctx.t('No matching results', 'Ничего не найдено'), ctx.t('Adjust the filters or search terms.', 'Измените фильтры или поисковый запрос.'));
  return empty(ctx, title, explanation);
}

function fields(ctx, pairs) {
  return `<dl class="hv-fields">${pairs.map(([name, value]) => `<div${typeof value === 'string' && value.length > 100 ? ' class="hv-field-wide"' : ''}><dt>${ctx.esc(name)}</dt><dd>${display(ctx, value)}</dd></div>`).join('')}</dl>`;
}

function section(ctx, title, body) {
  return body ? `<section class="hv-detail-section"><h3>${ctx.esc(title)}</h3>${body}</section>` : '';
}

function inspectButton(ctx, key, position, title) {
  return `<button type="button" class="hv-inline-action" data-hv-inspect="${key}:${position}">${display(ctx, title)} <span aria-hidden="true">↗</span></button>`;
}

function observedDuration(ctx, agent, live) {
  const start = timestamp(agent.startedAt);
  const end = timestamp(agent.endedAt || agent.completedAt) || (live ? Date.now() : timestamp(agent.updatedAt));
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return '—';
  const seconds = Math.floor((end - start) / 1000);
  const parts = [[Math.floor(seconds / 3600), ctx.t('h', 'ч')], [Math.floor(seconds / 60) % 60, ctx.t('m', 'мин')], [seconds % 60, ctx.t('s', 'с')]];
  return (seconds < 60 ? parts.slice(2) : parts.slice(0, 2).filter(([value]) => value)).map(([value, unit]) => `${value} ${unit}`).join(' ');
}

const activeStates = new Set(['running', 'active', 'busy', 'in_progress', 'working', 'generating', 'tool']);
const waitingStates = new Set(['waiting', 'queued', 'pending', 'blocked']);
const attentionStates = new Set(['blocked', 'stalled', 'interrupted', 'failed', 'error', 'offline']);

function isCurrentWork(agent) {
  return agent.active ?? (activeStates.has(agent.status) || waitingStates.has(agent.status) || agent.status === 'stalled');
}

function teamRecords(data) {
  const agents = list(data.agents);
  const installed = list(data.piAgents);
  const current = [...agents.filter((agent) => agent.kind === 'profile' || isCurrentWork(agent)), ...installed];
  const currentIds = new Set(current.map((agent) => agent.id));
  const history = [...list(data.runs), ...agents.filter((agent) => !currentIds.has(agent.id))];
  const issues = new Map(current.filter((agent) => agent.issue || attentionStates.has(agent.status)).map((agent) => [agent.id, agent]));
  for (const agent of list(data.issues)) {
    if (currentIds.has(agent.id) || agent.kind !== 'session') issues.set(agent.id, agent);
  }
  return {agents, installed, current, history, issues: [...issues.values()]};
}

function issueReason(ctx, agent) {
  return agent.issue?.reason || (typeof agent.issue === 'string' ? agent.issue : null)
    || (attentionStates.has(agent.status) ? label(ctx, agent.status) : null);
}

function agentDetails(ctx, agent) {
  return `${status(ctx, agent.status)}${fields(ctx, [
    [ctx.t('Kind', 'Тип'), label(ctx, agent.kind, kinds)], [ctx.t('Role', 'Роль'), agent.role],
    [ctx.t('Model', 'Модель'), agent.model], [ctx.t('Profile', 'Профиль'), agent.profile],
    [ctx.t('Task', 'Задача'), agent.task], [ctx.t('Project', 'Проект'), agent.project],
    [ctx.t('Team', 'Команда'), agent.team], [ctx.t('Session', 'Сессия'), agent.session],
    [ctx.t('Started', 'Запуск'), shortDate(ctx, agent.startedAt, true)], [ctx.t('Last activity', 'Последняя активность'), shortDate(ctx, agent.issue?.lastActivityAt || agent.lastActivityAt || agent.updatedAt, true)],
    [ctx.t('Elapsed', 'Длительность'), observedDuration(ctx, agent, isCurrentWork(agent))],
    [ctx.t('Last action', 'Последнее действие'), agent.lastAction],
    [ctx.t('Pi job', 'Задание Pi'), agent.jobId],
  ])}${section(ctx, ctx.t('Needs attention', 'Требует внимания'), issueReason(ctx, agent) ? fields(ctx, [
    [ctx.t('Reason', 'Причина'), issueReason(ctx, agent)], [ctx.t('Since', 'С какого времени'), shortDate(ctx, agent.issue?.at || agent.issue?.since, true)],
  ]) : '')}${textBlock(ctx, agent.detail)}${link(ctx, agent.href || agent.sessionHref, ctx.t('Open native session', 'Открыть сессию'))}`;
}

function teamView(ctx, filter) {
  const {agents, installed, current, history, issues} = teamRecords(ctx.data);
  const executors = current.filter((agent) => agent.kind !== 'profile' && isCurrentWork(agent));
  const currentIds = new Set(current.map((agent) => agent.id));
  const groups = {
    live: current,
    active: executors.filter((agent) => activeStates.has(agent.status)),
    waiting: executors.filter((agent) => waitingStates.has(agent.status)),
    issues,
    history,
  };
  const mode = filter.teamScope || 'live';
  const choices = [
    ['live', ctx.t('Team now', 'Команда сейчас'), ctx.t('Gateways, Pi roles and current work', 'Гейтвеи, роли Pi и текущая работа'), 'accent'],
    ['active', ctx.t('Active executors', 'Активные исполнители'), ctx.t('Working or running', 'Работают или выполняются'), 'green'],
    ['waiting', ctx.t('Waiting', 'Ожидают'), ctx.t('Queued, waiting or blocked', 'В очереди, ожидают или заблокированы'), 'amber'],
    ['issues', ctx.t('Needs attention', 'Требуют внимания'), ctx.t('Issues in the current team', 'Проблемы текущей команды'), 'red'],
    ['history', ctx.t('History', 'История'), ctx.t('Past executions and sessions', 'Прошлые запуски и сессии'), 'violet'],
  ];
  const all = groups[mode];
  const results = latest(all, 'updatedAt').filter((row) => match(row, filter, ['status', 'kind'], [
    row.name, row.id, row.role, row.task, row.project, row.team, row.model, row.profile, row.session, row.lastAction, issueReason(ctx, row),
  ]));
  if (mode === 'live') results.sort((a, b) => Number(b.kind === 'profile') - Number(a.kind === 'profile'));
  const limit = filter.teamLimit || 50;
  const rows = results.slice(0, limit).map((agent) => {
    const source = {history: ['runs', history], issues: ['issues', issues]}[mode] ||
      (installed.includes(agent) ? ['piAgents', installed] : ['agents', agents]);
    const [key, records] = source;
    const position = records.indexOf(agent);
    const lastActivity = shortDate(ctx, agent.issue?.lastActivityAt || agent.lastActivityAt || agent.updatedAt, true);
    const issue = issueReason(ctx, agent);
    const since = shortDate(ctx, agent.issue?.at || agent.issue?.since, true);
    return `<article class="hv-team-entry">
      <button type="button" class="hv-team-row" data-hv-inspect="${key}:${position}">
        <span class="hv-agent-head">${ctx.avatar(agent)}<span class="hv-agent-title"><strong>${display(ctx, agent.name || agent.id)}</strong><small>${display(ctx, agent.role || label(ctx, agent.kind, kinds))}</small></span></span>
        <span class="hv-team-work"><strong>${display(ctx, agent.task || agent.title)}</strong><small>${display(ctx, agent.lastAction || agent.activity)}</small></span>
        <span class="hv-team-model">${display(ctx, agent.model)}</span><span class="hv-team-duration">${ctx.esc(observedDuration(ctx, agent, currentIds.has(agent.id) && isCurrentWork(agent)))}</span>
        <time class="hv-team-time">${ctx.esc(lastActivity)}</time>${status(ctx, agent.status)}<span class="hv-team-arrow" aria-hidden="true">↗</span>
      </button>
      ${mode === 'issues' ? `<div class="hv-issue-line"><strong>${display(ctx, issue)}</strong><span>${ctx.esc(ctx.t('Since', 'С какого времени'))} · ${ctx.esc(since)}</span><span>${ctx.esc(ctx.t('Last activity', 'Последняя активность'))} · ${ctx.esc(lastActivity)}</span>${link(ctx, agent.href || agent.sessionHref, ctx.t('Open session', 'Открыть сессию')) || (agent.session ? `<span>${display(ctx, agent.session)}</span>` : '')}</div>` : ''}
    </article>`;
  }).join('');
  const heading = [ctx.t('Agent', 'Агент'), ctx.t('Task / last action', 'Задача и последнее действие'), ctx.t('Model', 'Модель'), ctx.t('Elapsed', 'Время'), ctx.t('Last active', 'Активность'), ctx.t('State', 'Состояние')];
  const table = `<div class="hv-team-table"><div class="hv-team-row hv-team-labels" aria-hidden="true">${heading.map((title) => `<span>${ctx.esc(title)}</span>`).join('')}</div>${rows}</div>`;
  return `<div class="hv-page hv-team"><div class="hv-stats hv-team-stats">
    ${choices.map(([value, name, note, tone]) => stat(ctx, name, groups[value].length, note, tone, {key: 'teamScope', value, selected: mode === value})).join('')}
  </div>${toolbar(ctx, filter, ctx.t('Search agents, tasks, models…', 'Поиск по агентам, задачам, моделям…'), [
    select(ctx, filter, 'status', ctx.t('Status', 'Статус'), optionsFor(all, 'status', (value) => label(ctx, value))),
    select(ctx, filter, 'kind', ctx.t('Kind', 'Тип'), optionsFor(all, 'kind', (value) => label(ctx, value, kinds))),
  ])}${panel(ctx, choices.find(([value]) => value === mode)[1], results.length ? table + (results.length > limit ? `<div class="hv-expand"><button type="button" class="hv-inline-action" data-hv-more="teamLimit">${ctx.esc(ctx.t('Show more', 'Показать ещё'))}</button></div>` : '') : dataEmpty(ctx, all, mode === 'issues' ? ctx.t('No current issues', 'Нет текущих проблем') : ctx.t('No records in this view', 'Нет записей в этом разделе'), ctx.t('Records appear here when connected sources report them.', 'Записи появятся, когда их передадут подключённые источники.')), `${Math.min(limit, results.length)} / ${results.length}`)}</div>`;
}

function eventDetails(ctx, event) {
  const events = list(ctx.data.events);
  const thread = event.thread || event.chainId;
  const related = thread ? latest(events.filter((row) => row !== event && (row.thread || row.chainId) === thread && communicationChannel(row) === communicationChannel(event)), 'at').reverse() : [];
  const chain = related.map((row) => `<li><time>${ctx.esc(shortDate(ctx, row.at, true))}</time>${inspectButton(ctx, 'events', events.indexOf(row), row.title || label(ctx, row.kind, kinds))}<small>${eventRoute(ctx, row)}</small></li>`).join('');
  return `${fields(ctx, [
    [ctx.t('Event', 'Событие'), label(ctx, event.kind, kinds)],
    [ctx.t('Time', 'Время'), shortDate(ctx, event.at, true)],
    [ctx.t('From', 'От кого'), event.fromName || agentName(ctx, event.from)],
    [ctx.t('To', 'Кому'), event.toName || agentName(ctx, event.to)],
    [ctx.t('Project', 'Проект'), event.project], [ctx.t('Team', 'Команда'), event.team], [ctx.t('Session', 'Сессия'), event.session],
    [ctx.t('Thread', 'Цепочка'), thread],
  ].filter(([, value]) => value))}${textBlock(ctx, event.body || event.detail)}${event.reason !== event.body && event.reason !== event.detail ? section(ctx, ctx.t('Reason', 'Причина'), textBlock(ctx, event.reason)) : ''}
    ${section(ctx, ctx.t('Related messages', 'Сообщения цепочки'), chain ? `<ol class="hv-detail-list hv-chain">${chain}</ol>` : '')}
    <div class="hv-links">${link(ctx, event.href, ctx.t('Open native session', 'Открыть сессию'))}${link(ctx, event.replyHref, ctx.t('Open conversation with recipient', 'Открыть диалог с получателем'))}</div>`;
}

function communicationChannel(event) {
  if (event.channel) return event.channel;
  return event.from && event.to && ['message', 'delegation', 'delegate', 'handoff', 'result', 'error', 'feedback'].includes(event.kind) && (event.body || event.detail)
    ? 'communication' : 'technical';
}

function eventRoute(ctx, event) {
  const from = event.fromName || agentName(ctx, event.from);
  const to = event.toName || agentName(ctx, event.to);
  return [from, to].filter(Boolean).map((item) => display(ctx, item)).join(' <span aria-hidden="true">→</span> ');
}

function communicationView(ctx, filter) {
  const events = list(ctx.data.events);
  const channel = filter.channel || 'communication';
  const visible = events.filter((event) => communicationChannel(event) === channel);
  const endpoints = [...new Map(visible.flatMap((event) => [[event.from, event.fromName], [event.to, event.toName]]).filter(([id]) => id)).entries()]
    .map(([id, name]) => [String(id), name || agentName(ctx, id)]).sort((a, b) => String(a[1]).localeCompare(String(b[1])));
  const results = latest(visible, 'at').filter((row) => match(row, filter, ['kind', 'session'], [row.title, row.body, row.detail, row.reason, row.fromName || agentName(ctx, row.from), row.toName || agentName(ctx, row.to), row.project, row.team, row.session, row.thread]) && (!filter.agent || [String(row.from), String(row.to)].includes(filter.agent)));
  let previousDay;
  const rows = results.map((event) => {
    const day = dateLabel(ctx, event.at);
    const heading = day === previousDay ? '' : `<h3 class="hv-date-divider">${ctx.esc(day)}</h3>`;
    previousDay = day;
    const tone = event.kind === 'error' ? 'red' : event.kind === 'result' ? 'green' : ['handoff', 'delegation', 'delegate'].includes(event.kind) ? 'violet' : 'accent';
    const body = event.body || event.detail || event.reason;
    const preview = typeof body === 'string' && body.length > 420 ? `${body.slice(0, 420)}…` : body;
    return `${heading}<button type="button" class="hv-event hv-tone-${tone}" data-hv-inspect="events:${events.indexOf(event)}">
      <time>${ctx.esc(shortDate(ctx, event.at))}</time><span class="hv-event-mark" aria-hidden="true"></span><span class="hv-event-main">
        <span class="hv-event-route">${eventRoute(ctx, event)}</span><span class="hv-event-title">${display(ctx, event.title || label(ctx, event.kind, kinds))}</span>
        ${preview ? `<span class="hv-event-preview">${display(ctx, preview)}</span>` : ''}
      </span><span class="hv-event-tags">${tags(ctx, [label(ctx, event.kind, kinds), event.project, event.team])}</span><span class="hv-event-arrow" aria-hidden="true">↗</span>
    </button>`;
  }).join('');
  const modes = [['communication', ctx.t('Agent messages', 'Сообщения агентов')], ['technical', ctx.t('Technical log', 'Технический журнал')]];
  const unreadable = !ctx.demo && !ctx.loading && !ctx.data.access;
  const incomplete = !ctx.demo && !visible.length && list(ctx.data.sources).some(source =>
    ['hermes', 'pi'].includes(source.id) && source.status !== 'available');
  const noEvents = unreadable ? empty(ctx, ctx.t('Event stream unavailable', 'Лента событий недоступна'),
    ctx.t('Waiting for a successful update from WebUI and HCC.', 'Ожидается успешное обновление от WebUI и HCC.'))
    : incomplete ? empty(ctx, ctx.t('No events in the available records', 'В доступных записях нет событий'),
      ctx.t('Some Hermes or Pi records could not be collected.', 'Часть записей Hermes или Pi недоступна для сбора.'))
    : dataEmpty(ctx, visible, ctx.t('No events in this stream', 'Нет событий в этом потоке'),
      ctx.t('Records appear here when connected sources report them.', 'Записи появятся, когда их передадут подключённые источники.'));
  return `<div class="hv-page"><div class="hv-view-switch">${segment(ctx, 'channel', channel, modes, ctx.t('Event stream', 'Поток событий'))}</div>${toolbar(ctx, filter, ctx.t('Search events and agents…', 'Поиск по событиям и агентам…'), [
    select(ctx, filter, 'kind', ctx.t('Event', 'Событие'), optionsFor(visible, 'kind', (value) => label(ctx, value, kinds))),
    select(ctx, filter, 'agent', ctx.t('Agent', 'Агент'), endpoints),
    select(ctx, filter, 'session', ctx.t('Session', 'Сессия'), optionsFor(visible, 'session')),
  ])}${panel(ctx, modes.find(([value]) => channel === value)[1], results.length ? `<div class="hv-timeline">${rows}</div>` : noEvents,
    unreadable || incomplete ? '' : `${results.length} / ${visible.length}`)}</div>`;
}

const lanes = [
  ['triage', 'Triage', 'На разборе', 'violet'], ['todo', 'To do', 'К выполнению', 'dim'],
  ['scheduled', 'Scheduled', 'Запланировано', 'amber'], ['ready', 'Ready', 'Готово к запуску', 'accent'],
  ['running', 'Running', 'В работе', 'accent'], ['blocked', 'Blocked', 'Заблокировано', 'red'],
  ['review', 'Review', 'На проверке', 'violet'], ['done', 'Done', 'Готово', 'green'],
  ['archived', 'Archived', 'В архиве', 'dim'], ['other', 'Other', 'Другие', 'dim'],
];

function lane(value) {
  const normalized = String(value ?? '').toLowerCase().replace(/[ -]/g, '_');
  if (lanes.some(([key]) => key === normalized)) return normalized;
  if (['backlog', 'open', 'new'].includes(normalized)) return 'todo';
  if (['active', 'busy', 'in_progress', 'doing', 'claimed', 'assigned', 'working'].includes(normalized)) return 'running';
  if (['waiting', 'queued', 'pending', 'retrying', 'paused'].includes(normalized)) return 'scheduled';
  if (['failed', 'error', 'timed_out', 'stalled', 'interrupted'].includes(normalized)) return 'blocked';
  if (['completed', 'succeeded', 'success', 'closed', 'resolved', 'complete'].includes(normalized)) return 'done';
  return 'other';
}

function taskDependencies(value) {
  if (Array.isArray(value)) return {count: value.length, items: value};
  if (valueOf(value) !== null) return {count: value, items: []};
  if (value && typeof value === 'object') {
    const items = list(value.items || value.tasks || value.blockedBy);
    return {count: valueOf(value.count) ?? items.length, items};
  }
  return {count: null, items: []};
}

function taskDetails(ctx, task) {
  const tasks = list(ctx.data.tasks);
  const taskDeps = taskDependencies(task.dependsOn);
  const dependencies = (value) => taskDependencies(value).items.map((dependency) => {
    const record = dependency && typeof dependency === 'object' ? dependency : {id: dependency};
    const id = record.id ?? record.taskId;
    const related = id == null ? null : tasks.find((item) => String(item.id) === String(id));
    const href = record.href || record.url || related?.href || safeLink(id);
    const title = related?.title || record.title || id || ctx.t('Task', 'Задача');
    const action = related || (typeof ctx.taskDetail === 'function' && typeof id === 'string' && record.available !== false)
      ? `<button type="button" class="hv-inline-action" data-hv-task="${ctx.esc(id)}">${display(ctx, title)} <span aria-hidden="true">↗</span></button>`
      : link(ctx, href, title) || display(ctx, title);
    return `<li><div class="hv-detail-meta">${action}${related?.status || record.status ? status(ctx, related?.status || record.status) : ''}</div></li>`;
  }).join('');
  const attempts = list(task.attempts).map((attempt) => {
    if (!attempt || typeof attempt !== 'object') return `<li>${textBlock(ctx, attempt)}</li>`;
    const started = attempt.startedAt ? shortDate(ctx, attempt.startedAt, true) : null;
    const ended = attempt.endedAt ? shortDate(ctx, attempt.endedAt, true) : null;
    return `<li><div class="hv-detail-meta">${attempt.status ? status(ctx, attempt.status) : ''}<strong>${display(ctx, attempt.title || attempt.id)}</strong><time>${ctx.esc([started, ended].filter(Boolean).join(' → ') || shortDate(ctx, attempt.at, true))}</time></div>
      ${textBlock(ctx, attempt.result || attempt.summary || attempt.body)}${textBlock(ctx, attempt.error || attempt.reason || attempt.blockedReason)}
      <div class="hv-detail-meta">${tags(ctx, [attempt.assignee || attempt.worker || attempt.profile, attempt.step, attempt.outcome, attempt.session, attempt.job])}${link(ctx, attempt.sessionHref, ctx.t('Open session', 'Открыть сессию'))}</div></li>`;
  }).join('');
  const entries = (items) => list(items).map((entry) => {
    if (typeof entry !== 'object' || !entry) return `<li>${textBlock(ctx, entry)}</li>`;
    const at = entry.at || entry.createdAt || entry.updatedAt;
    const attributes = entry.details && typeof entry.details === 'object' ? Object.entries(entry.details).map(([name, value]) => `${name.replaceAll('_', ' ')} · ${Array.isArray(value) ? value.join(', ') : value}`) : [];
    return `<li><div class="hv-detail-meta">${tags(ctx, [entry.author || entry.actor, entry.kind || entry.type || entry.status, entry.runId])}${at ? `<time>${ctx.esc(shortDate(ctx, at, true))}</time>` : ''}</div>
      ${textBlock(ctx, entry.body || entry.text || entry.message || entry.summary || entry.detail || entry.content)}
      <div class="hv-detail-meta">${tags(ctx, attributes)}</div></li>`;
  }).join('');
  const details = [
    [ctx.t('Task ID', 'ID задачи'), task.id], [ctx.t('Assignee', 'Назначено'), agentName(ctx, task.assignee)],
    [ctx.t('Executor', 'Исполнитель'), agentName(ctx, task.executor || task.activity?.profile)],
    [ctx.t('Project', 'Проект'), task.project], [ctx.t('Board', 'Доска'), task.board], [ctx.t('Namespace', 'Пространство имён'), task.tenant],
    [ctx.t('Created', 'Создана'), task.createdAt && shortDate(ctx, task.createdAt, true)],
    [ctx.t('Started', 'Запуск'), task.startedAt && shortDate(ctx, task.startedAt, true)],
    [ctx.t('Completed', 'Завершена'), task.completedAt && shortDate(ctx, task.completedAt, true)],
    [ctx.t('Last update', 'Обновление'), task.updatedAt && shortDate(ctx, task.updatedAt, true)],
    [ctx.t('Session', 'Сессия'), safeLink(task.session) ? null : task.session], [ctx.t('Current run', 'Текущая попытка'), task.currentRunId],
    [ctx.t('Created by', 'Автор'), task.createdBy], [ctx.t('Model override', 'Модель задания'), task.modelOverride],
    [ctx.t('Provider override', 'Провайдер задания'), task.providerOverride], [ctx.t('Block type', 'Тип блокировки'), task.blockKind],
    [ctx.t('Dependencies', 'Зависимости'), taskDeps.count], [ctx.t('Attempts', 'Попытки'), Array.isArray(task.attempts) ? task.attempts.length : task.attempts],
  ].filter(([, value]) => value != null && value !== '');
  const linked = list(task.links).map((item) => {
    if (!item || typeof item !== 'object') return '';
    const name = item.title || item.label || item.id || item.url || item.href;
    return name ? `<li><div class="hv-detail-meta">${link(ctx, item.href || item.url, String(name)) || display(ctx, name)} ${tags(ctx, [item.kind || item.type])}</div></li>` : '';
  }).join('');
  const listSection = (title, body) => section(ctx, title, body ? `<ul class="hv-detail-list">${body}</ul>` : '');
  const activity = task.activity;
  const latestEvent = activity?.latestEvent;
  const activityContent = activity && typeof activity === 'object' ? fields(ctx, [
    [ctx.t('Started', 'Запуск'), activity.startedAt && shortDate(ctx, activity.startedAt, true)],
    [ctx.t('Last heartbeat', 'Последний сигнал активности'), activity.heartbeatAt && shortDate(ctx, activity.heartbeatAt, true)],
    [ctx.t('Last action', 'Последнее действие'), latestEvent?.summary || latestEvent?.kind],
    [ctx.t('Last action time', 'Время последнего действия'), latestEvent?.at && shortDate(ctx, latestEvent.at, true)],
  ].filter(([, value]) => value)) : textBlock(ctx, activity);
  return `${status(ctx, task.status)}${fields(ctx, details)}
    ${section(ctx, ctx.t('Activity', 'Текущая деятельность'), activityContent)}
    ${section(ctx, ctx.t('Description', 'Описание'), textBlock(ctx, task.description))}
    ${section(ctx, ctx.t('Acceptance criteria', 'Критерии выполнения'), textBlock(ctx, task.criteria))}
    ${section(ctx, ctx.t('Blocking reason', 'Причина блокировки'), textBlock(ctx, task.blockedReason))}
    ${section(ctx, ctx.t('Result', 'Результат'), textBlock(ctx, task.result))}
    ${listSection(ctx.t('Dependencies', 'Зависимости'), dependencies(task.dependsOn))}
    ${listSection(ctx.t('Dependent tasks', 'Зависимые задачи'), dependencies(task.dependents))}
    ${listSection(ctx.t('Comments', 'Комментарии'), entries(task.comments))}
    ${listSection(ctx.t('Change history', 'История изменений'), entries(task.events || task.history))}
    ${listSection(ctx.t('Attempt history', 'История попыток'), attempts)}
    ${listSection(ctx.t('Related records', 'Связанные записи'), linked)}
    ${section(ctx, ctx.t('Collection limits', 'Лимиты сбора'), list(task.limits).length ? textBlock(ctx, task.limits.join(', ')) : '')}
    <div class="hv-links">${link(ctx, task.href, ctx.t('Open native task', 'Открыть задачу'))}${link(ctx, task.sessionHref || task.session, ctx.t('Open session', 'Открыть сессию'))}
      ${link(ctx, task.prHref || task.pr, ctx.t('Open pull request', 'Открыть пул-реквест'))}${link(ctx, task.jobHref || task.job, ctx.t('Open job', 'Открыть задание'))}
    </div>`;
}

function missionView(ctx, filter) {
  const tasks = list(ctx.data.tasks);
  const filtered = latest(tasks, 'updatedAt').filter((row) => match(row, filter, ['status', 'board', 'team', 'project', 'assignee'], [row.id, row.title, row.description, agentName(ctx, row.assignee), row.project, row.team, row.board]));
  const projects = new Map();
  for (const task of filtered) {
    const name = task.project || task.board;
    if (!name || lane(task.status) === 'archived') continue;
    if (!projects.has(name)) projects.set(name, { done: 0, total: 0 });
    const progress = projects.get(name);
    progress.total += 1;
    if (lane(task.status) === 'done') progress.done += 1;
  }
  const progress = [...projects].map(([name, { done, total }]) =>
    `<div class="hv-project"><strong>${display(ctx, name)}</strong><span>${ctx.esc(ctx.t('Completed', 'Завершено'))} ${done} / ${total}</span><progress value="${done}" max="${total}"></progress></div>`
  ).join('');
  const counts = new Map(lanes.map(([key]) => [key, filtered.filter((task) => lane(task.status) === key)]));
  const columns = lanes.filter(([key]) => (key !== 'archived' || filter.showArchived || filter.status === 'archived') && (counts.get(key).length || (filter.showEmpty && key !== 'other')));
  const widths = [['compact', ctx.t('Compact', 'Узкие')], ['standard', ctx.t('Standard', 'Обычные')], ['wide', ctx.t('Wide', 'Широкие')]];
  const width = filter.width || 'standard';
  const board = columns.map(([key, en, ru, tone]) => {
    const items = counts.get(key);
    const cards = items.map((task) => {
      const dependencies = taskDependencies(task.dependsOn).count;
      const attempts = Array.isArray(task.attempts) ? task.attempts.length : valueOf(task.attempts);
      return `<button type="button" class="hv-task" data-hv-inspect="tasks:${tasks.indexOf(task)}"><span class="hv-task-top"><span>${display(ctx, task.id)}</span>${status(ctx, task.status)}</span><strong>${display(ctx, task.title)}</strong>
        ${task.description || task.activity ? `<span class="hv-task-preview">${display(ctx, task.activity || task.description)}</span>` : ''}
        <span class="hv-task-tags">${tags(ctx, [agentName(ctx, task.assignee), task.project])}</span>
        <span class="hv-task-meta">${tags(ctx, [dependencies ? `${ctx.t('Dependencies', 'Зависимости')} · ${dependencies}` : null, attempts ? `${ctx.t('Attempts', 'Попытки')} · ${attempts}` : null, task.updatedAt ? shortDate(ctx, task.updatedAt, true) : null])}</span></button>`;
    }).join('');
    return `<section class="hv-lane hv-tone-${tone}"><header><span aria-hidden="true"></span><h3>${ctx.esc(ctx.t(en, ru))}</h3><small>${items.length}</small></header><div>${cards || `<p class="hv-lane-empty">${ctx.esc(ctx.t('No tasks', 'Нет задач'))}</p>`}</div></section>`;
  }).join('');
  return `<div class="hv-page">${toolbar(ctx, filter, ctx.t('Search tasks and assignees…', 'Поиск по задачам и исполнителям…'), [
    select(ctx, filter, 'status', ctx.t('Status', 'Статус'), optionsFor(tasks, 'status', (value) => label(ctx, value))),
    select(ctx, filter, 'board', ctx.t('Board', 'Доска'), optionsFor(tasks, 'board')),
    tasks.some(task => task.team) ? select(ctx, filter, 'team', ctx.t('Team', 'Команда'), optionsFor(tasks, 'team')) : '',
    select(ctx, filter, 'project', ctx.t('Project', 'Проект'), optionsFor(tasks, 'project')),
    select(ctx, filter, 'assignee', ctx.t('Assignee', 'Исполнитель'), optionsFor(tasks, 'assignee', (value) => agentName(ctx, value))),
    select(ctx, {width}, 'width', ctx.t('Column width', 'Ширина колонок'), widths, ''),
  ])}${progress ? panel(ctx, ctx.t('Project progress', 'Прогресс проектов'), `<div class="hv-projects">${progress}</div>`) : ''}
  ${panel(ctx, ctx.t('Hermes Kanban', 'Канбан Hermes'), `<div class="hv-board-options">${toggle(ctx, 'showEmpty', ctx.t('Show empty columns', 'Показывать пустые колонки'), filter.showEmpty)}${toggle(ctx, 'showArchived', ctx.t('Show archive', 'Показывать архив'), filter.showArchived)}</div>`
    + (columns.length ? `<div class="hv-board hv-board-${ctx.esc(width)}" id="hv-board" tabindex="0" role="region" aria-label="${ctx.esc(ctx.t('Kanban columns', 'Колонки канбана'))}">${board}</div>` : dataEmpty(ctx, tasks, ctx.t('No Kanban tasks', 'Нет задач в канбане'), ctx.t('Tasks appear here when a Hermes Kanban source is available.', 'Задачи появятся здесь, когда станет доступен источник канбана Hermes.'))), `${columns.reduce((total, [key]) => total + counts.get(key).length, 0)} / ${tasks.length}`)}</div>`;
}

function sum(rows, key) {
  const measured = rows.map((row) => valueOf(row[key])).filter((value) => value !== null);
  return measured.length ? measured.reduce((total, value) => total + value, 0) : null;
}

function chart(ctx, rows, series, unit = '', maxGap = Infinity) {
  const points = rows.filter((row) => Number.isFinite(timestamp(row.at))).sort((a, b) => timestamp(a.at) - timestamp(b.at));
  const values = points.flatMap((row) => series.map(([key]) => valueOf(row[key])).filter((value) => value !== null));
  if (!values.length) return empty(ctx, ctx.t('No chart data', 'Нет данных для графика'), ctx.t('This view needs timestamped measurements.', 'Для графика нужны измерения с указанием времени.'));
  const width = 760;
  const height = 168;
  const left = 69;
  const right = width - 15;
  const top = 13;
  const bottom = height - 24;
  const start = timestamp(points[0].at);
  const end = timestamp(points.at(-1).at);
  const lowest = values.reduce((min, value) => Math.min(min, value), 0);
  const highest = values.reduce((max, value) => Math.max(max, value), lowest + 1);
  const compact = new Intl.NumberFormat(ctx.t('en-US', 'ru-RU'), { notation: 'compact', maximumFractionDigits: 1 });
  const x = (at) => start === end ? (left + right) / 2 : left + ((timestamp(at) - start) / (end - start)) * (right - left);
  const y = (value) => bottom - ((value - lowest) / (highest - lowest)) * (bottom - top);
  const direction = ctx.t('Time', 'Время');
  const activeSeries = series.filter(([key]) => points.some((row) => valueOf(row[key]) !== null));
  const grid = [0, 0.25, 0.5, 0.75, 1].map((fraction) => {
    const value = lowest + fraction * (highest - lowest);
    return `<line x1="${left}" x2="${right}" y1="${y(value)}" y2="${y(value)}"/><text x="${left - 8}" y="${y(value) + 4}" text-anchor="end">${ctx.esc(compact.format(value))}</text>`;
  }).join('');
  const pointStride = Math.ceil(points.length / 36);
  const traces = activeSeries.map(([key, title, tone]) => {
    let firstPoint = true;
    let previous = null;
    let path = '';
    const nodes = points.map((row, index) => {
      const value = valueOf(row[key]);
      if (value === null) {
        previous = null;
        return '';
      }
      const pointX = x(row.at);
      const pointY = y(value);
      const stamp = timestamp(row.at);
      const connected = previous !== null && stamp - previous < maxGap;
      const point = !connected || index % pointStride === 0 || index === points.length - 1;
      path += `${connected ? 'L' : 'M'}${pointX.toFixed(2)} ${pointY.toFixed(2)} `;
      previous = stamp;
      if (!point) return '';
      const tooltip = `${title} · ${num(ctx, value, unit, 2)} · ${shortDate(ctx, row.at, true)}`;
      const tabindex = firstPoint ? 0 : -1;
      firstPoint = false;
      return `<circle cx="${pointX}" cy="${pointY}" r="3.5" tabindex="${tabindex}" role="img" aria-label="${ctx.esc(tooltip)}" data-hv-point="${ctx.esc(tooltip)}"><title>${ctx.esc(tooltip)}</title></circle>`;
    }).join('');
    return `<g class="hv-trace hv-tone-${tone}"><path d="${path}"/>${nodes}</g>`;
  }).join('');
  const legend = series.map(([key, title, tone]) => `<span class="hv-tone-${tone}"><i aria-hidden="true"></i>${ctx.esc(title)}${activeSeries.some(([item]) => key === item) ? '' : ` · ${ctx.esc(ctx.t('unavailable', 'нет данных'))}`}</span>`).join('');
  return `<div class="hv-chart"><svg viewBox="0 0 ${width} ${height}" aria-label="${ctx.esc(series.map(([, title]) => title).join(', '))}"><g class="hv-chart-grid">${grid}<text x="${left}" y="${height - 5}">${ctx.esc(shortDate(ctx, start, true))}</text><text x="${right}" y="${height - 5}" text-anchor="end">${ctx.esc(start === end ? direction : shortDate(ctx, end, true))}</text></g>${traces}</svg><div class="hv-chart-foot"><div class="hv-legend">${legend}${unit ? `<span>${ctx.esc(unit)}</span>` : ''}</div><output class="hv-chart-tip" aria-live="polite"></output></div></div>`;
}

function timeRanges(ctx) {
  return [['1h', ctx.t('1 hour', '1 час')], ['24h', ctx.t('24 hours', '24 часа')], ['7d', ctx.t('7 days', '7 дней')], ['30d', ctx.t('30 days', '30 дней')]];
}

const rangeWindow = {'1h': 3600000, '24h': 86400000, '7d': 604800000, '30d': 2592000000};

function observationRange(filter) {
  return filter.range || '24h';
}

function historyGap(history) {
  const seconds = valueOf(history?.coverage?.bucketSeconds);
  return seconds > 0 ? seconds * 2000 : Infinity;
}

function observationData(ctx, filter) {
  const range = observationRange(filter);
  const data = ctx.data.history;
  if (data?.range === range) return data;
  if (typeof ctx.loadHistory !== 'function') {
    return {range, usage: [], gpus: Object.fromEntries(list(ctx.data.gpus).map((gpu) => [gpu.id, list(gpu.history)])), engine: list(ctx.data.metrics?.history)};
  }
  if (!ctx.demo && ctx.data.access === false && !ctx.loading) return {range, proxyUnavailable: true};
  return null;
}

function observationState(ctx, history) {
  if (!history) return empty(ctx, ctx.t('Loading history', 'Загружается история'), ctx.t('Reading the HCC telemetry store.', 'Читается хранилище телеметрии HCC.'));
  if (history.proxyUnavailable) return empty(ctx, ctx.t('History unavailable', 'История недоступна'), ctx.t('Check the WebUI proxy status in Data sources.', 'Проверьте состояние прокси WebUI в разделе источников данных.'));
  if (history.error) return `<div class="hv-history-status">${empty(ctx, ctx.t('History unavailable', 'История недоступна'), ctx.t('The HCC history request failed.', 'Не удалось получить историю HCC.'))}
    <button type="button" data-hv-history-retry class="hv-inline-action">${ctx.esc(ctx.t('Retry', 'Повторить'))}</button></div>`;
  return '';
}

function observationNote(ctx, history) {
  const dates = [history?.startedAt && shortDate(ctx, history.startedAt, true), history?.lastAt && shortDate(ctx, history.lastAt, true)].filter(Boolean);
  const coverage = history?.coverage;
  const checks = [
    ['bucketSeconds', ctx.t('Interval', 'Интервал'), ctx.t('s', 'с')],
    ['counterResets', ctx.t('Counter resets', 'Сбросы счётчиков')],
    ['scopeChanges', ctx.t('Coverage changes', 'Изменения охвата')],
    ['limitedObservations', ctx.t('Limited observations', 'Неполные наблюдения')],
    ['conflictingSeries', ctx.t('Counter conflicts', 'Конфликты счётчиков')],
  ];
  const notes = dates.length ? [`${ctx.t('HCC collection', 'Сбор HCC')} · ${dates.join(' → ')}`] : [];
  for (const [key, name, unit] of checks) {
    if (valueOf(coverage?.[key]) > 0) notes.push(`${name} · ${num(ctx, coverage[key], unit)}`);
  }
  if (coverage?.sizePrunedBefore) notes.push(`${ctx.t('Older samples pruned', 'Старые замеры удалены')} · ${shortDate(ctx, coverage.sizePrunedBefore, true)}`);
  return notes.length ? `<p class="hv-note">${notes.map((note) => ctx.esc(note)).join(' · ')}</p>` : '';
}

function usageDetails(ctx, row) {
  const raw = [
    [ctx.t('Time', 'Время'), shortDate(ctx, row.at, true)], [ctx.t('Agent', 'Агент'), agentName(ctx, row.agent)],
    [ctx.t('Project', 'Проект'), row.project], [ctx.t('Model', 'Модель'), row.model],
    [ctx.t('Task', 'Задача'), row.task], [ctx.t('Session', 'Сессия'), row.session],
  ];
  return `${fields(ctx, raw)}${fields(ctx, usageDimensions(ctx).map(([key, title, unit]) => [title, num(ctx, row[key], unit, 2)]))}${link(ctx, row.href, ctx.t('Open native session', 'Открыть сессию'))}`;
}

function usageDimensions(ctx) {
  return [
    ['input', ctx.t('Input tokens', 'Входные токены'), ''], ['output', ctx.t('Output tokens', 'Выходные токены'), ''],
    ['cached', ctx.t('Cached tokens', 'Токены из кеша'), ''], ['reasoning', ctx.t('Reasoning tokens', 'Токены рассуждения'), ''],
    ['requests', ctx.t('Requests', 'Запросы'), ''], ['ttftMs', ctx.t('Time to first token', 'Время до первого токена'), ctx.t('ms', 'мс')],
    ['tokensPerSecond', ctx.t('Generation rate', 'Скорость генерации'), ctx.t('tok/s', 'ток/с')],
    ['durationMs', ctx.t('Duration', 'Длительность'), ctx.t('ms', 'мс')], ['contextTokens', ctx.t('Context tokens', 'Токены контекста'), ''],
    ['errors', ctx.t('Errors', 'Ошибки'), ''], ['retries', ctx.t('Retries', 'Повторы'), ''], ['orchestration', ctx.t('Orchestration', 'Оркестрация'), ''],
  ];
}

function usageView(ctx, filter) {
  const usage = list(ctx.data.usage);
  const filtered = latest(usage, 'at').filter((row) => match(row, filter, ['agent', 'project', 'model', 'task', 'session'], [agentName(ctx, row.agent), row.model, row.project, row.task, row.session]));
  const history = observationData(ctx, filter);
  const observed = list(history?.usage);
  const tokenSeries = [
    ['input', ctx.t('Input', 'Вход'), 'accent'], ['output', ctx.t('Output', 'Выход'), 'green'],
    ['cached', ctx.t('Cache', 'Кеш'), 'amber'], ['reasoning', ctx.t('Reasoning', 'Рассуждение'), 'violet'],
  ];
  const tokenTotals = (value, note) => usageDimensions(ctx).slice(0, 5).map(([key, title], index) => stat(ctx, title, num(ctx, value(key)), note, tokenSeries[index]?.[2] || 'accent')).join('');
  const headline = engineStats(ctx, ctx.data.metrics, ['throughput', 'running', 'waiting', 'ttftMs']);
  const perRequest = list(ctx.data.requestMetrics);
  const completed = list(ctx.data.runs).filter(row => row.completedMetrics);
  const attribution = perRequest.length ? requestTable(ctx, perRequest) : `<p class="hv-note">${ctx.esc(ctx.t(
    'The vLLM Prometheus endpoint reports aggregate counters and gauges. It does not expose active request IDs or a mapping to Hermes sessions, agents or Pi jobs. Request-level throughput, TTFT and token attribution are unavailable.',
    'Prometheus vLLM отдаёт агрегированные счётчики и показатели. В них нет ID активных запросов или связи с сессиями Hermes, агентами и заданиями Pi. Скорость генерации, TTFT и токены по запросам недоступны.',
  ))}</p>`;
  const headers = [ctx.t('Last activity', 'Последняя активность'), ctx.t('Scope', 'Агент и контекст'), ctx.t('Model', 'Модель'), ctx.t('Input', 'Вход'), ctx.t('Output', 'Выход'), ctx.t('Requests', 'Запросы'), ''];
  const body = filtered.map((row) => `<tr><td>${ctx.esc(shortDate(ctx, row.at, true))}</td><td><strong>${display(ctx, agentName(ctx, row.agent))}</strong><small>${display(ctx, row.project || row.task || row.session)}</small></td><td>${display(ctx, row.model)}</td><td>${ctx.esc(num(ctx, row.input))}</td><td>${ctx.esc(num(ctx, row.output))}</td><td>${ctx.esc(num(ctx, row.requests))}</td><td><button class="hv-row-action" type="button" aria-label="${ctx.esc(ctx.t('Inspect usage record', 'Открыть запись использования'))}" data-hv-inspect="usage:${usage.indexOf(row)}">↗</button></td></tr>`).join('');
  const records = filtered.length ? `<div class="hv-table-wrap"><table class="hv-table"><thead><tr>${headers.map((title) => `<th>${ctx.esc(title)}</th>`).join('')}</tr></thead><tbody>${body}</tbody></table></div>` : dataEmpty(ctx, usage, ctx.t('No usage records', 'Нет записей об использовании'), ctx.t('Connect a Hermes source with usage telemetry to see measurements here.', 'Здесь появятся измерения из источника телеметрии Hermes.'));
  const observedPanel = observationState(ctx, history) || `<div class="hv-metrics hv-token-stats">${tokenTotals((key) => sum(observed, key), ctx.t('Observed counter increase', 'Измеренный прирост счётчика'))}</div>
    ${chart(ctx, observed, tokenSeries, '', historyGap(history))}${observationNote(ctx, history)}
    <p class="hv-note">${ctx.esc(ctx.t('HCC adds increments between counter samples after collection starts. Each point shows increments in its interval.', 'HCC суммирует приросты между замерами после начала сбора. Каждая точка показывает прирост за свой интервал.'))}</p>`;
  const baseline = history?.baseline || {};
  const baselinePanel = usageDimensions(ctx).slice(0, 5).some(([key]) => valueOf(baseline[key]) !== null)
    ? panel(ctx, ctx.t('Cumulative counters at start of HCC collection', 'Накопленные счётчики на старте сбора HCC'), `<div class="hv-metrics hv-token-stats">${tokenTotals((key) => baseline[key], ctx.t('Initial value', 'Начальное значение'))}</div>`, shortDate(ctx, history?.baselineAt, true))
    : '';
  const unplaced = history?.unplaced || {};
  const unplacedPanel = usageDimensions(ctx).slice(0, 5).some(([key]) => valueOf(unplaced[key]) > 0)
    ? panel(ctx, ctx.t('Counter increases with unknown timing', 'Прирост счётчиков без известного времени'), `<div class="hv-metrics hv-token-stats">${tokenTotals((key) => unplaced[key], ctx.t('Excluded from timed history', 'Не включены в историю по времени'))}</div>`)
    : '';
  return `<div class="hv-page"><div class="hv-stats">${headline}</div>
    ${panel(ctx, ctx.t('Per-request telemetry', 'Телеметрия по запросам'), attribution)}
    ${completed.length ? panel(ctx, ctx.t('Completed Open Agents runs', 'Завершённые запуски Open Agents'),
      `<p class="hv-note">${ctx.esc(ctx.t('Reported by completed Pi tool results. Duration is the whole tool call.', 'Данные из завершённых вызовов Pi. Длительность относится ко всему вызову инструмента.'))}</p>
      <div class="hv-table-wrap"><table class="hv-table"><thead><tr>${[ctx.t('Subagent','Субагент'), ctx.t('Pi job','Задание Pi'), ctx.t('Input','Вход'), ctx.t('Output','Выход'), ctx.t('Cache read','Чтение кеша'), ctx.t('Elapsed','Длительность')]
        .map(title => `<th>${ctx.esc(title)}</th>`).join('')}</tr></thead><tbody>${completed.slice(0, 40).map(row => `<tr><td><strong>${display(ctx, row.name)}</strong><small>${display(ctx, row.model)}</small></td><td>${display(ctx, row.jobId)}</td>
        ${['input','output','cached','durationMs'].map(key => `<td>${ctx.esc(num(ctx, row.completedMetrics[key], key === 'durationMs' ? ctx.t('ms','мс') : ''))}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`) : ''}
    <div class="hv-view-switch">${segment(ctx, 'range', observationRange(filter), timeRanges(ctx), ctx.t('Observation period', 'Период наблюдения'))}</div>
    ${panel(ctx, ctx.t('Observed token use', 'Измеренный расход токенов'), observedPanel)}
    ${baselinePanel}${unplacedPanel}
    ${panel(ctx, ctx.t('Cumulative Hermes session usage', 'Накопленные токены сессий Hermes'), `<p class="hv-note">${ctx.esc(ctx.t('Each record shows lifetime session counters. Last activity identifies the record and is independent of when the tokens were used.', 'Строки содержат накопленные счётчики сессий. Дата последней активности относится к записи и не определяет, когда были потрачены токены.'))}</p>
    ${toolbar(ctx, filter, ctx.t('Search usage by agent or task…', 'Поиск по агентам и задачам…'), [
    select(ctx, filter, 'agent', ctx.t('Agent', 'Агент'), optionsFor(usage, 'agent', (value) => agentName(ctx, value))),
    select(ctx, filter, 'project', ctx.t('Project', 'Проект'), optionsFor(usage, 'project')),
    select(ctx, filter, 'model', ctx.t('Model', 'Модель'), optionsFor(usage, 'model')),
  ])}${records}`, `${filtered.length} / ${usage.length}`)}</div>`;
}

function gpuMetric(ctx, name, value, unit, progress, tone) {
  const measured = valueOf(progress);
  const width = measured === null ? 0 : Math.min(100, Math.max(0, measured));
  return `<div class="hv-gpu-metric hv-tone-${tone}"><span>${ctx.esc(name)}</span><strong>${ctx.esc(num(ctx, value, unit, 1))}</strong>${measured !== null ? `<span class="hv-gauge" aria-hidden="true"><i style="width:${width}%"></i></span>` : ''}</div>`;
}

const gpuTones = ['accent', 'violet', 'amber', 'green', 'red'];

function gpuHistory(ctx, history, gpus, filter) {
  const samples = history.gpus && typeof history.gpus === 'object' ? history.gpus : {};
  const ids = [...new Set([...gpus.map((gpu) => String(gpu.id)), ...Object.keys(samples)])];
  if (!ids.length) return empty(ctx, ctx.t('No GPU devices', 'Нет данных об устройствах GPU'), ctx.t('GPU histories appear once the sampler has readings.', 'История появится, когда сборщик получит показания GPU.'));
  const deviceRows = ids.map((id, index) => [id, `GPU ${id}`, gpuTones[index % gpuTones.length]]);
  const hidden = list(filter.hiddenGpu);
  const shown = deviceRows.filter(([id]) => !hidden.includes(id));
  const choices = deviceRows.map(([id, title, tone]) => `<span class="hv-gpu-toggle hv-tone-${tone}">${toggle(ctx, 'hiddenGpu', title, !hidden.includes(id), id)}</span>`).join('');
  const plots = [
    ['utilization', ctx.t('GPU utilization', 'Загрузка GPU'), '%'],
    ['memoryUsed', ctx.t('Used VRAM', 'Занято VRAM'), 'MiB'],
    ['temperature', ctx.t('Temperature', 'Температура'), '°C'],
    ['power', ctx.t('Power', 'Мощность'), ctx.t('W', 'Вт')],
  ].map(([key, title, unit]) => {
    const byTime = new Map();
    const start = Date.now() - rangeWindow[observationRange(filter)];
    for (const [id] of shown) {
      for (const sample of list(samples[id])) {
        const at = timestamp(sample.at);
        if (!Number.isFinite(at) || (ctx.demo && at < start)) continue;
        if (!byTime.has(at)) byTime.set(at, {at});
        byTime.get(at)[`gpu-${id}`] = sample[key];
      }
    }
    const lines = shown.map(([id, name, tone]) => [`gpu-${id}`, name, tone]);
    return panel(ctx, title, chart(ctx, [...byTime.values()], lines, unit, historyGap(history)));
  }).join('');
  return `<div class="hv-gpu-toggles" role="group" aria-label="${ctx.esc(ctx.t('Visible GPUs', 'Отображаемые GPU'))}">${choices}</div>
    ${shown.length ? `<div class="hv-split hv-history-grid">${plots}</div>` : empty(ctx, ctx.t('All GPUs hidden', 'Все GPU скрыты'), ctx.t('Select a device to display its history.', 'Выберите устройство, чтобы увидеть его историю.'))}`;
}

function gpuCard(ctx, gpu, position) {
  const percent = valueOf(gpu.memoryUsed) !== null && valueOf(gpu.memoryTotal) > 0 ? gpu.memoryUsed / gpu.memoryTotal * 100 : null;
  const memory = `${num(ctx, gpu.memoryUsed)} / ${num(ctx, gpu.memoryTotal, 'MiB')}`;
  return `<article class="hv-gpu"><header><span class="hv-gpu-icon" aria-hidden="true">${ctx.icon?.('chip', 18) || '▧'}</span><div><h3>${display(ctx, gpu.name || gpu.id)}</h3><small>GPU ${display(ctx, gpu.id)} · ${ctx.esc(memory)}</small></div><button class="hv-row-action" type="button" data-hv-inspect="gpus:${position}" aria-label="${ctx.esc(ctx.t('Inspect GPU', 'Открыть данные GPU'))}">↗</button></header><div class="hv-gpu-metrics">${gpuMetric(ctx, ctx.t('Utilization', 'Загрузка'), gpu.utilization, '%', gpu.utilization, 'accent')}${gpuMetric(ctx, ctx.t('VRAM usage', 'Использование VRAM'), percent, '%', percent, 'violet')}${gpuMetric(ctx, ctx.t('Temperature', 'Температура'), gpu.temperature, '°C', null, 'amber')}${gpuMetric(ctx, ctx.t('Power', 'Мощность'), gpu.power, ctx.t('W', 'Вт'), null, 'green')}</div></article>`;
}

function gpuDetails(ctx, gpu) {
  return `${fields(ctx, [
    [ctx.t('GPU identifier', 'Идентификатор GPU'), gpu.id],
    [ctx.t('Utilization', 'Загрузка'), num(ctx, gpu.utilization, '%', 1)],
    [ctx.t('Used VRAM', 'Занято VRAM'), num(ctx, gpu.memoryUsed, 'MiB', 1)],
    [ctx.t('Total VRAM', 'Всего VRAM'), num(ctx, gpu.memoryTotal, 'MiB', 1)],
    [ctx.t('Temperature', 'Температура'), num(ctx, gpu.temperature, '°C', 1)],
    [ctx.t('Power', 'Мощность'), num(ctx, gpu.power, ctx.t('W', 'Вт'), 1)],
  ])}${textBlock(ctx, gpu.detail)}`;
}

function serviceDetails(ctx, service) {
  const detailText = (value) => Array.isArray(value) ? value.join('\n') : value;
  return `${status(ctx, service.status)}${fields(ctx, [
    [ctx.t('Identifier', 'Идентификатор'), service.id],
    [ctx.t('Last update', 'Обновление'), shortDate(ctx, service.updatedAt || service.at, true)],
    [ctx.t('Last successful collection', 'Последний успешный сбор'), shortDate(ctx, service.lastSuccessAt || service.lastSuccessfulAt, true)],
  ])}${section(ctx, ctx.t('Status details', 'Причина статуса'), textBlock(ctx, ctx.t(service.detail, service.detailRu || service.detail)))}
    ${section(ctx, ctx.t('Missing data', 'Отсутствующие данные'), textBlock(ctx, detailText(service.missing)))}
    ${section(ctx, ctx.t('Collection limits', 'Лимиты сбора'), textBlock(ctx, detailText(service.limits)))}
    ${section(ctx, ctx.t('Impact on measurements', 'Влияние на показатели'), textBlock(ctx, detailText(service.impact)))}
    ${section(ctx, ctx.t('Recommended action', 'Что можно сделать'), textBlock(ctx, detailText(service.recommendation)))}
    ${link(ctx, service.href, ctx.t('Open source', 'Открыть источник'))}`;
}

function engineMetrics(ctx) {
  return [
    ['throughput', ctx.t('Generation throughput', 'Скорость генерации'), ctx.t('tok/s', 'ток/с'), 'green', ctx.t('Generation tokens per second across vLLM', 'Выходные токены в секунду по всему vLLM')],
    ['running', ctx.t('Active requests', 'Активные запросы'), '', 'accent', ctx.t('Currently running in vLLM', 'Сейчас выполняются в vLLM')],
    ['waiting', ctx.t('Waiting requests', 'Запросы в очереди'), '', 'amber', ctx.t('Currently queued in vLLM', 'Сейчас ждут в vLLM')],
    ['kvCache', ctx.t('KV cache usage', 'Загрузка KV-кеша'), '%', 'violet', ctx.t('Reported by vLLM', 'По данным vLLM')],
    ['ttftMs', ctx.t('Time to first token', 'Время до первого токена'), ctx.t('ms', 'мс'), 'violet', ctx.t('Mean of recorded TTFT observations', 'Среднее по зарегистрированным наблюдениям TTFT')],
  ];
}

function engineStats(ctx, metrics = {}, keys) {
  const definitions = new Map(engineMetrics(ctx).map((row) => [row[0], row]));
  return keys.map((metric) => {
    const [key, title, unit, tone, note] = definitions.get(metric);
    const missing = valueOf(metrics?.[key]) === null;
    return stat(ctx, title, num(ctx, metrics?.[key], unit, 2), missing ? ctx.t('No vLLM observation in the latest collection', 'В последнем сборе нет наблюдения vLLM') : note, tone);
  }).join('');
}

function engineHistory(ctx, history) {
  const definitions = new Map(engineMetrics(ctx).map((row) => [row[0], row]));
  const rows = list(history.engine);
  return [
    [ctx.t('Generation throughput', 'Скорость генерации'), ['throughput']],
    [ctx.t('Time to first token, mean', 'Время до первого токена, среднее'), ['ttftMs']],
    [ctx.t('KV cache usage', 'Загрузка KV-кеша'), ['kvCache']],
    [ctx.t('Request queue and concurrency', 'Очередь и параллельные запросы'), ['running', 'waiting']],
  ].map(([title, keys]) => {
    const metrics = keys.map((key) => definitions.get(key));
    return panel(ctx, title, chart(ctx, rows, metrics.map(([key, name, , tone]) => [key, name, tone]), metrics[0][2], historyGap(history)));
  }).join('');
}

function requestTable(ctx, rows) {
  const labels = [ctx.t('Request', 'Запрос'), ctx.t('State', 'Состояние'), ctx.t('Session', 'Сессия'), ctx.t('Output tokens', 'Выходные токены'), ctx.t('Generation rate', 'Скорость генерации'), 'TTFT'];
  const body = rows.map((row) => `<tr><td>${display(ctx, row.requestId || row.id)}</td><td>${status(ctx, row.status)}</td><td>${display(ctx, row.session || row.job)}</td><td>${ctx.esc(num(ctx, row.output))}</td><td>${ctx.esc(num(ctx, row.tokensPerSecond, ctx.t('tok/s', 'ток/с'), 1))}</td><td>${ctx.esc(num(ctx, row.ttftMs, ctx.t('ms', 'мс'), 1))}</td></tr>`).join('');
  return `<div class="hv-table-wrap"><table class="hv-table"><thead><tr>${labels.map((name) => `<th>${ctx.esc(name)}</th>`).join('')}</tr></thead><tbody>${body}</tbody></table></div>`;
}

function infrastructureView(ctx, filter) {
  const gpus = list(ctx.data.gpus);
  const services = list(ctx.data.services);
  const sources = list(ctx.data.sources);
  const engine = engineStats(ctx, ctx.data.metrics, ['throughput', 'running', 'waiting', 'kvCache', 'ttftMs']);
  const powerSources = gpus.filter((gpu) => valueOf(gpu.power) !== null);
  const power = num(ctx, sum(powerSources, 'power'), ctx.t('W', 'Вт'), 1);
  const powerNote = powerSources.length ? `${powerSources.length} / ${gpus.length} GPU` : ctx.t('No power measurements', 'Нет замеров мощности');
  const serviceRows = (items, key) => items.map((row, index) => `<button class="hv-service" type="button" data-hv-inspect="${key}:${index}"><span class="hv-service-icon" aria-hidden="true">${ctx.icon?.(key === 'sources' ? 'database' : 'activity', 17) || '◇'}</span><span class="hv-service-main"><strong>${display(ctx, row.label || row.name || row.id)}</strong>${typeof row.detail === 'string' ? `<small>${display(ctx, ctx.t(row.detail, row.detailRu || row.detail))}</small>` : ''}</span>${status(ctx, row.status)}<span class="hv-service-arrow" aria-hidden="true">↗</span></button>`).join('');
  const gpuPanel = panel(ctx, ctx.t('GPU telemetry', 'Телеметрия GPU'), gpus.length
    ? `<div class="hv-gpus">${gpus.map((gpu, index) => gpuCard(ctx, gpu, index)).join('')}</div>`
    : empty(ctx, ctx.t('No GPU samples', 'Нет данных GPU'), ctx.t('No source has reported GPU measurements.', 'Источники пока не передали измерения GPU.')));
  const servicePanel = panel(ctx, ctx.t('Services', 'Сервисы'), services.length
    ? `<div class="hv-services">${serviceRows(services, 'services')}</div>`
    : empty(ctx, ctx.t('No service status', 'Нет статусов сервисов'), ctx.t('Service health depends on the configured Hermes sources.', 'Статусы сервисов зависят от настроенных источников Hermes.')));
  const sourcePanel = panel(ctx, ctx.t('Data sources', 'Источники данных'), sources.length
    ? `<div class="hv-services">${serviceRows(sources, 'sources')}</div>`
    : empty(ctx, ctx.t('No source status', 'Нет статусов источников'), ctx.t('Source health appears after the next successful refresh.', 'Статусы источников появятся после обновления.')));
  const history = observationData(ctx, filter);
  const historyContent = observationState(ctx, history) || `${observationNote(ctx, history)}
    ${panel(ctx, ctx.t('GPU history', 'История GPU'), gpuHistory(ctx, history, gpus, filter))}
    ${panel(ctx, ctx.t('vLLM history', 'История vLLM'), `<div class="hv-split hv-history-grid">${engineHistory(ctx, history)}</div>`)}`;
  return `<div class="hv-page"><div class="hv-stats hv-infra-stats">${engine}${stat(ctx, ctx.t('Total GPU power', 'Суммарная мощность GPU'), power, powerNote, 'green')}</div>
    ${gpuPanel}<div class="hv-view-switch">${segment(ctx, 'range', observationRange(filter), timeRanges(ctx), ctx.t('History period', 'Период истории'))}</div>
    ${historyContent}<div class="hv-split">${servicePanel}${sourcePanel}</div></div>`;
}

const renderers = {
  team: teamView,
  communications: communicationView,
  missions: missionView,
  usage: usageView,
  infrastructure: infrastructureView,
};

const inspectors = {
  agents: [(ctx, row) => row.name || row.id || ctx.t('Agent', 'Агент'), agentDetails],
  piAgents: [(ctx, row) => row.name || row.id || 'Pi', agentDetails],
  runs: [(ctx, row) => row.name || row.title || row.id || ctx.t('Run', 'Запуск'), agentDetails],
  issues: [(ctx, row) => row.name || row.id || ctx.t('Agent', 'Агент'), agentDetails],
  events: [(ctx, row) => row.title || label(ctx, row.kind, kinds), eventDetails],
  tasks: [(ctx, row) => row.title || row.id || ctx.t('Task', 'Задача'), taskDetails],
  usage: [(ctx) => ctx.t('Usage record', 'Запись об использовании'), usageDetails],
  gpus: [(ctx, row) => row.name || row.id || 'GPU', gpuDetails],
  sources: [(ctx, row) => row.label || row.id || ctx.t('Source', 'Источник'), serviceDetails],
  services: [(ctx, row) => row.name || row.id || ctx.t('Service', 'Сервис'), serviceDetails],
};

export function renderView(view, ctx) {
  return Object.hasOwn(renderers, view) ? renderers[view](ctx, ctx.filters(view)) : '';
}

let inspectorSequence = 0;

function bindInspector(scope, ctx) {
  for (const button of scope.querySelectorAll('[data-hv-inspect]')) {
    const [key, position] = button.dataset.hvInspect.split(':');
    if (!Object.hasOwn(inspectors, key)) continue;
    const rows = key === 'runs' ? teamRecords(ctx.data).history : key === 'issues' ? teamRecords(ctx.data).issues : list(ctx.data[key]);
    const row = rows[Number(position)];
    if (!row) continue;
    const identity = row.id ?? [row.session, row.agent, row.model, row.task, row.at].filter((value) => value != null).join('/');
    if (!button.id && scope.matches('.hcc-content')) button.id = `hv-inspect-${key}-${identity || position}`;
    button.addEventListener('click', () => openInspector(ctx, key, row));
  }
  for (const button of scope.querySelectorAll('[data-hv-task]')) {
    const id = button.dataset.hvTask;
    const row = list(ctx.data.tasks).find((task) => task.id === id) || {id};
    button.addEventListener('click', () => openInspector(ctx, 'tasks', row));
  }
}

function openInspector(ctx, key, row) {
  const sequence = ++inspectorSequence;
  const [title, contents] = inspectors[key];
  const name = title(ctx, row);
  const draw = (record, trailing = '') => {
    const dialog = ctx.detail(title(ctx, record), contents(ctx, record) + trailing);
    if (dialog) {
      bindInspector(dialog, ctx);
      if (!dialog.contains(ctx.root.activeElement)) dialog.querySelector('[data-inspector-close]')?.focus({preventScroll: true});
    }
    return dialog;
  };
  if (key !== 'tasks' || typeof ctx.taskDetail !== 'function' || !row.id) {
    const dialog = draw(row);
    if (dialog) dialog.scrollTop = 0;
    return;
  }
  const dialog = draw(row, `<p class="hv-detail-loading" role="status">${ctx.esc(ctx.t('Loading Kanban details…', 'Загружаются подробности Kanban…'))}</p>`);
  if (dialog) dialog.scrollTop = 0;
  Promise.resolve().then(() => ctx.taskDetail(row.id)).then((value) => {
    if (!value || typeof value !== 'object') throw new Error(name);
    if (sequence === inspectorSequence && dialog?.open) draw({...row, ...value});
  }).catch(() => {
    if (sequence !== inspectorSequence || !dialog?.open) return;
    draw(row, `<div class="hv-detail-loading"><p role="status">${ctx.esc(ctx.t('Kanban details are unavailable.', 'Подробности Kanban недоступны.'))}</p>
      <button type="button" class="hv-inline-action" data-hv-task="${ctx.esc(row.id)}">${ctx.esc(ctx.t('Retry', 'Повторить'))}</button></div>`);
  });
}

export function bindView(view, ctx) {
  const scope = ctx.root.querySelector('.hcc-content');
  if (!scope || !Object.hasOwn(renderers, view)) return;
  const rendered = {...ctx, data: ctx.data};
  const filter = ctx.filters(view);
  if (['usage', 'infrastructure'].includes(view) && typeof ctx.loadHistory === 'function') {
    const range = observationRange(filter);
    if (ctx.data.history?.range !== range) void ctx.loadHistory(range);
    scope.querySelector('[data-hv-history-retry]')?.addEventListener('click', () => ctx.loadHistory(range, true));
  }
  for (const control of scope.querySelectorAll('[data-hv-filter]')) {
    const key = control.dataset.hvFilter;
    if (key === 'query') {
      const refreshResults = (event) => {
        filter.query = control.value;
        filter.teamLimit = 50;
        if (event.isComposing) return;
        const {selectionStart, selectionEnd, selectionDirection} = control;
        ctx.update();
        const refreshed = ctx.root.querySelector('.hcc-content [data-hv-filter="query"]');
        refreshed?.focus({preventScroll: true});
        refreshed?.setSelectionRange(selectionStart, selectionEnd, selectionDirection);
      };
      control.addEventListener('input', refreshResults);
      control.addEventListener('compositionend', refreshResults);
    } else {
      control.addEventListener('change', () => {
        filter[key] = control.value;
        filter.teamLimit = 50;
        ctx.update();
      });
    }
  }
  for (const button of scope.querySelectorAll('[data-hv-choice]')) {
    button.addEventListener('click', () => {
      const {hvChoice: key, hvValue: value} = button.dataset;
      if (button.getAttribute('aria-pressed') === 'true') return;
      filter[key] = value;
      if (key === 'teamScope') Object.assign(filter, {status: '', kind: '', teamLimit: 50});
      if (key === 'channel') Object.assign(filter, {kind: '', agent: '', session: ''});
      ctx.update();
    });
  }
  for (const control of scope.querySelectorAll('[data-hv-toggle]')) {
    control.addEventListener('change', () => {
      const key = control.dataset.hvToggle;
      if (key === 'hiddenGpu') {
        const hidden = new Set(list(filter.hiddenGpu));
        if (control.checked) hidden.delete(control.value);
        else hidden.add(control.value);
        filter.hiddenGpu = [...hidden];
      } else {
        filter[key] = control.checked;
      }
      ctx.update();
    });
  }
  scope.querySelector('[data-hv-more]')?.addEventListener('click', () => {
    filter.teamLimit = (filter.teamLimit || 50) + 50;
    ctx.update();
  });
  bindInspector(scope, rendered);
  for (const point of scope.querySelectorAll('[data-hv-point]')) {
    const tip = point.closest('.hv-chart')?.querySelector('.hv-chart-tip');
    if (!tip) continue;
    const show = () => { tip.textContent = point.dataset.hvPoint; };
    const hide = () => { tip.textContent = ''; };
    point.addEventListener('pointerenter', show);
    point.addEventListener('pointerleave', hide);
    point.addEventListener('focus', show);
    point.addEventListener('blur', hide);
    point.addEventListener('keydown', (event) => {
      if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === 'ArrowLeft' ? point.previousElementSibling : point.nextElementSibling;
      if (!next?.hasAttribute('data-hv-point')) return;
      point.setAttribute('tabindex', '-1');
      next.setAttribute('tabindex', '0');
      next.focus();
    });
  }
  const board = scope.querySelector('#hv-board');
  if (board) {
    board.scrollLeft = filter.boardScroll || 0;
    return () => { filter.boardScroll = board.scrollLeft; };
  }
}
