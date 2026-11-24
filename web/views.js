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
  return list(ctx.data.agents).find((agent) => agent.id === id || agent.name === id);
}

function agentName(ctx, value) {
  return findAgent(ctx, value)?.name || value || '';
}

function empty(ctx, title, description) {
  return `<div class="hv-empty"><span class="hv-empty-icon" aria-hidden="true">◇</span><strong>${ctx.esc(title)}</strong><p>${ctx.esc(description)}</p></div>`;
}

function stat(ctx, name, number, note, tone = '') {
  return `<div class="hv-stat ${tone ? `hv-tone-${tone}` : ''}"><span>${ctx.esc(name)}</span><strong>${ctx.esc(number)}</strong><small>${ctx.esc(note)}</small></div>`;
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
  return `<dl class="hv-fields">${pairs.map(([name, value]) => `<div><dt>${ctx.esc(name)}</dt><dd>${display(ctx, value)}</dd></div>`).join('')}</dl>`;
}

function agentDetails(ctx, agent) {
  return `${status(ctx, agent.status)}${fields(ctx, [
    [ctx.t('Kind', 'Тип'), label(ctx, agent.kind, kinds)], [ctx.t('Role', 'Роль'), agent.role],
    [ctx.t('Model', 'Модель'), agent.model], [ctx.t('Profile', 'Профиль'), agent.profile],
    [ctx.t('Task', 'Задача'), agent.task], [ctx.t('Project', 'Проект'), agent.project],
    [ctx.t('Team', 'Команда'), agent.team], [ctx.t('Session', 'Сессия'), agent.session],
    [ctx.t('Started', 'Запуск'), shortDate(ctx, agent.startedAt, true)], [ctx.t('Last update', 'Обновление'), shortDate(ctx, agent.updatedAt, true)],
  ])}${textBlock(ctx, agent.detail)}${link(ctx, agent.href, ctx.t('Open native session', 'Открыть сессию'))}`;
}

function teamView(ctx, filter) {
  const agents = list(ctx.data.agents);
  const active = agents.filter((agent) => ['running', 'active', 'busy', 'in_progress', 'working', 'generating', 'tool'].includes(agent.status)).length;
  const waiting = agents.filter((agent) => ['waiting', 'queued', 'pending', 'blocked'].includes(agent.status)).length;
  const attention = agents.filter((agent) => ['failed', 'error', 'offline', 'stalled', 'interrupted'].includes(agent.status)).length;
  const results = latest(agents, 'updatedAt').filter((row) => match(row, filter, ['status', 'team', 'project', 'kind'], [row.name, row.role, row.task, row.project, row.team, row.model, row.profile, row.session]));
  const cards = results.map((agent) => `<button type="button" class="hv-agent" data-hv-inspect="agents:${agents.indexOf(agent)}"><span class="hv-agent-head">${ctx.avatar(agent)}<span class="hv-agent-title"><strong>${display(ctx, agent.name || agent.id)}</strong><small>${display(ctx, agent.role || label(ctx, agent.kind, kinds))}</small></span>${status(ctx, agent.status)}</span><span class="hv-agent-task"><span>${ctx.esc(ctx.t('Current task', 'Текущая задача'))}</span><strong>${display(ctx, agent.task)}</strong></span><span class="hv-agent-meta">${tags(ctx, [agent.model, agent.profile, agent.project, agent.team])}</span><span class="hv-card-foot"><span>${ctx.esc(ctx.t('Updated', 'Обновление'))} ${ctx.esc(shortDate(ctx, agent.updatedAt, true))}</span><span aria-hidden="true">↗</span></span></button>`).join('');
  return `<div class="hv-page hv-team"><div class="hv-stats">
    ${stat(ctx, ctx.t('Observed agents', 'Агенты в источниках'), agents.length, ctx.t('Agent records', 'Записи об агентах'))}
    ${stat(ctx, ctx.t('Active', 'Активны'), active, ctx.t('Running or busy', 'Работают или заняты'), 'green')}
    ${stat(ctx, ctx.t('Waiting', 'Ожидают'), waiting, ctx.t('Queued, waiting or blocked', 'В очереди, ожидают или заблокированы'), 'amber')}
    ${stat(ctx, ctx.t('Needs attention', 'Требуют внимания'), attention, ctx.t('Failed, stalled, interrupted or offline', 'Ошибка, зависание, прерывание или отключение'), 'red')}
  </div>${toolbar(ctx, filter, ctx.t('Search agents, tasks, models…', 'Поиск по агентам, задачам, моделям…'), [
    select(ctx, filter, 'status', ctx.t('Status', 'Статус'), optionsFor(agents, 'status', (value) => label(ctx, value))),
    select(ctx, filter, 'team', ctx.t('Team', 'Команда'), optionsFor(agents, 'team')),
    select(ctx, filter, 'project', ctx.t('Project', 'Проект'), optionsFor(agents, 'project')),
    select(ctx, filter, 'kind', ctx.t('Kind', 'Тип'), optionsFor(agents, 'kind', (value) => label(ctx, value, kinds))),
  ])}${panel(ctx, ctx.t('Agents', 'Агенты'), results.length ? `<div class="hv-agent-grid">${cards}</div>` : dataEmpty(ctx, agents, ctx.t('No agent records', 'Нет записей об агентах'), ctx.t('Agents appear here when a source reports them.', 'Агенты появятся здесь, когда их передаст источник.')), `${results.length} / ${agents.length}`)}</div>`;
}

