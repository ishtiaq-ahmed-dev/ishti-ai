// Secrets arrive over the private launch pipe; they never appear in argv or logs.
import fs from 'node:fs/promises';
import path from 'node:path';
import { createHash, randomBytes, createCipheriv, createDecipheriv } from 'node:crypto';
import { BufferJSON, initAuthCreds, proto } from '@whiskeysockets/baileys';
export class SecureFiles {
  constructor(directory, key) {
    if (!Buffer.isBuffer(key) || key.length !== 32) throw new Error('Encryption key required');
    this.directory = directory; this.key = key; this.tail = Promise.resolve();
  }
  filename(name) { return path.join(this.directory, createHash('sha256').update(name).digest('hex') + '.enc'); }
  async read(name) {
    const bytes = await this.readBytes(name);
    return bytes === null ? null : JSON.parse(bytes.toString(), BufferJSON.reviver);
  }
  async readBytes(name) {
    await this.tail;
    let bytes;
    try { bytes = await fs.readFile(this.filename(name)); }
    catch (err) { if (err.code === 'ENOENT') return null; throw err; }
    if (bytes.length < 32 || bytes.subarray(0, 4).toString() !== 'IWA1') throw new Error('Invalid encrypted state');
    const decipher = createDecipheriv('aes-256-gcm', this.key, bytes.subarray(4, 16));
    decipher.setAAD(Buffer.from(name)); decipher.setAuthTag(bytes.subarray(16, 32));
    return Buffer.concat([decipher.update(bytes.subarray(32)), decipher.final()]);
  }
  write(name, value) {
    const job = this.tail.then(async () => {
      await fs.mkdir(this.directory, { recursive: true, mode: 0o700 });
      if (value === null) { await fs.rm(this.filename(name), { force: true }); return; }
      const iv = randomBytes(12), cipher = createCipheriv('aes-256-gcm', this.key, iv);
      cipher.setAAD(Buffer.from(name));
      const encrypted = Buffer.concat([cipher.update(JSON.stringify(value, BufferJSON.replacer)), cipher.final()]);
      const tmp = this.filename(name) + '.' + randomBytes(8).toString('hex') + '.tmp';
      try {
        await fs.writeFile(tmp, Buffer.concat([Buffer.from('IWA1'), iv, cipher.getAuthTag(), encrypted]), { flag: 'wx', mode: 0o600 });
        await fs.rename(tmp, this.filename(name));
      } finally { await fs.rm(tmp, { force: true }); }
    });
    this.tail = job.catch(() => {}); return job;
  }
}
export async function encryptedAuth(files) {
  // Corrupt credentials fail closed instead of silently creating a new session.
  const creds = await files.read('creds') || initAuthCreds();
  return { state: { creds, keys: {
    get: async (type, ids) => Object.fromEntries(await Promise.all(ids.map(async id => {
      let value = await files.read(`key:${type}:${id}`);
      if (value && type === 'app-state-sync-key') value = proto.Message.AppStateSyncKeyData.fromObject(value);
      return [id, value];
    }))),
    set: async data => { for (const [type, entries] of Object.entries(data)) {
      for (const [id, value] of Object.entries(entries)) await files.write(`key:${type}:${id}`, value ?? null);
    } },
  } }, saveCreds: () => files.write('creds', creds) };
}
