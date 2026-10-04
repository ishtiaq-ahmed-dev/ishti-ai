/**
 * ISHTI WhatsApp bridge - pair ONCE by scanning a QR code, then ISHTI talks to WhatsApp over a small
 * loopback HTTP API. No browser, no page scraping: messages arrive as they land.
 *
 * Built on Baileys (MIT, https://github.com/WhiskeySockets/Baileys), the maintained open-source
 * WhatsApp-Web WebSocket library. NOT affiliated with WhatsApp - see its own disclaimer.
 *
 *   node index.js              pair (print QR + save it to data/wa_bridge/pair.txt) and serve
 *   node index.js --qr-only    just print the QR and exit
 *
 * Endpoints (127.0.0.1 only): /status /chats /messages /send /markread /delete /edit /react
 */
import makeWASocket, {
  useMultiFileAuthState, fetchLatestBaileysVersion, DisconnectReason, makeCacheableSignalKeyStore,
} from '@whiskeySockets/baileys';
import qrcode from 'qrcode-terminal';
import express from 'express';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..', '..');
const DATA = path.join(ROOT, 'data', 'wa_bridge');
const SESSION = path.join(DATA, 'auth_info');
const INBOX = path.join(DATA, 'inbox.jsonl');
const PORT = Number(process.env.WA_BRIDGE_PORT || 8788);
const LOG = (...a) => console.log('[wa-bridge]', ...a);

fs.mkdirSync(SESSION, { recursive: true });

const logger = { level: 'silent', info() {}, warn() {}, error() {}, trace() {}, child: () => logger };

let sock = null;
let status = { connected: false, me: null, qr: null, lastError: null };
const unread = new Map();        // chat id -> unread count
const lastMessage = new Map();   // chat id -> {text, ts, fromMe}

function appendInbox(row) {
  try { fs.appendFileSync(INBOX, JSON.stringify({ ...row, at: Date.now() }) + '\n'); } catch (e) { /* ignore */ }
}

function jidOf(chat) {
  const s = String(chat || '').trim();
  if (!s) return '';
  if (s.includes('@')) return s;
  if (/^\d{5,}$/.test(s)) return `${s}@s.whatsapp.net`;
  return `${s.replace(/\s+/g, '')}@s.whatsapp.net`;
}

function bodyText(m) {
  const msg = m.message || {};
  const t = msg.conversation || msg.imageMessage?.caption || msg.videoMessage?.caption
    || msg.documentMessage?.caption || msg.extendedTextMessage?.text || '';
  if (t) return t;
  if (msg.imageMessage) return '[image]';
  if (msg.videoMessage) return '[video]';
  if (msg.audioMessage) return '[voice message]';
  if (msg.documentMessage) return '[document]';
  if (msg.stickerMessage) return '[sticker]';
  return '';
}

async function chatName(jid) {
  try {
    const c = await sock.storeFetch?.(jid, 'lid-mapping');
    if (c?.name) return c.name;
  } catch (e) { /* ignore */ }
  const id = String(jid).split('@')[0];
  const c = sock.store?.contacts?.[id] || sock.store?.contacts?.[jid];
  return c?.name || c?.notify || id;
}

function chatKey(id) {
  try {
    const key = sock.store?.messages?.get?.(id);
    if (key) return key;
  } catch (e) { /* ignore */ }
  const c = sock.store?.contacts?.[id];
  return c ? { remoteJid: id, id, fromMe: true } : { remoteJid: id, id, fromMe: true };
}

function writeQr(qr) {
  try {
    qrcode.generate(qr, { small: true }, (code) => {
      fs.writeFileSync(path.join(DATA, 'pair.txt'),
        'Open WhatsApp > Linked devices > Link a device, and scan this:\n' + code + '\n');
    });
  } catch (e) { /* ignore */ }
}

async function connect() {
  const { state, saveCreds } = await useMultiFileAuthState(SESSION);
  const { version } = await fetchLatestBaileysVersion();
  sock = makeWASocket({
    version,
    auth: { creds: state.creds, keys: makeCacheableSignalKeyStore(state.keys, logger) },
    printQRInTerminal: false,
    browser: ['ISHTI', 'Chrome', '120.0'],
    logger,
  });

  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('connection.update', async ({ connection, lastDisconnect, qr }) => {
    if (qr) { status.qr = qr; LOG('new QR ready - scan it in WhatsApp > Linked devices'); writeQr(qr); }
    if (connection === 'open') {
      status.connected = true; status.qr = null; status.lastError = null;
      status.me = sock.user?.id || null;
      LOG('connected as', status.me);
    }
    if (connection === 'close') {
      status.connected = false;
      const code = lastDisconnect?.error?.output?.statusCode;
      LOG('closed', code, lastDisconnect?.error?.message || '');
      if (code !== DisconnectReason.loggedOut) setTimeout(connect, 4000);  // reconnect, session kept
      else { status.lastError = 'logged out - scan a QR again'; LOG('logged out'); }
    }
  });

  sock.ev.on('messages.upsert', async ({ messages, type }) => {
    for (const m of messages) {
      const chat = m.key.remoteJid || '';
      if (!chat || chat.endsWith('@newsletter') || chat.endsWith('@broadcast')) continue;
      if (type === 'notify' && !m.key.fromMe) unread.set(chat, (unread.get(chat) || 0) + 1);
      const row = { chat, name: await chatName(chat), id: m.key.id, fromMe: !!m.key.fromMe,
                    text: bodyText(m), ts: Number(m.messageTimestamp || 0) * 1000,
                    unread: unread.get(chat) || 0 };
      lastMessage.set(chat, { text: row.text, ts: row.ts, fromMe: row.fromMe });
      appendInbox(row);
    }
  });
}

