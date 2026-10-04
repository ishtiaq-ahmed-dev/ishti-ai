import express from 'express';
import { timingSafeEqual } from 'node:crypto';
import { createRequire } from 'node:module';
import { jidOf, userJid, sha256, fail, sendContent, validateDownload, MAX_MEDIA } from './core.js';
export async function createApi({ token, port, state, store, socket, files, outbox, download, testDestination = '', sendTimeout = 20000 }) {
  if (typeof token !== 'string' || token.length < 32) throw fail('API token required');
  const actions = new Map(Object.entries(await files.read('actions') || {}));
  for (const action of actions.values()) if (action.state === 'pending') action.state = 'uncertain';
  const persist = () => files.write('actions', Object.fromEntries(actions));
  const app = express(); app.disable('x-powered-by');
  app.use((req, res, next) => {
    res.set('Cache-Control', 'no-store');
    if (!['127.0.0.1', '::1', '::ffff:127.0.0.1'].includes(req.socket.remoteAddress)
      || ![`127.0.0.1:${port}`, `localhost:${port}`].includes(req.headers.host)
      || req.headers.origin || req.headers.forwarded || Object.keys(req.headers).some(h => h.startsWith('x-forwarded-'))
      || (req.headers['sec-fetch-site'] && req.headers['sec-fetch-site'] !== 'none')) return res.status(403).json({ error: 'Local client required' });
    const given = Buffer.from(String(req.headers.authorization || '').replace(/^Bearer /, '')), expected = Buffer.from(token);
    if (given.length !== expected.length || !timingSafeEqual(given, expected)) return res.status(401).json({ error: 'Authentication required' });
    next();
  });
  app.use(express.json({ limit: '96kb', strict: true }));
  const route = fn => (req, res, next) => Promise.resolve().then(() => fn(req, res)).catch(next);
  let qrCode, qrMatrix;
  app.get('/status', (_req, res) => {
    const qr = state.qrAt && Date.now() - state.qrAt < 60000 ? state.qr : null;
    if (qr && qr !== qrCode) {
      const QRCode = createRequire(import.meta.url)('qrcode-terminal/vendor/QRCode');
      const encoder = new QRCode(-1, 1); encoder.addData(qr); encoder.make();
      qrMatrix = encoder.modules; qrCode = qr;
    }
    const { qr: _raw, ...publicState } = state;
    res.json({ ...publicState, raw_qr: qr, qrAvailable: !!qr, qr_matrix: qr ? qrMatrix : null,
      historyScope: 'since_bridge_start', revision: store.revision, retainedChats: store.chats.size });
  });
  app.get('/chats', (_req, res) => res.json(store.list()));
  app.get('/messages', route(async (req, res) => res.json(store.messages(req.query.chat ? jidOf(req.query.chat) : '', req.query.limit))));
  app.get('/action', (req, res) => res.json(actions.get(String(req.query.id)) || { state: 'unknown' }));
  app.post('/send', route(async (req, res) => {
    const p = req.body || {}, id = p.actionId;
    if (!/^[A-Za-z0-9_-]{16,100}$/.test(id || '')) throw fail('Action ID required');
    const chat = jidOf(p.chat), fingerprint = sha256(Buffer.from(JSON.stringify({ chat, text: p.text || '', media: p.media || null, caption: p.caption || '' })));
    if (actions.has(id)) {
      const previous = actions.get(id);
      if (previous.fingerprint !== fingerprint) throw fail('Action ID already belongs to another draft', 409);
      return res.json(previous);
    }
    if (!state.connected || !socket()) throw fail('WhatsApp is not connected', 503);
    if (testDestination && (chat !== jidOf(testDestination) || userJid(socket().user?.id) !== chat)) throw fail('Self-test destination/account mismatch', 403);
    if (actions.size >= 10000) throw fail('Action ledger capacity reached', 503);
    const content = await sendContent(p, outbox);
    if (actions.has(id)) {
      const existing = actions.get(id);
      if (existing.fingerprint !== fingerprint) throw fail('Action ID conflict', 409);
      return res.json(existing);
    }
    const action = { actionId: id, chat, fingerprint, state: 'pending', at: Date.now(), id: null, delivery: null };
    actions.set(id, action);
    try { await persist(); } catch { actions.delete(id); throw fail('Cannot persist action before sending', 503); }
    let timer;
    const attempt = Promise.resolve().then(() => socket().sendMessage(chat, content)).then(async message => {
      if (!message?.key?.id || message.key.remoteJid !== chat) throw fail('Send returned no matching message ID');
      const row = store.ingest(message, 'append');
      action.id = message.key.id; action.state = 'submitted'; action.delivery = row?.delivery === 'observed' ? 'submitted' : row?.delivery || 'submitted';
      await persist(); return action;
    }).catch(async () => { action.state = 'uncertain'; await persist().catch(() => {}); return action; });
    await Promise.race([attempt, new Promise(resolve => { timer = setTimeout(resolve, sendTimeout); })]);
    clearTimeout(timer);
    if (action.state === 'pending') { action.state = 'uncertain'; await persist(); }
    res.json(action);
  }));
  app.post('/markread', route(async (req, res) => {
    if (!state.connected) throw fail('Not connected', 503);
    const chat = jidOf(req.body.chat), rows = store.messages(chat, 200).filter(row => !row.fromMe && row.unread);
    const keys = rows.map(row => ({ remoteJid: chat, id: row.id, fromMe: false, ...(row.participant ? { participant: row.participant } : {}) }));
    if (keys.length) await socket().readMessages(keys);
    store.markRead(chat, rows.map(row => row.id)); res.json({ ok: true, count: keys.length });
  }));
  for (const name of ['edit', 'delete', 'react']) app.post('/' + name, (_req, res) => res.status(409).json({ error: 'Deferred in NEEDS; no mutation performed' }));
  app.post('/media', route(async (req, res) => {
    const chat = jidOf(req.body.chat), id = String(req.body.id || '');
    const message = store.raw.get(chat + '\0' + id), row = store.messages(chat, 200).find(row => row.id === id);
    if (!message || !row?.media || row.restricted) throw fail('Exact retained, unrestricted media message required', 404);
    if (row.media.size > MAX_MEDIA) throw fail('Incoming attachment exceeds limit', 413);
    if (!state.connected) throw fail('Not connected', 503);
    validateDownload(message);
    const stream = await download(message), chunks = []; let size = 0;
    try { for await (const chunk of stream) {
      size += chunk.length; if (size > MAX_MEDIA) throw fail('Incoming attachment exceeds limit', 413); chunks.push(chunk);
    } } finally { stream.destroy?.(); }
    const bytes = Buffer.concat(chunks); if (!bytes.length) throw fail('Empty incoming attachment');
    res.set('Content-Type', 'application/octet-stream'); res.set('X-Content-SHA256', sha256(bytes)); res.send(bytes);
  }));
  app.use((err, _req, res, _next) => res.status(err.statusCode || err.status || 500).json({ error: err.statusCode ? err.message : 'Bridge operation failed' }));
  return { app, actions, async updateDelivery(updates) {
    store.update(updates);
    for (const { key } of updates) for (const action of actions.values()) if (action.id === key.id && action.chat === key.remoteJid) {
      action.delivery = store.messages(action.chat).find(r => r.id === action.id)?.delivery || action.delivery;
    }
    await persist();
  } };
}
