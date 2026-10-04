import base64
import hashlib
import json
import os
from pathlib import Path
import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from ishti import wa_bridge as bridge


@pytest.fixture
def scratch(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge, 'DATA', str(tmp_path))
    key = bytes(range(32))
    monkeypatch.setattr(bridge, '_credentials', lambda create=False: {'key': base64.b64encode(key).decode(), 'token': 'test_token' * 5})
    return tmp_path, key


def test_find_chat_never_guesses_empty_prefix_or_ambiguous_name(monkeypatch):
    monkeypatch.setattr(bridge, 'chats', lambda: [{'id': 'user@example.com', 'name': 'Ali'}, {'id': 'user@example.com', 'name': 'Ali'}])
    assert bridge.find_chat('') == ''
    assert bridge.find_chat('Al') == ''
    assert bridge.find_chat('Ali') == ''
    assert bridge.find_chat('+1 (000) 000-0001') == 'user@example.com'


def test_staging_encrypts_exact_approved_bytes_in_node_format(scratch):
    directory, key = scratch
    original = directory / 'invoice.pdf'
    original.write_bytes(b'%PDF-synthetic private invoice')
    digest = hashlib.sha256(original.read_bytes()).hexdigest()
    media, target = bridge._stage_media(str(original), '', digest)
    stored = target.read_bytes()
    assert b'private invoice' not in stored
    assert media['kind'] == 'document'
    assert media['mimetype'] == 'application/pdf'
    decrypted = AESGCM(key).decrypt(stored[4:16], stored[32:] + stored[16:32], media['file'].encode())
    assert decrypted == original.read_bytes()


def test_changed_attachment_and_wav_voice_rejected(scratch):
    directory, _key = scratch
    original = directory / 'test.wav'; original.write_bytes(b'RIFF synthetic')
    with pytest.raises(bridge.BridgeError, match='changed'):
        bridge._stage_media(str(original), '', '0' * 64)
    with pytest.raises(bridge.BridgeError, match='Ogg Opus'):
        bridge._stage_media(str(original), 'voice', None)


def test_live_operations_disabled_in_test_mode(monkeypatch):
    monkeypatch.setenv('ISHTI_TEST_MODE', '1')
    monkeypatch.setattr(bridge, '_call', lambda *_a, **_k: pytest.fail('network called'))
    for call in [bridge.start, lambda: bridge.send('10000000001', 'test', action_id='test_action_12345'), lambda: bridge.mark_read('10000000001')]:
        with pytest.raises(bridge.BridgeError): call()


def test_errors_are_not_empty_successful_reads(monkeypatch):
    def unavailable(*_a, **_k): raise bridge.BridgeError('Unavailable')
    monkeypatch.setattr(bridge, '_call', unavailable)
    with pytest.raises(bridge.BridgeError): bridge.messages('user@example.com')
    with pytest.raises(bridge.BridgeError): bridge.chats()
    assert bridge.status() == {'connected': False, 'running': False}


def test_qr_response_is_one_status_read_and_contains_image_not_raw_credentials(monkeypatch):
    calls = []
    monkeypatch.setattr(bridge, 'status', lambda: calls.append(1) or {'running': True, 'connected': False, 'qr_matrix': [[True] * 21 for _ in range(21)]})
    qr = bridge.get_qr_data()
    assert qr['qr_image'].startswith('data:image/png;base64,')
    assert qr['raw_qr'] is None and qr['ascii_qr'] == '' and calls == [1]


def test_deferred_mutations_never_call_network(monkeypatch):
    monkeypatch.setattr(bridge, '_call', lambda *_a, **_k: pytest.fail('network called'))
    for fn in [bridge.edit, bridge.delete, bridge.react]: assert fn('chat', 'key')['ok'] is False


def test_send_timeout_does_not_replay(monkeypatch):
    monkeypatch.delenv('ISHTI_TEST_MODE', raising=False)
    calls = []
    def unavailable(*args, **kwargs):
        calls.append(args); raise bridge.BridgeError('uncertain')
    monkeypatch.setattr(bridge, '_call', unavailable)
    with pytest.raises(bridge.BridgeError): bridge.send('10000000001', 'synthetic', action_id='test_action_12345')
    assert len(calls) == 1 and calls[0][0] == '/send'


def test_missing_key_preserves_existing_encrypted_session(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge, 'DATA', str(tmp_path)); monkeypatch.delenv('ISHTI_TEST_MODE', raising=False)
    (tmp_path / 'encrypted_state').mkdir(); (tmp_path / 'encrypted_state' / 'existing.enc').write_bytes(b'ciphertext')
    with pytest.raises(bridge.BridgeError, match='missing'): bridge._credentials(create=True)
    assert not (tmp_path / 'bootstrap.dpapi').exists()


@pytest.mark.skipif(os.name != 'nt', reason='Windows DPAPI integration')
def test_native_dpapi_roundtrip_uses_bytes_without_exposing_synthetic_secret():
    encrypted = bridge._protect(b'synthetic credential verification')
    assert isinstance(encrypted, bytes)
    assert b'synthetic credential verification' not in encrypted
    assert bridge._unprotect(encrypted) == b'synthetic credential verification'
