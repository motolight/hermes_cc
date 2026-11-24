const HOUR = 3600000;
const bootTime = Date.now();
const names = ['Hermes Core', 'Atlas', 'Nova', 'Sable', 'Relay', 'Kite'];
const ids = ['core', 'atlas', 'nova', 'sable', 'relay', 'kite'];
const roles = ['Coordinator', 'Research profile', 'Engineering profile', 'Review subagent', 'Pi executor', 'Build subagent'];
const kinds = ['profile', 'profile', 'profile', 'subagent', 'pi', 'subagent'];
const models = ['Qwen Flash-Next', 'Qwen Flash-Next', 'Qwen Coder', 'Qwen Coder', 'Qwen Flash-Next', 'Qwen Coder'];
const colors = ['blue', 'violet', 'green', 'violet', 'amber', 'blue'];
const statuses = ['working', 'generating', 'tool', 'stalled', 'working', 'done'];
const projects = ['Observatory', 'Observatory', 'Home Lab', 'Observatory', 'Home Lab', 'Home Lab'];
const taskNames = [
  'Review overnight diagnostics', 'Index model telemetry', 'Validate inference adapter',
  'Inspect reliability report', 'Collect GPU baseline', 'Build integration bundle',
  'Verify sample dashboards', 'Publish test evidence', 'Document service checks',
  'Prepare fallback config', 'Audit event attribution', 'Summarize usage by model',
];
const handoffs = [
  ['core', 'atlas', 'delegation', 'Telemetry index assigned', 'Read the latest sample generation metrics and produce a local index.'],
  ['nova', 'kite', 'message', 'Build artifacts ready', 'Integration fixtures passed the local packaging step.'],
  ['relay', 'nova', 'result', 'GPU baseline returned', 'Four devices sampled with memory, utilization, temperature and power.'],
  ['atlas', 'core', 'message', 'Initial diagnostics delivered', 'The local sample contains short and long context inference measurements.'],
  ['nova', 'relay', 'delegation', 'Baseline collection delegated', 'Collect a snapshot of the synthetic lab fleet.'],
  ['sable', null, 'approval', 'Reliability review requested', 'Review evidence is ready for approval.'],
  ['kite', null, 'tool', 'Integration fixture built', 'The fixture builder completed with exit code zero.'],
  ['core', 'sable', 'delegation', 'Review assigned', 'Check retry reporting and source availability copy.'],
];

function demoGpu(index, now) {
  const history = Array.from({ length: 42 }, (_, step) => {
    const angle = (step + index * 3) / 3.2;
    const utilization = Math.round(48 + Math.sin(angle) * 20 + Math.cos(angle * .43) * 11 + index * 4);
    return { at: now - (41 - step) * 45000, utilization,
      memoryUsed: 5500 + utilization * 162 + index * 310,
      temperature: 39 + Math.round(utilization * .39), power: 49 + Math.round(utilization * 2.3) };
  });
  return { id: String(index), name: 'NVIDIA GeForce RTX 3090', memoryTotal: 24576, ...history.at(-1), history };
}

export function demoData() {
  const now = Date.now();
  const tick = Math.floor((now - bootTime) / 12000);
  const agents = ids.map((id, i) => ({
    id, name: names[i], kind: kinds[i], role: roles[i], model: models[i], color: colors[i], profile: i < 3 ? names[i] : names[i === 3 ? 0 : 2],
    status: statuses[i], task: taskNames[i], project: projects[i], team: i < 4 ? 'Platform' : 'Operations',
    active: i !== 5, lastAction: ['Routed a review', 'Indexing telemetry', 'Read adapter diagnostics', 'Awaiting a tool result', 'Baseline collection started', 'Returned build report'][i],
    issue: i === 3 ? {reason: 'The test worker has been waiting on a tool response.', at: now - 29000, lastActivityAt: now - 18000} : null,
    session: `DEMO-${211 + i}`, startedAt: bootTime - (93 + i * 69) * 1000, updatedAt: now - (5 + i * 8) * 1000,
  }));
  const events = Array.from({ length: 20 }, (_, index) => {
    const [from, to, kind, title, detail] = handoffs[(index + tick) % handoffs.length];
    const agent = agents.find(item => item.id === from);
    return { from, to, kind, title, detail, id: `demo-event-${tick - index}`, thread: index % 3 ? 'demo-telemetry' : 'demo-build',
      channel: kind === 'tool' ? 'technical' : 'communication', body: kind === 'tool' ? null : detail,
      at: bootTime + (tick - index) * 12000, project: agent.project, team: agent.team, session: agent.session };
  });
  const priority = ['running', 'running', 'running', 'review', 'running', 'done', 'todo', 'blocked', 'ready', 'scheduled', 'triage', 'done'];
  const tasks = taskNames.map((title, i) => ({
    id: `OBS-${110 + i}`, title, status: priority[i], assignee: i < 8 ? ids[i % ids.length] : null,
    project: projects[i % projects.length], team: i % 3 ? 'Platform' : 'Operations',
    board: projects[i % projects.length] === 'Observatory' ? 'observatory' : 'home-lab',
    description: 'Synthetic work item for the Command Center walkthrough.',
    dependsOn: i === 7 ? ['OBS-113', 'OBS-115'] : i === 10 ? ['OBS-112'] : [],
    updatedAt: bootTime - i * 710000, attempts: i === 5 ? [{ status: 'done', at: bootTime - 2300000 }] : [],
    session: i < 6 ? agents[i].session : null,
  }));
  const usage = Array.from({ length: 126 }, (_, i) => {
    const agent = agents[i % agents.length];
    const curve = 1.12 + Math.sin(i * .57) * .43 + Math.cos(i * .18) * .31;
    return { at: now - (i * 1.37 + .02) * HOUR, agent: agent.id, project: agent.project, model: agent.model,
      task: tasks[i % tasks.length].id, session: agent.session,
      input: Math.round(curve * 17500 + (i % 9) * 823), output: Math.round(curve * 2700 + i % 8 * 117),
      cached: i % 5 === 0 ? null : Math.round(curve * 3600), reasoning: i % 4 === 0 ? null : Math.round(curve * 670),
      requests: 4 + i % 12, ttftMs: Math.round(245 * curve + i % 6 * 15), tokensPerSecond: 43 + (i * 11 % 29),
      durationMs: 42000 + (i * 13 % 47) * 1000, contextTokens: 6000 + (i * 1031 % 19000),
      errors: Number(i % 17 === 2), retries: Number(i % 17 === 3), orchestration: i % 2 ? Math.round(curve * 570) : null };
  });
  return {
    agents: agents.filter(agent => agent.active), runs: [agents.at(-1)], issues: agents.filter(agent => agent.issue), events, tasks, usage,
    links: [
      { from: 'core', to: 'atlas', kind: 'delegation' }, { from: 'core', to: 'nova', kind: 'observed' },
      { from: 'core', to: 'sable', kind: 'delegation' }, { from: 'nova', to: 'relay', kind: 'delegation' },
      { from: 'relay', to: 'sable', kind: 'delegation' },
    ],
    gpus: Array.from({ length: 4 }, (_, i) => demoGpu(i, now)),
    services: [
      { id: 'webui', name: 'Hermes WebUI', status: 'online', detail: 'Synthetic service status' },
      { id: 'hermes', name: 'Hermes Gateway', status: 'online', detail: 'Synthetic service status' },
      { id: 'pi', name: 'Pi Bridge', status: 'online', detail: 'Synthetic service status' },
      { id: 'kanban', name: 'Hermes Kanban', status: 'online', detail: 'Synthetic service status' },
      { id: 'vllm', name: 'vLLM', status: 'online', detail: 'Synthetic service status' },
    ],
    metrics: { running: 4, waiting: 1, kvCache: 39.2, throughput: 148.6, ttftMs: 284.7 },
    sources: [{ id: 'demo', label: 'DEMO', status: 'available', detail: 'Synthetic data for the interface preview.', detailRu: 'Синтетические данные для просмотра интерфейса.' }],
  };
}

