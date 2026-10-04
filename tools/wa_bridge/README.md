# Local WhatsApp bridge

Start from the repository with the configured Python runtime:

```
python -m ishti.wa_bridge --start
```

Settings → Inputs → WhatsApp → Link also starts the bridge and displays a rotating QR. Link with WhatsApp → Linked devices on the phone. No terminal QR, authentication key or phone number is printed to the bridge log. This is a third-party WhatsApp Web transport; it is not the official Business Platform API.

The loopback API requires a bearer credential and rejects browser origins, foreign hosts and proxy headers. Windows DPAPI protects the launch credentials. SDK auth state, action ledger and staged attachment bytes use AES-GCM. No old history is imported automatically; retained messages exist in memory for this process with bounded per-chat retention. Chrome remains available for older history.

Images, videos, documents, regular audio and native Ogg Opus voice notes have separate payloads. A caller supplies an action ID and attachment digest. An identical action ID cannot submit again, including after timeout or restart; a changed draft with the same ID is rejected. Submitted, server-acknowledged, delivered and read are different outcomes. Editing, deletion and reactions are deferred.

Set `ISHTI_WA_TEST_DESTINATION` only for authorized self-tests. Both the destination and linked account must match that number. Synthetic offline tests do not connect to WhatsApp:

```
node --test test/bridge.test.js
```

SDK 6.7.24 is pinned. Review [the official security policy](https://github.com/WhiskeySockets/Baileys/security) and [GHSA-qvv5-jq5g-4cgg](https://github.com/advisories/GHSA-qvv5-jq5g-4cgg) before updates. That advisory's legacy affected range is below 6.7.22.

Limitations: Windows credential storage is currently required; legacy plaintext sessions need explicit migration; historical sync is not implemented; action ledger is capped at 10000 entries; periodic encrypted attachment cleanup and end-to-end Business-account compatibility are still acceptance work. Do not treat a successful unit test as live delivery proof.