function eventDetails(ctx, event) {
  return `${fields(ctx, [
    [ctx.t('Event', 'Событие'), label(ctx, event.kind, kinds)], [ctx.t('Time', 'Время'), shortDate(ctx, event.at, true)],
    [ctx.t('From', 'От кого'), agentName(ctx, event.from)], [ctx.t('To', 'Кому'), agentName(ctx, event.to)],
    [ctx.t('Project', 'Проект'), event.project], [ctx.t('Team', 'Команда'), event.team], [ctx.t('Session', 'Сессия'), event.session],
  ])}${textBlock(ctx, event.detail)}${link(ctx, event.href, ctx.t('Open native session', 'Открыть сессию'))}`;
}

function communicationView(ctx, filter) {
  const events = list(ctx.data.events);
  const endpoints = [...new Set(events.flatMap((event) => [event.from, event.to]).filter(Boolean))].map((value) => [String(value), agentName(ctx, value)]).sort((a, b) => String(a[1]).localeCompare(String(b[1])));
  const results = latest(events, 'at').filter((row) => match(row, filter, ['kind', 'team', 'project', 'session'], [row.title, row.detail, agentName(ctx, row.from), agentName(ctx, row.to), row.project, row.team, row.session]) && (!filter.agent || [String(row.from), String(row.to)].includes(filter.agent)));
  let previousDay;
  const rows = results.map((event) => {
    const day = dateLabel(ctx, event.at);
    const heading = day === previousDay ? '' : `<h3 class="hv-date-divider">${ctx.esc(day)}</h3>`;
    previousDay = day;
    const from = agentName(ctx, event.from);
    const to = agentName(ctx, event.to);
    return `${heading}<button type="button" class="hv-event" data-hv-inspect="events:${events.indexOf(event)}"><time>${ctx.esc(shortDate(ctx, event.at))}</time><span class="hv-event-mark" aria-hidden="true"></span><span class="hv-event-main"><span class="hv-event-title">${display(ctx, event.title || label(ctx, event.kind, kinds))}</span><span class="hv-event-route">${display(ctx, from)}${to ? ` <span aria-hidden="true">→</span> ${display(ctx, to)}` : ''}</span></span><span class="hv-event-tags">${tags(ctx, [label(ctx, event.kind, kinds), event.project, event.team])}</span><span class="hv-event-arrow" aria-hidden="true">↗</span></button>`;
  }).join('');
  return `<div class="hv-page">${toolbar(ctx, filter, ctx.t('Search events and agents…', 'Поиск по событиям и агентам…'), [
    select(ctx, filter, 'kind', ctx.t('Event', 'Событие'), optionsFor(events, 'kind', (value) => label(ctx, value, kinds))),
    select(ctx, filter, 'agent', ctx.t('Agent', 'Агент'), endpoints),
    select(ctx, filter, 'team', ctx.t('Team', 'Команда'), optionsFor(events, 'team')),
    select(ctx, filter, 'project', ctx.t('Project', 'Проект'), optionsFor(events, 'project')),
    select(ctx, filter, 'session', ctx.t('Session', 'Сессия'), optionsFor(events, 'session')),
  ])}${panel(ctx, ctx.t('Event timeline', 'Хронология событий'), results.length ? `<div class="hv-timeline">${rows}</div>` : dataEmpty(ctx, events, ctx.t('No communication events', 'Нет событий обмена'), ctx.t('Messages and handoffs appear here when Hermes reports them.', 'Сообщения и передачи задач появятся здесь, когда их передаст Hermes.')), `${results.length} / ${events.length}`)}</div>`;
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
  const dependencies = taskDeps.items.map((dependency) => {
    const record = dependency && typeof dependency === 'object' ? dependency : {id: dependency};
    const id = record.id ?? record.taskId;
    const related = id == null ? null : tasks.find((item) => String(item.id) === String(id));
    const href = record.href || record.url || related?.href || safeLink(id);
    const title = related?.title || record.title || id || ctx.t('Task', 'Задача');
    return `<li>${link(ctx, href, title) || display(ctx, title)}${related?.status ? ` ${status(ctx, related.status)}` : ''}</li>`;
  }).join('');
  const attempts = list(task.attempts).map((attempt) => `<li>${display(ctx, attempt?.title || attempt?.id || attempt?.status || attempt)}${attempt?.at ? ` · ${ctx.esc(shortDate(ctx, attempt.at, true))}` : ''}</li>`).join('');
  const session = task.sessionHref || task.session;
  const pr = task.prHref || task.pr;
  const job = task.jobHref || task.job;
  return `${status(ctx, task.status)}${textBlock(ctx, task.description)}${fields(ctx, [
    [ctx.t('Assignee', 'Исполнитель'), agentName(ctx, task.assignee)], [ctx.t('Project', 'Проект'), task.project],
    [ctx.t('Board', 'Доска'), task.board], [ctx.t('Namespace', 'Пространство имён'), task.tenant],
    [ctx.t('Team', 'Команда'), task.team], [ctx.t('Last update', 'Обновление'), shortDate(ctx, task.updatedAt, true)],
    [ctx.t('Session', 'Сессия'), safeLink(task.session) ? null : task.session],
    [ctx.t('Pull request', 'Пул-реквест'), safeLink(task.pr) ? null : task.pr],
    [ctx.t('Job', 'Задание'), safeLink(task.job) ? null : task.job],
    [ctx.t('Dependencies', 'Зависимости'), taskDeps.count],
    [ctx.t('Attempts', 'Попытки'), Array.isArray(task.attempts) ? task.attempts.length : task.attempts],
  ])}${dependencies ? `<section class="hv-detail-section"><h3>${ctx.esc(ctx.t('Dependencies', 'Зависимости'))}</h3><ul>${dependencies}</ul></section>` : ''}${attempts ? `<section class="hv-detail-section"><h3>${ctx.esc(ctx.t('Attempt history', 'История попыток'))}</h3><ul>${attempts}</ul></section>` : ''}<div class="hv-links">${link(ctx, task.href, ctx.t('Open native task', 'Открыть задачу'))}${link(ctx, session, ctx.t('Open session', 'Открыть сессию'))}${link(ctx, pr, ctx.t('Open pull request', 'Открыть пул-реквест'))}${link(ctx, job, ctx.t('Open job', 'Открыть задание'))}</div>`;
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
  const columns = lanes.filter(([key]) => !['archived', 'other'].includes(key) || filtered.some((task) => lane(task.status) === key));
  const board = columns.map(([key, en, ru, tone]) => {
    const items = filtered.filter((task) => lane(task.status) === key);
    const cards = items.map((task) => {
      const dependencies = taskDependencies(task.dependsOn).count;
      const attempts = Array.isArray(task.attempts) ? task.attempts.length : valueOf(task.attempts);
      return `<button type="button" class="hv-task" data-hv-inspect="tasks:${tasks.indexOf(task)}"><span class="hv-task-top"><span>${display(ctx, task.id)}</span>${status(ctx, task.status)}</span><strong>${display(ctx, task.title)}</strong><span class="hv-task-tags">${tags(ctx, [agentName(ctx, task.assignee), task.project])}</span>${dependencies || attempts !== null ? `<span class="hv-task-meta">${dependencies ? `${ctx.esc(ctx.t('Dependencies', 'Зависимости'))} · ${dependencies}` : ''}${dependencies && attempts !== null ? '<span aria-hidden="true"> / </span>' : ''}${attempts !== null ? `${ctx.esc(ctx.t('Attempts', 'Попытки'))} · ${attempts}` : ''}</span>` : ''}</button>`;
    }).join('');
    return `<section class="hv-lane hv-tone-${tone}"><header><span aria-hidden="true"></span><h3>${ctx.esc(ctx.t(en, ru))}</h3><small>${items.length}</small></header><div>${cards || `<p class="hv-lane-empty">${ctx.esc(ctx.t('No tasks', 'Нет задач'))}</p>`}</div></section>`;
  }).join('');
  return `<div class="hv-page">${toolbar(ctx, filter, ctx.t('Search tasks and assignees…', 'Поиск по задачам и исполнителям…'), [
    select(ctx, filter, 'status', ctx.t('Status', 'Статус'), optionsFor(tasks, 'status', (value) => label(ctx, value))),
    select(ctx, filter, 'board', ctx.t('Board', 'Доска'), optionsFor(tasks, 'board')),
    tasks.some(task => task.team) ? select(ctx, filter, 'team', ctx.t('Team', 'Команда'), optionsFor(tasks, 'team')) : '',
    select(ctx, filter, 'project', ctx.t('Project', 'Проект'), optionsFor(tasks, 'project')),
    select(ctx, filter, 'assignee', ctx.t('Assignee', 'Исполнитель'), optionsFor(tasks, 'assignee', (value) => agentName(ctx, value))),
  ])}${progress ? panel(ctx, ctx.t('Project progress', 'Прогресс проектов'), `<div class="hv-projects">${progress}</div>`) : ''}${panel(ctx, ctx.t('Hermes Kanban', 'Канбан Hermes'), filtered.length ? `<div class="hv-board" id="hv-board" tabindex="0" role="region" aria-label="${ctx.esc(ctx.t('Kanban columns', 'Колонки канбана'))}">${board}</div>` : dataEmpty(ctx, tasks, ctx.t('No Kanban tasks', 'Нет задач в канбане'), ctx.t('Tasks appear here when a Hermes Kanban source is available.', 'Задачи появятся здесь, когда станет доступен источник канбана Hermes.')), `${filtered.length} / ${tasks.length}`)}</div>`;
}

