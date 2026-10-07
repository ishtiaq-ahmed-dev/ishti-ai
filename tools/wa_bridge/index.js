// Launch through: python -m ishti.wa_bridge --start (secrets arrive over stdin).
import makeWASocket, { makeCacheableSignalKeyStore, DisconnectReason, downloadMediaMessage } from '@whiskeysockets/baileys';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import readline from 'node:readline';
import { SecureFiles, encryptedAuth } from './secure-files.js';
import { MessageStore } from './core.js';
import { createApi } from './api.js';
import { ConnectionLifecycle, initialBridgeState, logger } from './runtime.js';
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
const store = new MessageStore(), state = initialBridgeState(auth.state.creds);
// libsignal can print session objects through console independently of the SDK logger.
for (const method of ['log', 'info', 'debug', 'warn', 'error', 'trace']) console[method] = () => {};
let sock, stopping = false;
const api = await createApi({ token: bootstrap.token, port, state, store, files,
  outbox: new SecureFiles(path.join(DATA, 'encrypted_outbox'), Buffer.from(bootstrap.key, 'base64')), socket: () => sock,
  testDestination: bootstrap.testDestination || process.env.ISHTI_WA_TEST_DESTINATION || '',
  download: message => downloadMediaMessage(message, 'stream', { options: { timeout: 20000, maxRedirects: 0 } },
    { logger, reuploadRequest: m => sock.updateMediaMessage(m) }) });
const server = api.app.listen(port, '127.0.0.1');
const lifecycle = new ConnectionLifecycle({ state, reasons: DisconnectReason, reconnect: () => connect() });
server.on('error', () => { stopping = true; lifecycle.cancel(); sock?.end(undefined); process.exitCode = 1; process.stderr.write('[wa-bridge] API could not start\n'); });
function connect() {
  if (stopping) return;
  sock = makeWASocket({ auth: { creds: auth.state.creds, keys: makeCacheableSignalKeyStore(auth.state.keys, logger) }, logger,
    printQRInTerminal: false, browser: ['ISHTI', 'Chrome', '120.0'], syncFullHistory: false, shouldSyncHistoryMessage: () => false,
    markOnlineOnConnect: false, emitOwnEvents: true, getMessage: async key => store.raw.get(key.remoteJid + '\0' + key.id)?.message });
  const current = sock;
  const safe = fn => (...args) => { if (current !== sock || stopping) return; Promise.resolve().then(() => fn(...args)).catch(() => { state.lastError = 'Bridge event could not be processed'; }); };
  sock.ev.on('creds.update', safe(async update => {
    if (update.me?.id) state.me = update.me.id;
    await auth.saveCreds(update);
  }));
  sock.ev.on('contacts.upsert', safe(rows => store.updateContacts(rows)));
  sock.ev.on('contacts.update', safe(rows => store.updateContacts(rows)));
  sock.ev.on('messages.upsert', safe(({ messages, type }) => { for (const message of messages) store.ingest(message, type); }));
  sock.ev.on('messages.update', safe(updates => api.updateDelivery(updates)));
  sock.ev.on('connection.update', safe(update => {
    const result = lifecycle.update(update, current.user?.id || auth.state.creds.me?.id || null);
    if (result.action === 'open') process.stdout.write('[wa-bridge] connected\n');
  }));
}
server.once('listening', () => { process.stdout.write('[wa-bridge] local API ready\n'); connect(); });
async function stop() {
  if (stopping) return;
  stopping = true; lifecycle.cancel(); state.qr = null; sock?.end(undefined); server.close(); await files.tail;
}
process.on('SIGINT', stop); process.on('SIGTERM', stop);
