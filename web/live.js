const extensionDirectory = '/extensions/hermes-command-center/';
const host = new URL(document.baseURI);
const prefix = host.pathname.includes(extensionDirectory)
  ? host.pathname.slice(0, host.pathname.indexOf(extensionDirectory) + 1)
  : '/';
const root = new URL(prefix, host.origin);
const extensions = 'api/extensions/hermes-command-center/sidecar/';

export const emptyData = () => ({
  agents: [], runs: [], issues: [], links: [], events: [], tasks: [], usage: [], gpus: [], services: [], metrics: {}, sources: [],
  history: null, access: false, retryable: false, at: null,
});

function source(id, label, status, detail, detailRu) {
  return { id, label, status, detail, detailRu };
}

export function webuiHref(path) {
  const link = new URL(path, root);
  return link.origin === host.origin ? link.href : '';
}

async function request(path) {
  const response = await fetch(webuiHref(path), {
    signal: AbortSignal.timeout(6500), cache: 'no-store', credentials: 'same-origin',
    headers: { Accept: 'application/json' }, redirect: 'manual',
  });
  if (!response.ok) {
    const status = response.status;
    let reason;
    if ([404, 409, 503].includes(status)) reason = (await response.json().catch(() => null))?.error;
    const denied = (status === 404 && reason === 'Extension not found') ||
      (status === 409 && ['Extension manifest is not loaded', 'Extension sidecar proxy is unavailable'].includes(reason)) ||
      (status === 503 && ['authentication_unavailable', 'Sidecar proxy auth token is unavailable'].includes(reason));
    throw Object.assign(new Error('Source unavailable'), { status, denied });
  }
  return response.json();
}

function transient(error) {
  return [429, 500, 502, 504].includes(error?.status) ||
    ['TimeoutError', 'AbortError', 'TypeError'].includes(error?.name);
}

export function isAccessDenied(error) {
  const status = error?.status;
  return error?.denied === true || [0, 401, 403].includes(status) || (status >= 300 && status < 400);
}

function profileAgent(row) {
  const name = typeof row?.name === 'string' && /^[\p{L}\p{N}_.-]{1,120}$/u.test(row.name) ? row.name : null;
  if (!name) return null;
  const model = typeof row.model === 'string' && /^[a-zA-Z0-9_.:/+-]{1,120}$/.test(row.model) &&
    !/(?:sk[-_]|gh[pousr]_|github_pat_|glpat-|AIza|hf_|xox[baprs]-|AKIA|eyJ)[a-zA-Z\d_.-]{8,}/i.test(row.model) &&
    !row.model.includes('://') ? row.model : null;
  return { id: `profile:${name}`, name, kind: 'profile', role: 'Hermes', profile: name,
    model, status: 'unknown' };
}

export function sidecarError(error) {
  const base = ['hcc', 'Command Center sidecar', 'unavailable'];
  if (error?.status === 403) {
    return source(...base, 'Enable the authenticated sidecar proxy in WebUI Settings → Extensions.',
      'Разрешите прокси для sidecar в WebUI → Настройки → Расширения.');
  }
  if (error?.denied) {
    return source(...base, 'Sidecar access could not be verified. Check WebUI extension settings.',
      'Доступ к sidecar не подтверждён. Проверьте расширение в настройках WebUI.');
  }
  if (isAccessDenied(error)) {
    return source(...base, 'Sign in to Hermes WebUI again.', 'Войдите в Hermes WebUI заново.');
  }
  return source(...base, 'The WebUI sidecar route is unavailable. Check the HCC service and proxy status.',
    'Маршрут sidecar недоступен. Проверьте службу HCC и состояние прокси.');
}

const validSession = session => typeof session === 'string' && /^[a-zA-Z0-9_-]{1,120}$/.test(session);
const ranges = new Set(['1h', '24h', '7d', '30d']);

export function readHistory(range) {
  if (!ranges.has(range)) return Promise.reject(new Error('Invalid history range'));
  return request(`${extensions}history/${range}`);
}

export async function readTaskDetail(id) {
  if (typeof id !== 'string' || id.length > 160) throw new Error('Invalid task ID');
  const task = await request(`${extensions}kanban/task?id=${encodeURIComponent(id)}`);
  return { ...task, sessionHref: validSession(task.session) ? webuiHref(`session/${encodeURIComponent(task.session)}`) : task.sessionHref,
    links: Array.isArray(task.links) ? task.links.map(link => link.kind === 'session' && validSession(link.id)
      ? { ...link, href: webuiHref(`session/${encodeURIComponent(link.id)}`) } : link) : [] };
}