function sum(rows, key) {
  const measured = rows.map((row) => valueOf(row[key])).filter((value) => value !== null);
  return measured.length ? measured.reduce((total, value) => total + value, 0) : null;
}

function mean(rows, key) {
  const measured = rows.map((row) => valueOf(row[key])).filter((value) => value !== null);
  return measured.length ? measured.reduce((total, value) => total + value, 0) / measured.length : null;
}

function coverage(ctx, rows, key) {
  const count = rows.filter((row) => valueOf(row[key]) !== null).length;
  return count ? `${count} / ${rows.length} ${ctx.t('records measured', 'записей с данными')}` : ctx.t('Unavailable from sources', 'Нет данных от источников');
}

function chart(ctx, rows, series, unit = '') {
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
  const traces = activeSeries.map(([key, title, tone]) => {
    let drawing = false;
    let path = '';
    const nodes = points.map((row) => {
      const value = valueOf(row[key]);
      if (value === null) {
        drawing = false;
        return '';
      }
      const pointX = x(row.at);
      const pointY = y(value);
      path += `${drawing ? 'L' : 'M'}${pointX} ${pointY} `;
      drawing = true;
      const tooltip = `${title} · ${num(ctx, value, unit, 2)} · ${shortDate(ctx, row.at, true)}`;
      return `<circle cx="${pointX}" cy="${pointY}" r="3.5" tabindex="0" role="img" aria-label="${ctx.esc(tooltip)}" data-hv-point="${ctx.esc(tooltip)}"><title>${ctx.esc(tooltip)}</title></circle>`;
    }).join('');
    return `<g class="hv-trace hv-tone-${tone}"><path d="${path}"/>${nodes}</g>`;
  }).join('');
  const legend = series.map(([key, title, tone]) => `<span class="hv-tone-${tone}"><i aria-hidden="true"></i>${ctx.esc(title)}${activeSeries.some(([item]) => key === item) ? '' : ` · ${ctx.esc(ctx.t('unavailable', 'нет данных'))}`}</span>`).join('');
  return `<div class="hv-chart"><svg viewBox="0 0 ${width} ${height}" aria-label="${ctx.esc(series.map(([, title]) => title).join(', '))}"><g class="hv-chart-grid">${grid}<text x="${left}" y="${height - 5}">${ctx.esc(shortDate(ctx, start, true))}</text><text x="${right}" y="${height - 5}" text-anchor="end">${ctx.esc(start === end ? direction : shortDate(ctx, end, true))}</text></g>${traces}</svg><div class="hv-chart-foot"><div class="hv-legend">${legend}</div><output class="hv-chart-tip" aria-live="polite"></output></div></div>`;
}

