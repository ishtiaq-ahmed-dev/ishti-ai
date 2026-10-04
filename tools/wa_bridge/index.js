// Launch through: python -m ishti.wa_bridge --start (secrets arrive over stdin).
import makeWASocket, { makeCacheableSignalKeyStore, DisconnectReason, downloadMediaMessage } from '@whiskeysockets/baileys';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import readline from 'node:readline';
import { SecureFiles, encryptedAuth } from './secure-files.js';
import { MessageStore } from './core.js';
import { createApi } from './api.js';
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..'), DATA = path.join(ROOT, 'data', 'wa_bridge');
const port = Number(process.env.WA_BRIDGE_PORT || 8788);
if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error('Invalid bridge port');
const input = readline.createInterface({ input: process.stdin, terminal: false });
const bootstrap = await new Promise((resolve, reject) => {
  const timer = setTimeout(() => reject(new Error('Start bridge with python -m ishti.wa_bridge --start')), 5000);
  input.once('line', line => { clearTimeout(timer); try { resolve(JSON.parse(line)); } catch { reject(new Error('Invalid bootstrap')); } });
  input.once('close', () => { clearTimeout(timer); reject(new Error('Private bootstrap pipe required')); });
});
input.close();
const files = new SecureFiles(path.join(DATA, 'encrypted_state'), Buffer.from(bootstrap.key || '', 'base64'));
const auth = await encryptedAuth(files);
if (!await files.read('creds')) {
  const legacy = await fs.readdir(path.join(DATA, 'auth_info')).catch(() => []);
  if (legacy.length) throw new Error('Existing legacy session requires explicit secure migration');
}
const store = new MessageStore(), state = { connected: false, me: null, qr: null, qrAt: 0, lastError: null };
const logger = { level: 'silent', trace() {}, debug() {}, info() {}, warn() {}, error() {}, fatal() {}, child() { return this; } };
// libsignal can print session objects through console independently of the SDK logger.
for (const method of ['log', 'info', 'debug', 'warn', 'error', 'trace']) console[method] = () => {};
let sock, reconnect, stopping = false, attempts = 0;
const api = await createApi({ token: bootstrap.token, port, state, store, files,
  outbox: new SecureFiles(path.join(DATA, 'encrypted_outbox'), Buffer.from(bootstrap.key, 'base64')), socket: () => sock,
  testDestination: process.env.ISHTI_WA_TEST_DESTINATION || '',
  download: message => downloadMediaMessage(message, 'stream', { options: { timeout: 20000, maxRedirects: 0 } },
    { logger, reuploadRequest: m => sock.updateMediaMessage(m) }) });
const server = api.app.listen(port, '127.0.0.1');
server.on('error', () => { stopping = true; clearTimeout(reconnect); sock?.end(undefined); process.exitCode = 1; process.stderr.write('[wa-bridge] API could not start\n'); });
function connect() {
  if (stopping) return;
  sock = makeWASocket({ auth: { creds: auth.state.creds, keys: makeCacheableSignalKeyStore(auth.state.keys, logger) }, logger,
    printQRInTerminal: false, browser: ['ISHTI', 'Chrome', '120.0'], syncFullHistory: false, shouldSyncHistoryMessage: () => false,
    markOnlineOnConnect: false, emitOwnEvents: true, getMessage: async key => store.raw.get(key.remoteJid + '\0' + key.id)?.message });
  const current = sock;
  const safe = fn => (...args) => { if (current !== sock || stopping) return; Promise.resolve().then(() => fn(...args)).catch(() => { state.lastError = 'Bridge event could not be processed'; }); };
  sock.ev.on('creds.update', safe(auth.saveCreds));
  sock.ev.on('contacts.upsert', safe(rows => store.updateContacts(rows)));
  sock.ev.on('contacts.update', safe(rows => store.updateContacts(rows)));
  sock.ev.on('messages.upsert', safe(({ messages, type }) => { for (const message of messages) store.ingest(message, type); }));
  sock.ev.on('messages.update', safe(updates => api.updateDelivery(updates)));
  sock.ev.on('connection.update', safe(({ connection, lastDisconnect, qr }) => {
    if (qr) { state.qr = qr; state.qrAt = Date.now(); state.lastError = null; }
    if (connection === 'open') {
      state.connected = true; state.me = current.user?.id || null; state.qr = null; state.qrAt = 0; state.lastError = null; attempts = 0;
      process.stdout.write('[wa-bridge] connected\n');
    }
    if (connection === 'close') {
      state.connected = false; state.qr = null; state.qrAt = 0;
      const code = lastDisconnect?.error?.output?.statusCode;
      if ([DisconnectReason.loggedOut, DisconnectReason.badSession, DisconnectReason.connectionReplaced].includes(code)) {
        state.lastError = 'Session disconnected; review linked devices before relinking'; return;
      }
      state.lastError = 'Connection interrupted; reconnecting'; clearTimeout(reconnect);
      reconnect = setTimeout(connect, Math.min(60000, 2000 * 2 ** Math.min(attempts++, 5)));
    }
  }));
}
server.once('listening', () => { process.stdout.write('[wa-bridge] local API ready\n'); connect(); });
async function stop() {
  if (stopping) return;
  stopping = true; clearTimeout(reconnect); state.qr = null; sock?.end(undefined); server.close(); await files.tail;
}
process.on('SIGINT', stop); process.on('SIGTERM', stop);
