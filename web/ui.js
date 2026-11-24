const icons = {
  logo: '<path d="M12 2 21 7v10l-9 5-9-5V7zM3 7l9 5 9-5M12 12v10M7.5 4.5l9 5v6"/>',
  graph: '<circle cx="12" cy="12" r="3"/><circle cx="5" cy="4" r="2"/><circle cx="19" cy="5" r="2"/><circle cx="4" cy="19" r="2"/><circle cx="19" cy="20" r="2"/><path d="m7 6 3 4m4 0 3-3M6 18l4-4m4 0 3 4"/>',
  team: '<circle cx="9" cy="8" r="3"/><path d="M3 20v-2a5 5 0 0 1 5-5h2a5 5 0 0 1 5 5v2m0-14a3 3 0 0 1 0 6m3 2a4 4 0 0 1 3 4v2"/>',
  communications: '<path d="M4 4h16a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-7l-5 4v-4H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2zM6 9h12M6 13h8"/>',
  missions: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16m6-16v16M6 8h0m6 0h0m6 0h0M6 12h0m6 0h0"/>',
  usage: '<path d="M3 3v17h18M7 15v-4m5 4V7m5 8v-6m4 6V4"/>',
  infrastructure: '<rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/><path d="M7 7.5h0m0 9h0m4-9h6m-6 9h6"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19m0-14-1.5 1.5m-11 11L5 19"/>',
  moon: '<path d="M20.5 14a8.8 8.8 0 0 1-10.4-10.5A8.9 8.9 0 1 0 20.5 14Z"/>',
  refresh: '<path d="M20 7v5h-5M4 17v-5h5m11 0a8 8 0 0 0-14-5m-2 5a8 8 0 0 0 14 5"/>',
  arrow: '<path d="M4 12h15m-6-6 6 6-6 6"/>',
  external: '<path d="M14 3h7v7m0-7-9 9m7 1v7H4V5h7"/>',
  chevron: '<path d="m7 10 5 5 5-5"/>',
  close: '<path d="m6 6 12 12M18 6 6 18"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  minus: '<path d="M5 12h14"/>',
  fit: '<path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5M9 9h6v6H9z"/>',
  expand: '<path d="M14 4h6v6M20 4l-6 6M10 20H4v-6m0 6 6-6"/>',
  activity: '<path d="M2 12h5l3-7 4 14 3-7h5"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  filter: '<path d="M3 5h18l-7 8v5l-4 2v-7z"/>',
  task: '<rect x="5" y="4" width="14" height="17" rx="2"/><path d="M9 4V2h6v2m-6 6h6m-6 5h6"/>',
  box: '<path d="m12 3 9 5-9 5-9-5zm-9 5v8l9 5 9-5V8m-9 5v8"/>',
  terminal: '<rect x="2" y="4" width="20" height="16" rx="2"/><path d="m6 9 3 3-3 3m6 0h5"/>',
  check: '<path d="m5 12 4 4 10-10"/>',
  alert: '<path d="M12 3 2 20h20zM12 9v4m0 3h0"/>',
  search: '<circle cx="10" cy="10" r="6"/><path d="m15 15 5 5"/>',
  layers: '<path d="m12 3 9 5-9 5-9-5zm-9 9 9 5 9-5m-18 4 9 5 9-5"/>',
  chip: '<rect x="5" y="5" width="14" height="14" rx="2"/><path d="M9 9h6v6H9zM9 2v3m6-3v3m-6 14v3m6-3v3M2 9h3m-3 6h3m14-6h3m-3 6h3"/>',
  database: '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>',
};

export const icon = (name, size = 18) => `<svg aria-hidden="true" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">${icons[name] || icons.box}</svg>`;