function dailyRows(rows, series) {
  const days = new Map();
  for (const row of rows) {
    if (!Number.isFinite(timestamp(row.at))) continue;
    const day = new Date(row.at);
    day.setHours(0, 0, 0, 0);
    const key = day.getTime();
    if (!days.has(key)) days.set(key, []);
    days.get(key).push(row);
  }
  return [...days].map(([at, records]) => ({at, ...Object.fromEntries(series.map(([key]) => [key, sum(records, key)]))}));
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
  const min = filter.period ? Date.now() - Number(filter.period) * 86400000 : -Infinity;
  const filtered = latest(usage, 'at').filter((row) => match(row, filter, ['agent', 'project', 'model', 'task', 'session'], [agentName(ctx, row.agent), row.model, row.project, row.task, row.session]) && (!filter.period || timestamp(row.at) >= min));
  const tokenSeries = [
    ['input', ctx.t('Input', 'Вход'), 'accent'], ['output', ctx.t('Output', 'Выход'), 'green'],
    ['cached', ctx.t('Cache', 'Кеш'), 'amber'], ['reasoning', ctx.t('Reasoning', 'Рассуждение'), 'violet'],
  ];
  const headline = usageDimensions(ctx).slice(0, 4).map(([key, title], index) => stat(ctx, title, num(ctx, sum(filtered, key)), coverage(ctx, filtered, key), tokenSeries[index][2])).join('');
  const metrics = usageDimensions(ctx).slice(4).map(([key, title, unit]) => {
    const averaged = ['ttftMs', 'tokensPerSecond', 'durationMs', 'contextTokens'].includes(key);
    const measured = averaged ? mean(filtered, key) : sum(filtered, key);
    return stat(ctx, title, num(ctx, measured, unit, averaged ? 2 : 0), `${measured !== null && averaged ? `${ctx.t('Mean', 'Среднее')} · ` : ''}${coverage(ctx, filtered, key)}`);
  }).join('');
  const headers = [ctx.t('Time', 'Время'), ctx.t('Scope', 'Агент и контекст'), ctx.t('Model', 'Модель'), ctx.t('Input', 'Вход'), ctx.t('Output', 'Выход'), ctx.t('Requests', 'Запросы'), ''];
  const body = filtered.map((row) => `<tr><td>${ctx.esc(shortDate(ctx, row.at, true))}</td><td><strong>${display(ctx, agentName(ctx, row.agent))}</strong><small>${display(ctx, row.project || row.task || row.session)}</small></td><td>${display(ctx, row.model)}</td><td>${ctx.esc(num(ctx, row.input))}</td><td>${ctx.esc(num(ctx, row.output))}</td><td>${ctx.esc(num(ctx, row.requests))}</td><td><button class="hv-row-action" type="button" aria-label="${ctx.esc(ctx.t('Inspect usage record', 'Открыть запись использования'))}" data-hv-inspect="usage:${usage.indexOf(row)}">↗</button></td></tr>`).join('');
  const records = filtered.length ? `<div class="hv-table-wrap"><table class="hv-table"><thead><tr>${headers.map((title) => `<th>${ctx.esc(title)}</th>`).join('')}</tr></thead><tbody>${body}</tbody></table></div>` : dataEmpty(ctx, usage, ctx.t('No usage records', 'Нет записей об использовании'), ctx.t('Connect a Hermes source with usage telemetry to see measurements here.', 'Здесь появятся измерения из источника телеметрии Hermes.'));
  return `<div class="hv-page">${toolbar(ctx, filter, ctx.t('Search usage by agent or task…', 'Поиск по агентам и задачам…'), [
    select(ctx, filter, 'period', ctx.t('Last activity', 'Последняя активность'), [['1', ctx.t('Last 24 hours', 'Последние 24 часа')], ['7', ctx.t('Last 7 days', 'Последние 7 дней')], ['30', ctx.t('Last 30 days', 'Последние 30 дней')]], ctx.t('Any time', 'За всё время')),
    select(ctx, filter, 'agent', ctx.t('Agent', 'Агент'), optionsFor(usage, 'agent', (value) => agentName(ctx, value))),
    select(ctx, filter, 'project', ctx.t('Project', 'Проект'), optionsFor(usage, 'project')),
    select(ctx, filter, 'model', ctx.t('Model', 'Модель'), optionsFor(usage, 'model')),
    select(ctx, filter, 'task', ctx.t('Task', 'Задача'), optionsFor(usage, 'task')),
    select(ctx, filter, 'session', ctx.t('Session', 'Сессия'), optionsFor(usage, 'session')),
  ])}<div class="hv-stats">${headline}</div>${panel(ctx, ctx.t('Token counts by activity date', 'Токены по дате активности'), `${chart(ctx, dailyRows(filtered, tokenSeries), tokenSeries)}<p class="hv-note">${ctx.esc(ctx.t('Hermes reports cumulative usage per session or model. The chart groups records by the date of their last activity.', 'Hermes передаёт накопленные значения по сессии или модели. На графике записи сгруппированы по дате последней активности.'))}</p>`)}${panel(ctx, ctx.t('Performance and reliability', 'Производительность и стабильность'), `<div class="hv-metrics">${metrics}</div>`)}${panel(ctx, ctx.t('Usage records', 'Записи об использовании'), records, `${filtered.length} / ${usage.length}`)}</div>`;
}

