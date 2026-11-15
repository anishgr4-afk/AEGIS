"""Security upgrade checks use fictional credentials and temporary local databases."""
import hashlib
import json
import sqlite3
import time

import pytest

from conftest import PASSWORD


NEW_PASSWORD = 'fictional-orbit river lantern 42'
OTHER_PASSWORD = 'another quiet orbital lantern 57'


def sign_in(client, username, password=PASSWORD, **kwargs):
    return client.post('/api/auth/login', json={'username': username, 'password': password}, **kwargs)


def bearer(token):
    return {'Authorization': 'Bearer ' + token}


def token(client, username='operator', password=PASSWORD):
    response = sign_in(client, username, password)
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()['token']


def change(client, value, current=PASSWORD, session=None, **extra):
    payload = {'current_password': current, 'new_password': value, **extra}
    return client.post('/api/auth/password', json=payload, headers=bearer(session) if session else {})


def test_uniform_login_failures_and_no_account_disclosure(client, app_factory):
    invalid = sign_in(client, 'operator', 'wrong-secret-for-testing')
    unknown = sign_in(client, 'not-a-real-account', 'wrong-secret-for-testing')
    malformed = client.post('/api/auth/login', json={})
    assert invalid.status_code == unknown.status_code == malformed.status_code == 401
    assert invalid.get_json() == unknown.get_json() == malformed.get_json() == {'error': 'Invalid username or password'}
    with sqlite3.connect(app_factory.db_path) as db:
        db.execute("UPDATE users SET active=0 WHERE username='medical'")
    inactive = sign_in(client, 'medical')
    assert inactive.status_code == 401 and inactive.get_json() == invalid.get_json()


def test_persistent_account_throttle_survives_restart_and_shared_proxy(app_factory):
    client = app_factory().test_client()
    for i in range(5):
        # Distinct network addresses and fake forwarded headers cannot escape a target-account limit.
        response = sign_in(client, 'operator', 'wrong', environ_base={'REMOTE_ADDR': f'10.0.0.{i + 1}'},
                           headers={'X-Forwarded-For': f'198.51.100.{i + 1}'})
        assert response.status_code == 401
    restarted = app_factory().test_client()
    blocked = sign_in(restarted, 'OpErAtOr', PASSWORD, environ_base={'REMOTE_ADDR': '10.0.0.99'},
                      headers={'X-Forwarded-For': '203.0.113.14'})
    assert blocked.status_code == 429
    assert 1 <= int(blocked.headers['Retry-After']) <= 300
    # The same proxy address remains usable by other accounts even after five failures.
    assert sign_in(restarted, 'commander', PASSWORD, environ_base={'REMOTE_ADDR': '10.0.0.99'}).status_code == 200
    with sqlite3.connect(app_factory.db_path) as db:
        rows = db.execute('SELECT account_key,failures FROM auth_failures').fetchall()
        assert rows and rows[0][1] == 5
        assert all('operator' not in key.lower() and key != hashlib.sha256(b'operator').hexdigest() for key, _ in rows)
        db.execute('UPDATE auth_failures SET window_start=?', (time.time() - 301,))
    assert sign_in(restarted, 'operator').status_code == 200


def test_throttling_unknown_and_known_users_respond_identically(client):
    for i in range(6):
        known = sign_in(client, 'medical', 'invalid-test-credential')
        unknown = sign_in(client, 'invented-user', 'invalid-test-credential')
        assert known.status_code == unknown.status_code == (401 if i < 5 else 429)
        assert known.get_json() == unknown.get_json()


def test_status_is_current_account_only_and_does_not_expose_hashes(client):
    assert client.get('/api/auth/security').status_code == 401
    operator = token(client, 'operator')
    admin = token(client, 'admin')
    result = client.get('/api/auth/security?user_id=ignored', headers=bearer(operator))
    assert result.status_code == 200
    assert result.get_json() == {'demo_password_in_use': True, 'min_password_length': 12, 'max_password_length': 256}
    assert PASSWORD not in result.get_data(as_text=True)
    assert 'hash' not in result.get_data(as_text=True).lower()
    assert change(client, NEW_PASSWORD, session=operator).status_code == 200
    new_token = token(client, 'operator', NEW_PASSWORD)
    assert client.get('/api/auth/security', headers=bearer(new_token)).get_json()['demo_password_in_use'] is False
    assert client.get('/api/auth/security', headers=bearer(admin)).get_json()['demo_password_in_use'] is True


def test_current_password_policy_and_target_identity(client):
    assert change(client, NEW_PASSWORD).status_code == 401
    operator = token(client)
    assert change(client, NEW_PASSWORD, current='incorrect-password', session=operator).status_code == 400
    assert client.get('/api/auth/session', headers=bearer(operator)).status_code == 200
    for bad in ('short', PASSWORD, 'password123456', '123456789012', 'a' * 12,
                ' leading whitespace', 'trailing whitespace ', 'x' * 257, 'with\nnewline password'):
        assert change(client, bad, session=operator).status_code == 400
    assert change(client, NEW_PASSWORD, session=operator, user_id='admin').status_code == 400
    assert sign_in(client, 'admin').status_code == 200
    assert change(client, NEW_PASSWORD, session=operator).status_code == 200
    fresh = token(client, 'operator', NEW_PASSWORD)
    same = change(client, NEW_PASSWORD, current=NEW_PASSWORD, session=fresh)
    assert same.status_code == 400 and 'differ' in same.get_json()['error'].lower()
    assert client.get('/api/auth/session', headers=bearer(fresh)).status_code == 200