const entities = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
export const esc = value => String(value ?? '').replace(/[&<>"']/g, char => entities[char]);

export const timestamp = value => {
  if (value == null || value === '') return NaN;
  if (typeof value === 'number') return value < 1e12 ? value * 1000 : value;
  return Date.parse(value);
};

const palettes = ['blue', 'violet', 'green', 'amber'];
function avatarHash(agent) {
  const identity = agent.kind === 'profile' ? agent.profile || agent.id : agent.id || agent.name;
  let hash = 2166136261;
  for (const char of identity || '') hash = Math.imul(hash ^ char.codePointAt(0), 16777619);
  return hash >>> 0;
}

export function color(agent) {
  return palettes.includes(agent.color) ? agent.color : palettes[avatarHash(agent) % palettes.length];
}

export function avatar(agent, size = 34) {
  const variant = color(agent);
  const native = {blue: 216, violet: 261, green: 157, amber: 34};
  const hue = palettes.includes(agent.color) ? native[variant] : avatarHash(agent) % 360;
  return `<span class="hcc-avatar avatar-${variant}" style="--avatar-size:${Number(size) || 34}px;--avatar:hsl(${hue} 84% 71%);--avatar-turn:${hue-native[variant]}deg" aria-hidden="true"><span></span></span>`;
}

const labels = {
  working: ['Working', 'В работе'], running: ['Running', 'В работе'], active: ['Active', 'Активен'],
  generating: ['Generating', 'Генерация'], tool: ['Using tool', 'Выполняет инструмент'],
  waiting: ['Waiting', 'Ожидает'], idle: ['Idle', 'Свободен'], blocked: ['Blocked', 'Заблокирован'],
  complete: ['Completed', 'Завершено'], done: ['Done', 'Готово'], completed: ['Completed', 'Завершено'],
  approved: ['Approved', 'Принято'], error: ['Error', 'Ошибка'], failed: ['Failed', 'Ошибка'],
  stalled: ['Possibly stalled', 'Возможное зависание'], unknown: ['Unknown', 'Неизвестно'],
  online: ['Online', 'Доступен'], available: ['Available', 'Доступно'], healthy: ['Healthy', 'В норме'],
  partial: ['Partial', 'Частично'], unavailable: ['Unavailable', 'Недоступно'],
  offline: ['Offline', 'Отключён'], ready: ['Ready', 'Готово к запуску'],
  review: ['In review', 'На проверке'], todo: ['To do', 'К выполнению'],
  in_progress: ['In progress', 'В работе'], canceled: ['Canceled', 'Отменено'],
  cancelled: ['Canceled', 'Отменено'], triage: ['Triage', 'Разбор'],
  scheduled: ['Scheduled', 'Запланировано'], archived: ['Archived', 'Архив'],
  ended: ['Ended', 'Завершено'], paused: ['Paused', 'Приостановлено'],
  interrupted: ['Interrupted', 'Прервано'], queued: ['Queued', 'В очереди'],
};

export function statusLabel(value, t) {
  const normalized = String(value || 'unknown').toLowerCase();
  return labels[normalized] ? t(...labels[normalized]) : normalized.replaceAll('_', ' ');
}

export function statusChip(value, t) {
  const normalized = String(value || 'unknown').toLowerCase();
  return `<span class="hcc-status state-${esc(normalized.replace(/[^a-z_]/g, ''))}"><i></i>${esc(statusLabel(normalized, t))}</span>`;
}

export function relTime(value, locale) {
  const elapsed = (timestamp(value) - Date.now()) / 1000;
  if (!Number.isFinite(elapsed)) return '—';
  const rel = new Intl.RelativeTimeFormat(locale, { numeric: 'auto', style: 'short' });
  if (Math.abs(elapsed) < 60) return rel.format(Math.round(elapsed), 'second');
  if (Math.abs(elapsed) < 3600) return rel.format(Math.round(elapsed / 60), 'minute');
  if (Math.abs(elapsed) < 86400) return rel.format(Math.round(elapsed / 3600), 'hour');
  return rel.format(Math.round(elapsed / 86400), 'day');
}

export function safeHref(value) {
  if (typeof value !== 'string' || !value) return '';
  try {
    const parsed = new URL(value, document.baseURI);
    if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password) return '';
    return parsed.href;
  } catch {
    return '';
  }
}