function gpuMetric(ctx, name, value, unit, progress, tone) {
  const measured = valueOf(progress);
  const width = measured === null ? 0 : Math.min(100, Math.max(0, measured));
  return `<div class="hv-gpu-metric hv-tone-${tone}"><span>${ctx.esc(name)}</span><strong>${ctx.esc(num(ctx, value, unit, 1))}</strong>${measured !== null ? `<span class="hv-gauge" aria-hidden="true"><i style="width:${width}%"></i></span>` : ''}</div>`;
}

function gpuCharts(ctx, gpu) {
  const history = list(gpu.history);
  return [
    ['utilization', ctx.t('GPU utilization', 'Загрузка GPU'), 'accent', '%'],
    ['memoryUsed', ctx.t('Used VRAM', 'Занято VRAM'), 'violet', 'MiB'],
    ['temperature', ctx.t('Temperature', 'Температура'), 'amber', '°C'],
    ['power', ctx.t('Power', 'Мощность'), 'green', ctx.t('W', 'Вт')],
  ].map(([key, title, tone, unit]) => panel(ctx, title, chart(ctx, history, [[key, title, tone]], unit))).join('');
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
  return `${status(ctx, service.status)}${fields(ctx, [
    [ctx.t('Identifier', 'Идентификатор'), service.id],
    [ctx.t('Last update', 'Обновление'), shortDate(ctx, service.updatedAt || service.at, true)],
  ])}${textBlock(ctx, service.detail)}${link(ctx, service.href, ctx.t('Open source', 'Открыть источник'))}`;
}

