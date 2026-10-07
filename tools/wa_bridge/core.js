import path from 'node:path';
import { createHash } from 'node:crypto';
import { normalizeMessageContent, proto } from '@whiskeysockets/baileys';
export const MAX_MEDIA = 50 * 1024 * 1024;
export const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');
export const fail = (message, code = 400) => Object.assign(new Error(message), { statusCode: code });
export function jidOf(value) {
  const raw = String(value || '').trim(), number = raw.replace(/^\+/, '').replace(/[ ()-]/g, '');
  if (/^\d{6,15}$/.test(number)) return number + '@s.whatsapp.net';
  if (/^(?:\d{6,15}@s\.whatsapp\.net|\d{6,20}@lid|\d{6,20}(?:-\d{5,20})?@g\.us)$/.test(raw)) return raw;
  throw fail('Use an exact resolved chat ID or complete international phone number');
}
export const userJid = value => String(value || '').replace(/:\d+(?=@)/, '');
function contentOf(message) {
  let content = message || {};
  for (let i = 0; i < 5; i++) {
    if (content.viewOnceMessage || content.viewOnceMessageV2 || content.viewOnceMessageV2Extension || content.ephemeralMessage) return { restricted: true, content: {} };
    if (!content.documentWithCaptionMessage) break;
    content = content.documentWithCaptionMessage.message || {};
  }
  return { restricted: false, content: normalizeMessageContent(content) || {} };
}
export function describeMessage(message) {
  const { content, restricted } = contentOf(message);
  let kind = 'text', media = null;
  for (const type of ['image', 'document', 'audio', 'video', 'sticker']) if (content[type + 'Message']) { kind = type; media = content[type + 'Message']; break; }
  const text = content.conversation || content.extendedTextMessage?.text || media?.caption || '';
  return { text: restricted ? '[restricted media]' : text || (media ? `[${kind}]` : ''), kind, restricted,
    media: media ? { mimetype: media.mimetype || '', fileName: media.fileName || '', size: Number(media.fileLength || 0), ptt: !!media.ptt } : null };
}
export function ackName(status) {
  const S = proto.WebMessageInfo.Status;
  if (status >= S.READ) return 'read';
  if (status >= S.DELIVERY_ACK) return 'delivered';
  if (status >= S.SERVER_ACK) return 'server_ack';
  return 'submitted';
}
export class MessageStore {
  constructor({ maxChats = 200, maxMessages = 200, ttl = 7 * 86400000, clock = Date.now } = {}) {
    Object.assign(this, { maxChats, maxMessages, ttl, clock });
    this.chats = new Map(); this.contacts = new Map(); this.lidToPhone = new Map(); this.raw = new Map(); this.pendingStatus = new Map(); this.revision = 0;
  }
  rememberIdentity(lid, phone) {
    lid = String(lid || ''); phone = String(phone || '');
    if (!/@lid$/.test(lid) || !/@s\.whatsapp\.net$/.test(phone)) return;
    this.lidToPhone.set(userJid(lid), userJid(phone));
    while (this.lidToPhone.size > 5000) this.lidToPhone.delete(this.lidToPhone.keys().next().value);
  }
  resolvePhone(jid) {
    jid = userJid(jid);
    if (/@s\.whatsapp\.net$/.test(jid)) return jid;
    return this.lidToPhone.get(jid) || '';
  }
  updateContacts(contacts) {
    for (const c of contacts) if (c.id) {
      this.contacts.set(c.id, c.name || c.notify || c.verifiedName || this.contacts.get(c.id) || c.id);
      this.rememberIdentity(c.lid || (/@lid$/.test(c.id) ? c.id : ''), c.jid || (/@s\.whatsapp\.net$/.test(c.id) ? c.id : ''));
    }
    while (this.contacts.size > 5000) this.contacts.delete(this.contacts.keys().next().value);
    this.revision++;
  }
  prune() {
    const cutoff = this.clock() - this.ttl;
    for (const [chat, records] of this.chats) {
      for (const [id, row] of records) if (row.observedAt < cutoff) { records.delete(id); this.raw.delete(chat + '\0' + id); }
      if (!records.size) this.chats.delete(chat);
    }
    while (this.chats.size > this.maxChats) {
      const oldest = this.chats.keys().next().value;
      for (const id of this.chats.get(oldest).keys()) this.raw.delete(oldest + '\0' + id);
      this.chats.delete(oldest);
    }
  }
  ingest(message, type = 'notify') {
    const key = message.key || {}, chat = key.remoteJid;
    if (!chat || !key.id || /@(?:broadcast|newsletter)$/.test(chat) || !message.message) return null;
    this.rememberIdentity(key.senderLid, key.senderPn);
    this.rememberIdentity(key.participantLid, key.participantPn);
    this.rememberIdentity(/@lid$/.test(chat) ? chat : '', key.senderPn || key.participantPn);
    const records = this.chats.get(chat) || new Map(), previous = records.get(key.id), detail = describeMessage(message.message);
    const senderJid = this.resolvePhone(key.participantPn || key.senderPn || key.participant || chat);
    const row = { chat, id: key.id, fromMe: !!key.fromMe, participant: key.participant || '', ...detail,
      senderJid, senderLid: key.participantLid || key.senderLid || (/@lid$/.test(chat) ? chat : ''),
      ts: Number(message.messageTimestamp || 0) * 1000, observedAt: this.clock(), unread: previous?.unread ?? (type === 'notify' && !key.fromMe),
      delivery: message.status == null ? previous?.delivery || this.pendingStatus.get(chat + '\0' + key.id) || 'observed' : ackName(message.status) };
    this.pendingStatus.delete(chat + '\0' + key.id);
    records.set(row.id, row); this.raw.set(chat + '\0' + row.id, message);
    while (records.size > this.maxMessages) { const id = records.keys().next().value; records.delete(id); this.raw.delete(chat + '\0' + id); }
    this.chats.delete(chat); this.chats.set(chat, records); this.prune(); this.revision++;
    return row;
  }
  update(updates) {
    for (const { key, update } of updates) {
      const row = this.chats.get(key.remoteJid)?.get(key.id);
      if (update.status == null) continue;
      if (row) row.delivery = ackName(update.status);
      else this.pendingStatus.set(key.remoteJid + '\0' + key.id, ackName(update.status));
    }
    while (this.pendingStatus.size > 2000) this.pendingStatus.delete(this.pendingStatus.keys().next().value);
    this.revision++;
  }
  list() {
    this.prune();
    return [...this.chats].map(([id, records]) => {
      const rows = [...records.values()].sort((a, b) => a.ts - b.ts);
      return { id, name: this.contacts.get(id) || id.split('@')[0], unread: rows.filter(r => r.unread).length, last: rows.at(-1) };
    }).sort((a, b) => b.last.ts - a.last.ts);
  }
  messages(chat, limit = 50) {
    this.prune();
    const rows = chat ? [...(this.chats.get(chat)?.values() || [])] : [...this.chats.values()].flatMap(r => [...r.values()]);
    return rows.sort((a, b) => a.ts - b.ts).slice(-Math.min(200, Math.max(1, Number(limit) || 50)));
  }
  markRead(chat, ids) { for (const id of ids) { const row = this.chats.get(chat)?.get(id); if (row) row.unread = false; } this.revision++; }
}
export async function sendContent(payload, outbox) {
  if (payload.media) {
    const m = payload.media;
    if (!/^[a-f0-9]{64}$/.test(m.sha256 || '') || m.file !== m.sha256 + '.bin') throw fail('Invalid approved attachment');
    const bytes = await outbox.readBytes(m.file); // decrypt and freeze before the SDK sees them
    if (!bytes || !bytes.length || bytes.length > MAX_MEDIA || sha256(bytes) !== m.sha256) throw fail('Attachment changed after approval or exceeds limit');
    if (!['image', 'video', 'document', 'audio', 'voice'].includes(m.kind)) throw fail('Unsupported media kind');
    if (typeof m.mimetype !== 'string' || !/^[a-z0-9.+-]+\/[a-z0-9.+;-]+(?: codecs=opus)?$/i.test(m.mimetype)) throw fail('Invalid MIME type');
    if (m.kind === 'voice' && (bytes.subarray(0, 4).toString() !== 'OggS' || !bytes.subarray(0, 4096).includes(Buffer.from('OpusHead')))) throw fail('Voice notes require Ogg Opus');
    const result = { [m.kind === 'voice' ? 'audio' : m.kind]: bytes, mimetype: m.mimetype };
    if (m.kind === 'voice') { result.ptt = true; result.mimetype = 'audio/ogg; codecs=opus'; }
    if (m.kind === 'audio') result.ptt = false;
    if (m.kind === 'document') result.fileName = path.basename(String(m.name || 'attachment')).slice(0, 160);
    if (payload.caption && !['audio', 'voice'].includes(m.kind)) result.caption = String(payload.caption).slice(0, 4096);
    return result;
  }
  if (typeof payload.text !== 'string' || !payload.text.trim() || payload.text.length > 65536) throw fail('Message text required (maximum 65536 characters)');
  return { text: payload.text, linkPreview: null };
}

export function validateDownload(message) {
  const { restricted, content } = contentOf(message.message);
  if (restricted) throw fail('Restricted media cannot be downloaded');
  const media = content.imageMessage || content.documentMessage || content.audioMessage || content.videoMessage || content.stickerMessage;
  if (!media) throw fail('Media payload required');
  if (media.url) {
    let url;
    try { url = new URL(media.url); } catch { throw fail('Invalid media URL'); }
    if (url.protocol !== 'https:' || url.username || url.password || (url.port && url.port !== '443')
      || !(url.hostname === 'whatsapp.net' || url.hostname.endsWith('.whatsapp.net'))) throw fail('Media URL outside WhatsApp CDN');
  }
  if (media.directPath && (!String(media.directPath).startsWith('/') || String(media.directPath).startsWith('//'))) throw fail('Invalid media direct path');
}