def test_current_password_guessing_uses_same_persistent_limit(app_factory):
    client = app_factory().test_client()
    session = token(client)
    for i in range(5):
        assert change(client, NEW_PASSWORD, current=f'wrong-{i}', session=session).status_code == 400
    restarted = app_factory().test_client()
    assert change(restarted, NEW_PASSWORD, session=session).status_code == 429
    assert sign_in(restarted, 'operator').status_code == 429
    assert sign_in(restarted, 'medical').status_code == 200


def test_password_change_revokes_all_own_sessions_only_and_redacts_audit(client, app_factory):
    first = token(client)
    second = token(client)
    other = token(client, 'medical')
    result = change(client, NEW_PASSWORD, session=first)
    assert result.status_code == 200
    assert 'sign in again' in result.get_json()['message'].lower()
    for prior in (first, second):
        assert client.get('/api/auth/session', headers=bearer(prior)).status_code == 401
    assert client.get('/api/auth/session', headers=bearer(other)).status_code == 200
    assert sign_in(client, 'operator').status_code == 401
    assert sign_in(client, 'operator', NEW_PASSWORD).status_code == 200
    with sqlite3.connect(app_factory.db_path) as db:
        audit = json.dumps(db.execute('SELECT actor,action,entity_id,before_json,after_json FROM audit').fetchall())
        hashed = db.execute("SELECT password_hash FROM users WHERE username='operator'").fetchone()[0]
        assert hashed != NEW_PASSWORD and NEW_PASSWORD not in hashed
        assert 'password_changed' in audit
        for secret in (PASSWORD, NEW_PASSWORD, first, second, other, hashed):
            assert secret not in audit


def test_password_change_transaction_rolls_back_when_audit_fails(client, monkeypatch):
    import backend.app as app_module
    old = token(client)
    original = app_module.record_audit

    def fail_on_change(action, *args, **kwargs):
        if action == 'password_changed':
            raise RuntimeError('simulated audit write failure')
        return original(action, *args, **kwargs)

    monkeypatch.setattr(app_module, 'record_audit', fail_on_change)
    assert change(client, NEW_PASSWORD, session=old).status_code == 500
    assert client.get('/api/auth/session', headers=bearer(old)).status_code == 200
    assert sign_in(client, 'operator').status_code == 200
    assert sign_in(client, 'operator', NEW_PASSWORD).status_code == 401


@pytest.mark.parametrize('username,role', [
    ('commander', 'Commander'), ('medical', 'Medical Officer'),
    ('operator', 'Mission Operator'), ('admin', 'Administrator'),
])
def test_each_existing_role_can_change_own_password(client, username, role):
    old = token(client, username)
    response = change(client, NEW_PASSWORD, session=old)
    assert response.status_code == 200
    assert client.get('/api/auth/session', headers=bearer(old)).status_code == 401
    new = token(client, username, NEW_PASSWORD)
    assert client.get('/api/auth/session', headers=bearer(new)).get_json()['role'] == role


def test_admin_new_and_reset_password_policy_and_session_revocation(client):
    admin = token(client, 'admin')
    operator = token(client, 'operator')
    operator_id = client.get('/api/auth/session', headers=bearer(operator)).get_json()['id']
    for weak in ('short123', PASSWORD, 'password123456'):
        response = client.post('/api/users', json={'username': 'newuser', 'role': 'Commander', 'password': weak}, headers=bearer(admin))
        assert response.status_code == 400
        response = client.patch('/api/users/' + operator_id, json={'password': weak}, headers=bearer(admin))
        assert response.status_code == 400
    reset = client.patch('/api/users/' + operator_id, json={'password': OTHER_PASSWORD}, headers=bearer(admin))
    assert reset.status_code == 200
    assert client.get('/api/auth/session', headers=bearer(operator)).status_code == 401
    assert sign_in(client, 'operator', OTHER_PASSWORD).status_code == 200
    assert client.patch('/api/users/' + operator_id, json={'password': OTHER_PASSWORD}, headers=bearer(admin)).status_code == 400


def test_headers_sizes_and_preview_compatibility(client):
    session = token(client)
    for path, headers in [('/', {}), ('/api/health', {}), ('/api/auth/session', bearer(session))]:
        headers = {**headers, 'Origin': 'https://other.example'}
        response = client.get(path, headers=headers)
        assert response.headers['Cache-Control'] == 'no-store'
        assert response.headers['X-Content-Type-Options'] == 'nosniff'
        assert response.headers['Referrer-Policy'] == 'no-referrer'
        assert 'camera=()' in response.headers['Permissions-Policy']
        csp = response.headers['Content-Security-Policy']
        assert "default-src 'self'" in csp and "object-src 'none'" in csp
        assert 'frame-ancestors' not in csp and 'X-Frame-Options' not in response.headers
        assert 'Access-Control-Allow-Origin' not in response.headers
        assert 'Access-Control-Allow-Credentials' not in response.headers
    spoof = client.get('/api/health', headers={'X-Forwarded-Proto': 'https'})
    assert 'Strict-Transport-Security' not in spoof.headers
    secure = client.get('/api/health', base_url='https://localhost')
    assert 'Strict-Transport-Security' in secure.headers
    for method, path in [('POST', '/api/auth/login'), ('GET', '/api/health')]:
        response = client.open(path, method=method, data='x' * (128 * 1024 + 1), content_type='application/json')
        assert response.status_code == 413
        assert response.headers['Cache-Control'] == 'no-store'