const hoursByRange = {'1h': 1, '24h': 24, '7d': 168, '30d': 720};

export function demoHistory(range) {
  const {gpus} = demoData();
  const end = Date.now();
  const duration = (hoursByRange[range] || 24) * HOUR;
  const points = Array.from({length: 48}, (_, i) => {
    const progress = i / 47;
    const signal = .8 + .32 * Math.sin(i * .62) + .13 * Math.cos(i * .27);
    return {at: end - duration * (1 - progress), signal};
  });
  const data = {
    range, startedAt: points[0].at, lastAt: end, baselineAt: points[0].at,
    baseline: {input: 1980000, output: 247000, cached: 440000, reasoning: 51800, requests: 412},
    coverage: {retentionDays: 31, sampleSeconds: 60, bucketSeconds: Math.round(duration / 47000), counterResets: 0, scopeChanges: 0, limitedObservations: 0},
    usage: points.map(({at, signal}, i) => ({
      at, input: Math.round(signal * 17500), output: Math.round(signal * 2400),
      cached: Math.round(signal * 6100), reasoning: Math.round(Math.max(0, signal - .22) * 550), requests: 4 + i % 6,
    })),
    engine: points.map(({at, signal}, i) => ({
      at, throughput: 153 * signal, ttftMs: 208 + 145 * (1.4 - signal),
      kvCache: 36 + 11 * signal, running: 1 + i % 6, waiting: i % 5 === 0 ? 2 : i % 2,
    })),
    gpus: Object.fromEntries(gpus.map((gpu, index) => [gpu.id, points.map(({at}, i) => {
      const s = .63 + .2 * Math.sin(i * .42 + index) + .11 * Math.cos(i * .19 + index * 2);
      return {at, utilization: Math.round(s * 100), memoryUsed: Math.round(8900 + s * 6500 + index * 750),
        temperature: 38 + Math.round(s * 30) + index, power: 66 + Math.round(s * 164) + index * 13};
    })])),
  };
  return data;
}

export function demoTaskDetail(id) {
  const tasks = demoData().tasks;
  const task = tasks.find(item => item.id === id);
  if (!task) throw new Error('No fixture matches this task.');
  return {...task, criteria: 'Record the measurement source and link each reproducible verification step.',
    description: 'Validate the synthetic observatory data through the native workflow.\n\nVerify that counters preserve their scope across a retry, then attach the input and output token measurements.',
    comments: [{id:'demo-comment',author:'Nova',body:'The metrics shape has been reviewed. Use the new snapshot for the retry.',at:task.updatedAt}],
    events: [{id:'demo-update',kind:'updated',summary:'Synthetic task evidence updated',at:task.updatedAt}],
    dependsOn: task.dependsOn.map(id => tasks.find(other => other.id === id)).filter(Boolean),
    dependents: tasks.filter(other => other.dependsOn.includes(id)),
    blockedReason: task.status === 'blocked' ? 'Waiting on a native dependency to finish.' : null,
    result: task.status === 'done' ? 'The demo fixture records the completed baseline review.' : null,
    limits: [], links: []};
}
