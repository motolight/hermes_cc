import assert from 'node:assert/strict';
import test from 'node:test';

globalThis.document = {baseURI: 'https://webui.test/extensions/hermes-command-center/index.html'};
const {isAccessDenied, readHistory, readLive, readTaskDetail, webuiReturnHref} = await import('../web/live.js');
const globalProfiles = {profiles: [], single_profile_mode: false};
const emptySnapshot = {agents: [], events: [], sources: []};

test('standalone HCC returns only to a same-origin Hermes document', () => {
  const session = 'https://webui.test/session/chat-id?panel=chat';
  assert.equal(webuiReturnHref(session), session);
  assert.equal(webuiReturnHref('https://webui.test/session/team.alpha'), 'https://webui.test/session/team.alpha');
  assert.equal(webuiReturnHref('https://webui.test/sessions'), 'https://webui.test/sessions');
  for (const value of ['', 'https://elsewhere.test/session/chat-id', 'https://webui.test/api/session',
    'https://webui.test/extensions/hermes-command-center/web/index.html', 'https://webui.test/session/manifest.json',
    'https://webui.test/session/static', 'javascript:alert(1)']) {
    assert.equal(webuiReturnHref(value), 'https://webui.test/');
  }
});

function mockRequests(profiles, snapshot) {
  const paths = [];
  globalThis.fetch = async input => {
    const {pathname} = new URL(input);
    paths.push(pathname);
    const value = pathname === '/api/profiles' ? profiles : snapshot;
    if (value instanceof Error) throw value;
    if (value instanceof Response) return value;
    if (typeof value === 'number') return new Response(null, {status: value});
    return Response.json(value);
  };
  return paths;
}

test('transport failures and temporary gateway responses permit a bounded stale display', async () => {
  for (const failure of [new TypeError('offline'), new DOMException('late', 'TimeoutError'), 429, 500, 502, 504]) {
    let paths = mockRequests(failure, emptySnapshot);
    let result = await readLive();
    assert.equal(result.access, false);
    assert.equal(result.retryable, true);
    assert.deepEqual(paths, ['/api/profiles']);

    paths = mockRequests(globalProfiles, failure);
    result = await readLive();
    assert.equal(result.access, false);
    assert.equal(result.retryable, true);
    assert.equal(paths.length, 2);
  }
});

test('refused or unverified scope cannot retain data or reach the sidecar', async () => {
  for (const profiles of [302, 401, 403, {profiles: []}, {...globalProfiles, single_profile_mode: true}]) {
    const paths = mockRequests(profiles, emptySnapshot);
    const result = await readLive();
    assert.equal(result.access, false);
    assert.equal(result.retryable, false);
    assert.deepEqual(paths, ['/api/profiles']);
  }
});

test('proxy denial, unavailable token, and invalid snapshot are not transient', async () => {
  for (const snapshot of [302, 401, 403, 404, 409, 503, {}, [], {error: 'invalid'}]) {
    mockRequests(globalProfiles, snapshot);
    const result = await readLive();
    assert.equal(result.access, false);
    assert.equal(result.retryable, false);
  }
});

test('an authenticated snapshot may legitimately have no events', async () => {
  mockRequests(globalProfiles, emptySnapshot);
  const result = await readLive();
  assert.equal(result.access, true);
  assert.deepEqual(result.events, []);
});

test('WebUI enriches known live profiles and preserves separate Pi topology and history', async () => {
  const current = {id:'profile:default', kind:'profile', profile:'default', name:'default'};
  const worker = {id:'pi-role:worker', kind:'pi-role', name:'worker', active:false};
  const run = {id:'old-pi', kind:'pi', name:'Pi', status:'completed', active:false};
  const route = {at:'2026-11-25T12:00:00Z', nodes:[run], links:[]};
  mockRequests({...globalProfiles, profiles:[{name:'closed'}, {name:'default', model:'local'}]},
    {...emptySnapshot, agents:[current], piAgents:[worker], recentPi:route, recentPiDelegation:route});
  const result = await readLive();
  assert.deepEqual(result.agents.map(node => [node.profile, node.model]), [['default', 'local']]);
  assert.deepEqual(result.piAgents, [worker]);
  assert.equal(result.recentPi.nodes[0].status, 'completed');
  assert.equal(result.recentPiDelegation.at, route.at);
});

test('failed auxiliary reads distinguish lost authorization from an unavailable resource', async () => {
  const errors = [
    [503, 'authentication_unavailable', true],
    [503, 'Sidecar proxy auth token is unavailable', true],
    [409, 'Extension sidecar proxy is unavailable', true],
    [404, 'Extension not found', true],
    [503, 'history_unavailable', false],
    [404, 'not_found', false],
  ];
  for (const [status, error, denied] of errors) {
    for (const read of [() => readHistory('24h'), () => readTaskDetail('default:task')]) {
      mockRequests(globalProfiles, Response.json({error}, {status}));
      await assert.rejects(read, failure => isAccessDenied(failure) === denied);
    }
  }
});