const app = express();
app.use(express.json({ limit: '30mb' }));

app.get('/status', (_req, res) => res.json(status));

app.get('/chats', async (_req, res) => {
  const rows = [];
  for (const jid of Object.keys(sock?.store?.chats || {})) {
    if (jid.endsWith('@newsletter')) continue;
    rows.push({ id: jid, name: await chatName(jid), unread: unread.get(jid) || 0,
                last: lastMessage.get(jid) || null });
  }
  rows.sort((a, b) => (b.last?.ts || 0) - (a.last?.ts || 0));
  res.json(rows);
});

app.get('/messages', async (req, res) => {
  try {
    const limit = Math.min(Number(req.query.limit || 50), 200);
    const chat = jidOf(req.query.chat || '');
    if (chat && sock.store?.msgs?.get?.(chat)) {
      const rows = [...sock.store.msgs.get(chat)].slice(-limit).map((m) => ({
        id: m.key.id, fromMe: !!m.key.fromMe, text: bodyText(m),
        ts: Number(m.messageTimestamp || 0) * 1000,
      }));
      return res.json(rows);
    }
    if (!chat) {
      const rows = [];
      for (const [cid, msgs] of sock.store?.msgs || []) {
        for (const m of [...msgs].slice(-3)) {
          rows.push({ chat: cid, id: m.key.id, fromMe: !!m.key.fromMe, text: bodyText(m),
                      ts: Number(m.messageTimestamp || 0) * 1000 });
        }
      }
      rows.sort((a, b) => b.ts - a.ts);
      return res.json(rows.slice(0, limit));
    }
    res.json([]);
  } catch (e) { res.status(500).json({ error: String(e) }); }
});

app.post('/send', async (req, res) => {
  try {
    const chat = jidOf(req.body.chat);
    const out = req.body.media
      ? { image: { url: req.body.media }, caption: req.body.caption }
      : { text: req.body.text || '' };
    const r = await sock.sendMessage(chat, out);
    appendInbox({ chat, name: await chatName(chat), id: r.key.id, fromMe: true,
                  text: bodyText(r), ts: Date.now() });
    res.json({ ok: true, id: r.key.id });
  } catch (e) { res.status(500).json({ ok: false, error: String(e) }); }
});

app.post('/markread', async (req, res) => {
  try {
    const chat = jidOf(req.body.chat);
    unread.set(chat, 0);
    await sock.readMessages([chat]);
    res.json({ ok: true });
  } catch (e) { res.status(500).json({ ok: false, error: String(e) }); }
});

app.post('/delete', async (req, res) => {
  try {
    await sock.sendMessage(jidOf(req.body.chat), { delete: chatKey(req.body.key || req.body.id) });
    res.json({ ok: true });
  } catch (e) { res.status(500).json({ ok: false, error: String(e) }); }
});

app.post('/edit', async (req, res) => {
  try {
    const chat = jidOf(req.body.chat);
    await sock.sendMessage(chat, { edit: { key: chatKey(req.body.key || req.body.id),
                                           text: req.body.text || '' } });
    res.json({ ok: true });
  } catch (e) { res.status(500).json({ ok: false, error: String(e) }); }
});

app.post('/react', async (req, res) => {
  try {
    const chat = jidOf(req.body.chat);
    await sock.sendMessage(chat, { react: { text: req.body.emoji || '👍',
                                            key: chatKey(req.body.key || req.body.id) } });
    res.json({ ok: true });
  } catch (e) { res.status(500).json({ ok: false, error: String(e) }); }
});

await connect();
const server = app.listen(PORT, '127.0.0.1', () =>
  LOG(`API on http://127.0.0.1:${PORT}  data=${DATA}`));

if (process.argv.includes('--qr-only')) {
  const t0 = Date.now();
  while (!status.qr && Date.now() - t0 < 60000) await new Promise((r) => setTimeout(r, 500));
  if (status.qr) qrcode.generate(status.qr, { small: true }, (c) => console.log(c));
  else console.log('no QR yet - run without --qr-only and watch the log');
  server.close();
  process.exit(0);
}

process.on('SIGINT', async () => { try { await sock?.end(undefined); } catch (e) {} process.exit(0); });
process.on('SIGTERM', async () => { try { await sock?.end(undefined); } catch (e) {} process.exit(0); });