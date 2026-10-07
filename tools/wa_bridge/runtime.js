import pino from 'pino';

export const logger = pino({ level: 'silent' });

export function initialBridgeState(creds = {}) {
  return {
    connected: false,
    me: creds.me?.id || null,
    qr: null,
    qrAt: 0,
    pairingState: creds.registered ? 'paired' : 'waiting_for_scan',
    lastError: null,
  };
}

export function disconnectCode(error) {
  return Number(error?.output?.statusCode ?? error?.data?.statusCode ?? error?.statusCode ?? 0);
}

export class ConnectionLifecycle {
  constructor({ state, reasons, reconnect, setTimer = setTimeout, clearTimer = clearTimeout,
    clock = Date.now, maxQrRetries = 5, baseDelayMs = 2000, maxDelayMs = 60000 }) {
    Object.assign(this, { state, reasons, reconnect, setTimer, clearTimer, clock,
      maxQrRetries, baseDelayMs, maxDelayMs });
    this.timer = null;
    this.attempts = 0;
    this.qrRetries = 0;
    this.restartAttempts = 0;
  }

  cancel() {
    if (this.timer !== null) this.clearTimer(this.timer);
    this.timer = null;
  }

  schedule(delay) {
    this.cancel();
    this.timer = this.setTimer(() => {
      this.timer = null;
      this.reconnect();
    }, delay);
  }

  update({ connection, lastDisconnect, qr }, me = null) {
    if (qr) {
      this.state.qr = qr;
      this.state.qrAt = this.clock();
      this.state.pairingState = 'waiting_for_scan';
      this.state.lastError = null;
    }
    if (connection === 'open') {
      this.cancel();
      this.state.connected = true;
      this.state.me = me || this.state.me;
      this.state.qr = null;
      this.state.qrAt = 0;
      this.state.pairingState = 'paired';
      this.state.lastError = null;
      this.attempts = 0;
      this.qrRetries = 0;
      this.restartAttempts = 0;
      return { action: 'open' };
    }
    if (connection !== 'close') return { action: 'none' };

    this.state.connected = false;
    this.state.qr = null;
    this.state.qrAt = 0;
    const code = disconnectCode(lastDisconnect?.error);
    if ([this.reasons.loggedOut, this.reasons.badSession, this.reasons.connectionReplaced].includes(code)) {
      this.cancel();
      this.state.lastError = 'Session disconnected; review linked devices before relinking';
      return { action: 'stop', code };
    }
    if (code === this.reasons.restartRequired) {
      if (this.restartAttempts >= 1) {
        this.cancel();
        this.state.lastError = 'WhatsApp requested repeated restarts; restart the bridge';
        return { action: 'stop', code };
      }
      this.restartAttempts += 1;
      this.state.pairingState = this.state.me ? 'paired' : 'waiting_for_scan';
      this.state.lastError = 'Pairing saved; restarting the connection';
      this.schedule(0);
      return { action: 'restart', code, delay: 0 };
    }
    const pairingTimedOut = (code === this.reasons.timedOut || code === this.reasons.connectionLost)
      && this.state.pairingState !== 'paired';
    if (pairingTimedOut) {
      this.qrRetries += 1;
      this.state.pairingState = 'qr_expired';
      if (this.qrRetries > this.maxQrRetries) {
        this.cancel();
        this.state.lastError = 'QR pairing expired after repeated retries; start pairing again';
        return { action: 'stop', code };
      }
      const delay = Math.min(this.maxDelayMs, this.baseDelayMs * 2 ** (this.qrRetries - 1));
      this.state.lastError = 'QR expired; preparing a fresh code';
      this.schedule(delay);
      return { action: 'retry_qr', code, delay };
    }

    const delay = Math.min(this.maxDelayMs, this.baseDelayMs * 2 ** Math.min(this.attempts++, 5));
    this.state.lastError = 'Connection interrupted; reconnecting';
    this.schedule(delay);
    return { action: 'reconnect', code, delay };
  }
}