export async function readLive() {
  const live = emptyData();
  let profile;
  try {
    profile = await request('api/profiles');
  } catch (error) {
    live.retryable = transient(error);
    live.sources.push(isAccessDenied(error)
      ? source('webui', 'Hermes WebUI', 'unavailable', 'Sign in to Hermes WebUI again.', 'Войдите в Hermes WebUI заново.')
      : source('webui', 'Hermes WebUI', 'unavailable', 'Profiles API did not complete.', 'Запрос API профилей не завершился.'));
    return live;
  }
  if (!Array.isArray(profile?.profiles)) {
    live.sources.push(source('webui', 'Hermes WebUI', 'unavailable', 'Profiles API unavailable.', 'API профилей недоступен.'));
    return live;
  }
  if (profile.single_profile_mode !== false) {
    live.sources.push(source('webui', 'Hermes WebUI', 'unavailable', 'Command Center requires a global, single-operator WebUI.',
      'Для Command Center требуется глобальный WebUI для одного оператора.'));
    return live;
  }
  let snapshot;
  try {
    snapshot = await request(`${extensions}snapshot`);
  } catch (error) {
    live.retryable = transient(error);
    live.sources.push(sidecarError(error));
  }
  if (Array.isArray(snapshot?.events) && Array.isArray(snapshot?.sources) && Array.isArray(snapshot?.agents)) {
    live.access = true;
    live.at = snapshot.at || null;
    for (const key of Object.keys(live)) {
      if (Array.isArray(live[key]) && Array.isArray(snapshot[key])) {
        live[key] = snapshot[key].filter(item => item && typeof item === 'object');
      }
    }
    if (snapshot.metrics && typeof snapshot.metrics === 'object') live.metrics = snapshot.metrics;
    live.sources.push(source('hcc', 'Command Center proxy', 'available', 'Snapshot received through the authenticated WebUI proxy.',
      'Данные получены через авторизованный серверный прокси WebUI.'));
  } else if (!live.sources.length) {
    live.sources.push(source('hcc', 'Command Center proxy', 'unavailable', 'The proxy returned an invalid snapshot.',
      'Прокси вернул некорректный снимок данных.'));
  }
  const normalizeAgent = agent => ({
    ...agent,
    href: agent.href || (agent.kind !== 'pi' && agent.source !== 'pi' && validSession(agent.session)
      ? webuiHref(`session/${encodeURIComponent(agent.session)}`) : null),
  });
  for (const list of ['agents', 'runs', 'issues']) {
    live[list] = live[list].filter(agent => typeof agent.id === 'string' && typeof agent.name === 'string').map(normalizeAgent);
  }
  if (!Array.isArray(snapshot?.runs)) {
    const active = new Set(['running', 'working', 'active', 'tool', 'generating', 'queued', 'stalled']);
    live.runs = live.agents.filter(agent => agent.kind !== 'profile' && !active.has(agent.status));
    live.agents = live.agents.filter(agent => agent.kind === 'profile' || active.has(agent.status));
  }
  live.tasks = live.tasks.map(task => ({
    ...task, sessionHref: validSession(task.session) ? webuiHref(`session/${encodeURIComponent(task.session)}`) : task.sessionHref,
  }));
  const agentsById = new Map([...live.runs, ...live.agents].map(agent => [agent.id, agent]));
  live.events = live.events.map(event => ({
    ...event, href: event.href || agentsById.get(event.from)?.href || agentsById.get(event.to)?.href,
  }));
  const profiles = new Map(live.agents.filter(agent => agent.kind === 'profile').map(agent => [agent.profile || agent.name, agent]));
  for (const row of profile.profiles) {
    const agent = profileAgent(row);
    if (!agent) continue;
    if (profiles.has(agent.profile)) {
      const existing = profiles.get(agent.profile);
      if (!existing.model && agent.model) existing.model = agent.model;
      continue;
    }
    live.agents.push(agent);
    profiles.set(agent.profile, agent);
  }
  for (const agent of live.agents) {
    const owner = agent.kind === 'session' && profiles.get(agent.profile);
    if (owner) live.links.push({ from: owner.id, to: agent.id, kind: 'observed' });
  }
  live.sources.unshift(source('webui', 'Hermes WebUI', 'available', 'Authenticated profiles API.', 'API профилей WebUI.'));
  return live;
}