function infrastructureView(ctx, filter) {
  const gpus = list(ctx.data.gpus);
  const services = list(ctx.data.services);
  const sources = list(ctx.data.sources);
  const metrics = ctx.data.metrics || {};
  const chosen = gpus.find((gpu) => String(gpu.id) === filter.gpu) || gpus[0];
  const engine = [
    ['running', ctx.t('Running requests', 'Выполняются'), '', 'accent'],
    ['waiting', ctx.t('Waiting requests', 'В очереди'), '', 'amber'],
    ['kvCache', ctx.t('KV cache usage', 'Загрузка KV-кеша'), '%', 'violet'],
    ['throughput', ctx.t('Generation throughput', 'Пропускная способность'), ctx.t('tok/s', 'ток/с'), 'green'],
  ].map(([key, title, unit, tone]) => stat(ctx, title, num(ctx, metrics[key], unit, 2), valueOf(metrics[key]) === null ? ctx.t('Unavailable from sources', 'Нет данных от источников') : ctx.t('Reported by inference engine', 'По данным движка инференса'), tone)).join('');
  const serviceRows = (items, key) => items.map((row, index) => `<button class="hv-service" type="button" data-hv-inspect="${key}:${index}"><span class="hv-service-icon" aria-hidden="true">${ctx.icon?.(key === 'sources' ? 'database' : 'activity', 17) || '◇'}</span><span class="hv-service-main"><strong>${display(ctx, row.label || row.name || row.id)}</strong>${typeof row.detail === 'string' ? `<small>${display(ctx, row.detail)}</small>` : ''}</span>${status(ctx, row.status)}<span class="hv-service-arrow" aria-hidden="true">↗</span></button>`).join('');
  const choices = gpus.length > 1 ? select(ctx, {gpu: String(chosen.id)}, 'gpu', ctx.t('GPU', 'GPU'), gpus.map((gpu) => [String(gpu.id), `GPU ${gpu.id} · ${gpu.name || 'NVIDIA'}`]), '') : '';
  const ttft = valueOf(metrics.ttftMs) === null ? ctx.t('Unavailable', 'Недоступно') : num(ctx, metrics.ttftMs, ctx.t('ms', 'мс'), 1);
  const gpuPanel = panel(ctx, ctx.t('GPU telemetry', 'Телеметрия GPU'), gpus.length
    ? `<div class="hv-gpus">${gpus.map((gpu, index) => gpuCard(ctx, gpu, index)).join('')}</div>`
    : empty(ctx, ctx.t('No GPU samples', 'Нет данных GPU'), ctx.t('No source has reported GPU measurements.', 'Источники пока не передали измерения GPU.')));
  const servicePanel = panel(ctx, ctx.t('Services', 'Сервисы'), services.length
    ? `<div class="hv-services">${serviceRows(services, 'services')}</div>`
    : empty(ctx, ctx.t('No service status', 'Нет статусов сервисов'), ctx.t('Service health depends on the configured Hermes sources.', 'Статусы сервисов зависят от настроенных источников Hermes.')));
  const sourcePanel = panel(ctx, ctx.t('Data sources', 'Источники данных'), sources.length
    ? `<div class="hv-services">${serviceRows(sources, 'sources')}</div>`
    : empty(ctx, ctx.t('No source status', 'Нет статусов источников'), ctx.t('Source health appears after the next successful refresh.', 'Статусы источников появятся после обновления.')));
  const history = chosen ? `<div class="hv-history-head"><h2>${ctx.esc(ctx.t('GPU history', 'История GPU'))} <span>${display(ctx, chosen.name || chosen.id)}</span></h2>${choices}</div><div class="hv-split">${gpuCharts(ctx, chosen)}</div>` : '';
  return `<div class="hv-page"><div class="hv-stats">${engine}</div><p class="hv-engine-extra">${ctx.esc(ctx.t('Engine TTFT, recent mean', 'Средний TTFT движка за последний интервал'))} · <strong>${ctx.esc(ttft)}</strong></p>${gpuPanel}<div class="hv-split">${servicePanel}${sourcePanel}</div>${history}</div>`;
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

export function bindView(view, ctx) {
  const scope = ctx.root.querySelector('.hcc-content');
  if (!scope || !Object.hasOwn(renderers, view)) return;
  const rendered = {...ctx, data: ctx.data};
  for (const control of scope.querySelectorAll('[data-hv-filter]')) {
    const key = control.dataset.hvFilter;
    if (key === 'query') {
      const refreshResults = (event) => {
        ctx.filters(view).query = control.value;
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
        ctx.filters(view)[key] = control.value;
        ctx.update();
      });
    }
  }
  for (const button of scope.querySelectorAll('[data-hv-inspect]')) {
    const [key, position] = button.dataset.hvInspect.split(':');
    if (!Object.hasOwn(inspectors, key)) continue;
    const row = list(rendered.data[key])[Number(position)];
    if (!row) continue;
    const identity = row.id ?? [row.session, row.agent, row.model, row.task, row.at].filter((value) => value != null).join('/');
    button.id = `hv-inspect-${key}-${identity === '' ? position : identity}`;
    button.addEventListener('click', () => {
      const [title, contents] = inspectors[key];
      ctx.detail(title(rendered, row), contents(rendered, row));
    });
  }
  for (const point of scope.querySelectorAll('[data-hv-point]')) {
    const tip = point.closest('.hv-chart')?.querySelector('.hv-chart-tip');
    if (!tip) continue;
    const show = () => { tip.textContent = point.dataset.hvPoint; };
    const hide = () => { tip.textContent = ''; };
    point.addEventListener('pointerenter', show);
    point.addEventListener('pointerleave', hide);
    point.addEventListener('focus', show);
    point.addEventListener('blur', hide);
  }
  const board = scope.querySelector('#hv-board');
  if (board) {
    const filter = ctx.filters(view);
    board.scrollLeft = filter.boardScroll || 0;
    return () => { filter.boardScroll = board.scrollLeft; };
  }
}
