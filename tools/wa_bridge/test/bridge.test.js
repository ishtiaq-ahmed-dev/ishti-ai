import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { randomBytes } from 'node:crypto';
import { Readable } from 'node:stream';
import { proto } from '@whiskeysockets/baileys';
import { SecureFiles, encryptedAuth } from '../secure-files.js';
import { jidOf, describeMessage, MessageStore, sha256, sendContent, ackName } from '../core.js';
import { createApi } from '../api.js';
import { ConnectionLifecycle, initialBridgeState, logger } from '../runtime.js';
const chat = '15550001111@s.whatsapp.net', token = 'test-token-'.repeat(5);
const message = (id, fromMe = false, body = { conversation: 'Meeting: Tuesday, 10 AM' }) => ({
  key: { remoteJid: chat, id, fromMe }, messageTimestamp: 1700000000, message: body,
});
test('Baileys receives a complete pino logger, including complete child loggers', () => {
  for (const candidate of [logger, logger.child({ test: true })]) {
    assert.equal(typeof candidate.level, 'string');
    for (const name of ['trace', 'debug', 'info', 'warn', 'error', 'fatal', 'child']) assert.equal(typeof candidate[name], 'function');
  }
  assert.doesNotThrow(() => logger.child({ socket: 'fake' }).debug('pair success recv'));
});
test('connection lifecycle restarts once for 515 and retains the paired identity', () => {
  const state = initialBridgeState({ registered: true, me: { id: chat } }), timers = [], reconnects = [];
  const life = new ConnectionLifecycle({ state, reasons: { loggedOut: 401, badSession: 500, connectionReplaced: 440,
    restartRequired: 515, timedOut: 408, connectionLost: 408 }, reconnect: () => reconnects.push(true),
    setTimer: (fn, delay) => { timers.push({ fn, delay }); return timers.length; }, clearTimer: () => {} });
  assert.equal(life.update({ connection: 'close', lastDisconnect: { error: { output: { statusCode: 515 } } } }).action, 'restart');
  assert.equal(state.me, chat); assert.equal(state.pairingState, 'paired'); assert.equal(timers[0].delay, 0);
  timers[0].fn(); assert.equal(reconnects.length, 1);
  assert.equal(life.update({ connection: 'close', lastDisconnect: { error: { output: { statusCode: 515 } } } }).action, 'stop');
});
test('QR timeout reports expiry, backs off, refreshes, and stops at the retry cap', () => {
  let now = 100, reconnects = 0; const state = initialBridgeState(), timers = [];
  const life = new ConnectionLifecycle({ state, reasons: { loggedOut: 401, badSession: 500, connectionReplaced: 440,
    restartRequired: 515, timedOut: 408, connectionLost: 408 }, reconnect: () => reconnects++, maxQrRetries: 2,
    clock: () => now, setTimer: (fn, delay) => { timers.push({ fn, delay }); return timers.length; }, clearTimer: () => {} });
  let result = life.update({ connection: 'close', lastDisconnect: { error: { output: { statusCode: 408 } } } });
  assert.equal(result.action, 'retry_qr'); assert.equal(result.delay, 2000); assert.equal(state.pairingState, 'qr_expired');
  timers.at(-1).fn(); assert.equal(reconnects, 1);
  now = 200; life.update({ qr: 'fresh' }); assert.equal(state.pairingState, 'waiting_for_scan'); assert.equal(state.qrAt, 200);
  result = life.update({ connection: 'close', lastDisconnect: { error: { output: { statusCode: 408 } } } });
  assert.equal(result.delay, 4000);
  result = life.update({ connection: 'close', lastDisconnect: { error: { output: { statusCode: 408 } } } });
  assert.equal(result.action, 'stop'); assert.match(state.lastError, /expired/i);
});
async function harness(t, options = {}) {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'ishti-wa-test-'));
  const files = new SecureFiles(dir, randomBytes(32)), store = new MessageStore();
  let calls = 0, readKeys = null;
  const sock = { user: { id: '15550001111:user@example.com' },
    sendMessage: async (destination, content) => { calls++; return { ...message('TEST_ID_' + calls, true, { conversation: content.text }), key: { remoteJid: destination, id: 'TEST_ID_' + calls, fromMe: true } }; },
    readMessages: async keys => { readKeys = keys; }, ...options.sock };
  const server = http.createServer();
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  t.after(async () => { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); await fs.rm(dir, { recursive: true, force: true }); });
  const api = await createApi({ token, port, files, store, state: { connected: true }, socket: () => sock,
    outbox: { readBytes: async () => null }, download: async () => Readable.from([Buffer.from('%PDF-test')]), sendTimeout: 20, ...options });
  server.on('request', api.app);
  const call = (url, payload, headers = {}) => new Promise((resolve, reject) => {
    const body = payload === undefined ? undefined : JSON.stringify(payload);
    const request = http.request({ hostname: '127.0.0.1', port, path: url, method: body === undefined ? 'GET' : 'POST',
      headers: { Authorization: 'Bearer ' + token, ...(body === undefined ? {} : { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) }), ...headers } }, response => {
      const chunks = []; response.on('data', chunk => chunks.push(chunk));
      response.on('end', () => { const raw = Buffer.concat(chunks).toString(); resolve({ code: response.statusCode,
        body: response.headers['content-type']?.includes('application/json') ? JSON.parse(raw) : raw }); });
    }); request.on('error', reject); request.end(body);
  });
  return { call, store, api, files, sock, calls: () => calls, readKeys: () => readKeys };
}
test('exact numbers and IDs; never fabricate a name or accept a partial number', () => {
  assert.equal(jidOf('+1 (555) 000-1111'), chat);
  for (const invalid of ['', 'Brother', '1555', '../secret', 'https://host/file', '15550001111:user@example.com']) assert.throws(() => jidOf(invalid));
});
test('message text/captions and restricted wrappers remain data', () => {
  assert.equal(describeMessage({ conversation: 'Meeting: 10 AM' }).text, 'Meeting: 10 AM');
  assert.equal(describeMessage({ documentWithCaptionMessage: { message: { documentMessage: { fileName: 'invoice.pdf', caption: 'invoice' } } } }).kind, 'document');
  assert.equal(describeMessage({ viewOnceMessage: { message: { imageMessage: { caption: 'secret' } } } }).restricted, true);
  assert.equal(describeMessage({ ephemeralMessage: { message: { conversation: 'private' } } }).text, '[restricted media]');
});
test('incoming store deduplicates unread rows, keeps own direction and exact IDs', () => {
  const store = new MessageStore(); store.ingest(message('A')); store.ingest(message('A')); store.ingest(message('B', true));
  assert.equal(store.list()[0].unread, 1); assert.equal(store.messages(chat).length, 2);
  assert.equal(store.messages(chat)[1].fromMe, true);
});
test('LID message envelopes and contacts retain the phone-number mapping', () => {
  const store = new MessageStore(), lid = '123456789012345@lid';
  store.updateContacts([{ id: lid, lid, jid: chat, name: 'Owner' }]);
  const row = store.ingest({ ...message('LID_1'), key: { remoteJid: lid, id: 'LID_1', fromMe: false,
    senderLid: lid, senderPn: chat } });
  assert.equal(row.senderJid, chat); assert.equal(row.senderLid, lid);
  assert.equal(store.resolvePhone(lid), chat);
});
test('store bounds and TTL evict raw payloads too', () => {
  let clock = 100; const store = new MessageStore({ maxMessages: 2, maxChats: 1, ttl: 50, clock: () => clock });
  for (const id of ['A', 'B', 'C']) store.ingest(message(id));
  assert.equal(store.raw.size, 2); assert.equal(store.raw.has(chat + '\0A'), false);
  clock = 200; assert.equal(store.messages(chat).length, 0); assert.equal(store.raw.size, 0);
});
test('SDK enum acknowledgements distinguish submission, server, delivery and read', () => {
  for (const [status, name] of [[proto.WebMessageInfo.Status.PENDING, 'submitted'], [proto.WebMessageInfo.Status.SERVER_ACK, 'server_ack'],
    [proto.WebMessageInfo.Status.DELIVERY_ACK, 'delivered'], [proto.WebMessageInfo.Status.READ, 'read']]) assert.equal(ackName(status), name);
});
test('encrypted SDK auth roundtrip restores binary keys and rejects wrong key/corruption', async t => {
  const h = await harness(t), auth = await encryptedAuth(h.files); await auth.saveCreds();
  await auth.state.keys.set({ session: { '../../../x': { key: Buffer.from('PRIVATE_TEST_KEY') } } });
  const restored = await encryptedAuth(h.files);
  assert.deepEqual((await restored.state.keys.get('session', ['../../../x']))['../../../x'].key, Buffer.from('PRIVATE_TEST_KEY'));
  const bytes = await fs.readFile(h.files.filename('creds')); assert.equal(bytes.includes(Buffer.from('noiseKey')), false);
  await assert.rejects(new SecureFiles(h.files.directory, randomBytes(32)).read('creds'));
  await fs.writeFile(h.files.filename('creds'), 'broken'); await assert.rejects(encryptedAuth(h.files));
});
test('typed media yields document/audio/native PTT payloads with frozen bytes', async () => {
  for (const kind of ['image', 'video', 'document', 'audio', 'voice']) {
    const bytes = Buffer.from(kind === 'voice' ? 'OggS...OpusHead...' : 'synthetic bytes'), digest = sha256(bytes);
    const payload = await sendContent({ media: { kind, sha256: digest, file: digest + '.bin', mimetype: 'application/octet-stream', name: 'invoice.pdf' } }, { readBytes: async () => bytes });
    assert.deepEqual(payload[kind === 'voice' ? 'audio' : kind], bytes);
    if (kind === 'voice') assert.equal(payload.ptt, true);
    if (kind === 'audio') assert.equal(payload.ptt, false);
    if (kind === 'document') assert.equal(payload.fileName, 'invoice.pdf');
  }
});
test('changed hashes, traversal and WAV-as-voice are rejected before sending', async () => {
  const bytes = Buffer.from('RIFF fake WAV'), digest = sha256(bytes);
  const m = { kind: 'voice', sha256: digest, file: digest + '.bin', mimetype: 'audio/wav' };
  await assert.rejects(sendContent({ media: m }, { readBytes: async () => bytes }));
  await assert.rejects(sendContent({ media: { ...m, kind: 'document', file: '../secret' } }, {}));
  await assert.rejects(sendContent({ media: { ...m, kind: 'document' } }, { readBytes: async () => Buffer.from('changed') }));
});
test('HTTP API requires token and rejects browser origins, foreign hosts and proxies', async t => {
  const h = await harness(t);
  assert.equal((await h.call('/status', undefined, { Authorization: '' })).code, 401);
  for (const headers of [{ Origin: 'https://evil.example' }, { Host: 'evil.example' }, { 'X-Forwarded-For': '127.0.0.1' }, { 'Sec-Fetch-Site': 'same-site' }]) assert.equal((await h.call('/status', undefined, headers)).code, 403);
  assert.equal((await h.call('/status')).body.historyScope, 'since_bridge_start');
});
test('identical concurrent actions send once; reused action with changed draft rejects', async t => {
  const h = await harness(t), p = { chat, text: 'synthetic test', actionId: 'synthetic_action_001' };
  const result = await Promise.all([h.call('/send', p), h.call('/send', p)]);
  assert.equal(h.calls(), 1); assert.equal(result[0].code, 200);
  assert.equal((await h.call('/send', { ...p, text: 'changed' })).code, 409);
  assert.equal(h.calls(), 1);
});
test('timeout remains consumed and cannot be retried into a duplicate', async t => {
  let calls = 0;
  const h = await harness(t, { sock: { sendMessage: async () => { calls++; return new Promise(() => {}); } } });
  const p = { chat, text: 'test', actionId: 'synthetic_timeout_001' };
  assert.equal((await h.call('/send', p)).body.state, 'uncertain');
  assert.equal((await h.call('/send', p)).body.state, 'uncertain'); assert.equal(calls, 1);
});
test('self-test gate checks both linked account and exact destination', async t => {
  const h = await harness(t, { testDestination: '+15550001111' });
  const p = { chat: 'user@example.com', text: 'test', actionId: 'synthetic_wrong_001' };
  assert.equal((await h.call('/send', p)).code, 403); assert.equal(h.calls(), 0);
  h.sock.user.id = '15550002222:user@example.com';
  assert.equal((await h.call('/send', { ...p, chat })).code, 403); assert.equal(h.calls(), 0);
});
test('mark-read uses actual incoming message keys; failed transport preserves unread', async t => {
  const h = await harness(t); h.store.ingest(message('IN_1')); h.store.ingest(message('OUT_1', true));
  assert.equal((await h.call('/markread', { chat })).body.count, 1);
  assert.deepEqual(h.readKeys(), [{ remoteJid: chat, id: 'IN_1', fromMe: false }]);
  h.store.ingest(message('IN_2')); h.sock.readMessages = async () => { throw new Error('failure'); };
  assert.equal((await h.call('/markread', { chat })).code, 500); assert.equal(h.store.list()[0].unread, 1);
});
test('mutations are deferred without calling SDK', async t => {
  const h = await harness(t);
  for (const route of ['/edit', '/delete', '/react']) assert.equal((await h.call(route, { chat, id: 'X' })).code, 409);
  assert.equal(h.calls(), 0);
});
test('media download is scoped to the exact observed chat/message and restricted media denied', async t => {
  const h = await harness(t); h.store.ingest(message('PDF_1', false, { documentMessage: { fileName: 'invoice.pdf', mimetype: 'application/pdf' } }));
  assert.equal((await h.call('/media', { chat, id: 'PDF_1' })).body, '%PDF-test');
  assert.equal((await h.call('/media', { chat: 'user@example.com', id: 'PDF_1' })).code, 404);
  h.store.ingest(message('ONCE', false, { viewOnceMessage: { message: { imageMessage: {} } } }));
  assert.equal((await h.call('/media', { chat, id: 'ONCE' })).code, 404);
});
test('ack updates apply to the recorded outgoing ID and action', async t => {
  const h = await harness(t), p = { chat, text: 'test', actionId: 'synthetic_ack_001' };
  const sent = (await h.call('/send', p)).body;
  assert.equal(sent.state, 'submitted'); assert.equal(sent.delivery, 'submitted');
  await h.api.updateDelivery([{ key: { remoteJid: chat, id: sent.id }, update: { status: proto.WebMessageInfo.Status.DELIVERY_ACK } }]);
  assert.equal((await h.call('/action?id=' + p.actionId)).body.delivery, 'delivered');
});
test('acknowledgement preceding the send return is preserved', async t => {
  const h = await harness(t);
  h.store.update([{ key: { remoteJid: chat, id: 'TEST_ID_1' }, update: { status: proto.WebMessageInfo.Status.SERVER_ACK } }]);
  const result = await h.call('/send', { chat, text: 'test', actionId: 'synthetic_early_ack_01' });
  assert.equal(result.body.delivery, 'server_ack');
});
