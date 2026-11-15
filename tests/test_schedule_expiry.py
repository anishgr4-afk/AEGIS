"""Regression coverage for the local-only message scheduler and expiry lead time."""
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from conftest import API, failure, parse_utc, success


def stored_message(db_path, ident):
    # Read SQLite directly: GET /api/messages itself advances the simulation.
    with sqlite3.connect(db_path, timeout=20) as db:
        row = db.execute("SELECT data FROM records WHERE kind='message' AND id=?", (ident,)).fetchone()
    assert row is not None
    return json.loads(row[0])


def wait_until(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.03)
    pytest.fail("Background state did not reach the expected condition in time")


def enabled_app(app_factory):
    module = sys.modules['backend.app']
    return module.create_app({'TESTING': True, 'DATABASE': str(app_factory.db_path),
                              'SIMULATION_ENABLED': True, 'SIMULATION_INTERVAL': 3600,
                              'MESSAGE_SCHEDULER_INTERVAL': 0.05})


def stop_app(app):
    app.extensions['checkpoint_simulation_stop'].set()
    app.extensions['checkpoint_message_scheduler_thread'].join(timeout=3)
    app.extensions['checkpoint_simulation_thread'].join(timeout=3)
    assert not app.extensions['checkpoint_message_scheduler_thread'].is_alive()
    assert not app.extensions['checkpoint_simulation_thread'].is_alive()


def create(api, direction='outgoing'):
    return success(api.post('/api/messages', role='operator', json={
        'direction': direction, 'subject': 'simulated status', 'body': 'local demo only'}))


def test_exact_due_boundary_and_honest_scheduler_audit(api, app, app_factory, monkeypatch):
    ops, module = sys.modules['backend.ops'], sys.modules['backend.app']
    clock = [datetime(2032, 1, 15, 12, tzinfo=timezone.utc)]
    monkeypatch.setattr(ops, 'now', lambda: clock[0].isoformat().replace('+00:00', 'Z'))
    success(api.patch('/api/settings', json={'earth_connected': True, 'delay_minutes': 1}))
    message = create(api, 'incoming')
    assert message['status'] == 'transmitted' and message['content_pending']
    assert 'body' not in message and 'subject' not in message
    due = parse_utc(message['due_at'])
    clock[0] = due - timedelta(microseconds=1)
    assert module.schedule_messages_once(app) == []
    assert stored_message(app_factory.db_path, message['id'])['status'] == 'transmitted'
    clock[0] = due
    assert module.schedule_messages_once(app) == [message['id']]
    delivered = stored_message(app_factory.db_path, message['id'])
    assert parse_utc(delivered['delivered_at']) == due
    assert delivered['acknowledged_at'] is None
    assert delivered['history'][-1]['actor_id'] == 'message-scheduler'
    audit = success(api.get('/api/audit'))
    transitions = [r for r in audit if r['entity_id'] == message['id'] and r['action'] == 'message_delivered']
    assert len(transitions) == 1 and transitions[0]['actor'] == 'message-scheduler'
    assert api.get('/api/audit').headers['X-Audit-Chain-Valid'] == 'true'


def test_background_delivery_without_any_client_polling(api, app_factory):
    success(api.patch('/api/settings', json={'earth_connected': True, 'delay_minutes': 0.05}))
    message = create(api, 'incoming')
    due = parse_utc(message['due_at'])
    worker = enabled_app(app_factory)
    try:
        assert success(api.get('/api/settings'))['message_scheduler_enabled'] is False
        assert success(API(worker.test_client()).get('/api/settings'))['message_scheduler_enabled'] is True
        # No message or sync request is made; even inspecting state reads SQLite directly.
        time.sleep(0.2)
        assert datetime.now(timezone.utc) < due
        assert stored_message(app_factory.db_path, message['id'])['status'] == 'transmitted'
        row = wait_until(lambda: (m if (m := stored_message(app_factory.db_path, message['id']))['status'] == 'delivered' else None))
        assert parse_utc(row['delivered_at']) >= due
        assert row['acknowledged_at'] is None
        assert [h['status'] for h in row['history']] == ['queued', 'transmitted', 'delivered']
        acknowledged = success(api.post(f"/api/messages/{message['id']}/acknowledge", role='operator'))
        assert acknowledged['acknowledgment_scope'] == 'local_record'
        assert parse_utc(acknowledged['acknowledged_at']) >= parse_utc(row['delivered_at'])
        assert not acknowledged.get('remote_acknowledged_at')
    finally:
        stop_app(worker)


def test_background_starts_queued_message_after_interrupted_reconnect(api, app, app_factory):
    success(api.patch('/api/settings', json={'earth_connected': False, 'delay_minutes': 0.02}))
    queued = create(api)
    # Model a restart after a persisted reconnection but before its request hook
    # could synchronize. Only the worker is allowed to advance the queue.
    core = sys.modules['backend.core']
    with app.app_context():
        db = core.get_db()
        db.execute('BEGIN IMMEDIATE')
        core.update_record('settings', 'mission', {'earth_connected': True})
        core.record_audit('test_connection_restore', 'settings', 'mission', after={'earth_connected': True})
        db.commit()
    worker = enabled_app(app_factory)
    try:
        delivered = wait_until(lambda: (m if (m := stored_message(app_factory.db_path, queued['id']))['status'] == 'delivered' else None))
        assert delivered['attempts'] == 1
        assert parse_utc(delivered['due_at']) > parse_utc(delivered['transmitted_at'])
        assert parse_utc(delivered['delivered_at']) >= parse_utc(delivered['due_at'])
        assert [h['status'] for h in delivered['history']] == ['queued', 'transmitted', 'delivered']
    finally:
        stop_app(worker)


def test_disconnected_traffic_waits_and_reconnection_resumes(api, app_factory):
    success(api.patch('/api/settings', json={'earth_connected': True, 'delay_minutes': 0.02}))
    transmitted = create(api)
    success(api.patch('/api/settings', json={'earth_connected': False}))
    queued = create(api, 'incoming')
    worker = enabled_app(app_factory)
    try:
        time.sleep(1.6)  # beyond the transmitted message's persisted due time
        assert datetime.now(timezone.utc) >= parse_utc(transmitted['due_at'])
        assert stored_message(app_factory.db_path, transmitted['id'])['status'] == 'transmitted'
        assert stored_message(app_factory.db_path, queued['id'])['status'] == 'queued'
        success(api.patch('/api/settings', json={'earth_connected': True}))
        queued_row = stored_message(app_factory.db_path, queued['id'])
        assert queued_row['status'] == 'transmitted'
        assert parse_utc(queued_row['due_at']) > parse_utc(queued_row['transmitted_at'])
        row = wait_until(lambda: (m if (m := stored_message(app_factory.db_path, queued['id']))['status'] == 'delivered' else None))
        assert parse_utc(row['delivered_at']) >= parse_utc(row['due_at'])
        assert stored_message(app_factory.db_path, transmitted['id'])['status'] == 'delivered'
    finally:
        stop_app(worker)


def test_disabled_worker_and_restart_catch_up_overdue_message(api, app, app_factory):
    assert 'checkpoint_message_scheduler_thread' not in app.extensions
    success(api.patch('/api/settings', json={'earth_connected': True, 'delay_minutes': 0.01}))
    message = create(api)
    time.sleep(0.8)
    assert stored_message(app_factory.db_path, message['id'])['status'] == 'transmitted'
    # This app uses the same persisted DB, as a restarted process would.
    worker = enabled_app(app_factory)
    try:
        row = wait_until(lambda: (m if (m := stored_message(app_factory.db_path, message['id']))['status'] == 'delivered' else None))
        assert parse_utc(row['delivered_at']) >= parse_utc(message['due_at'])
        assert row['acknowledged_at'] is None
    finally:
        stop_app(worker)


def test_two_processes_share_lease_and_do_not_duplicate_delivery(api, app_factory):
    success(api.patch('/api/settings', json={'earth_connected': True, 'delay_minutes': 0}))
    message = create(api)
    root = str(Path(__file__).resolve().parents[1])
    env = dict(os.environ, CHECKPOINT_SIMULATION='0', CHECKPOINT_DB_PATH=str(app_factory.db_path))
    script = '''
import time
from backend.app import create_app
app = create_app({'TESTING': True, 'SIMULATION_ENABLED': True, 'SIMULATION_INTERVAL': 3600, 'MESSAGE_SCHEDULER_INTERVAL': .05})
time.sleep(1)
app.extensions['checkpoint_simulation_stop'].set()
app.extensions['checkpoint_message_scheduler_thread'].join(3)
app.extensions['checkpoint_simulation_thread'].join(3)
'''
    processes = [subprocess.Popen([sys.executable, '-c', script], cwd=root, env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
    try:
        for process in processes:
            _, stderr = process.communicate(timeout=15)
            assert process.returncode == 0, stderr.decode()
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait()
    row = stored_message(app_factory.db_path, message['id'])
    assert row['status'] == 'delivered'
    assert [h['status'] for h in row['history']].count('delivered') == 1
    entries = success(api.get('/api/audit'))
    assert len([e for e in entries if e['entity_id'] == message['id'] and e['action'] == 'message_delivered']) == 1
    assert api.get('/api/audit').headers['X-Audit-Chain-Valid'] == 'true'


def test_custom_expiry_lead_utc_boundaries_and_permissions(api, app, app_factory, monkeypatch):
    ops = sys.modules['backend.ops']
    monkeypatch.setattr(ops, 'now', lambda: '2032-01-15T12:00:00Z')
    assert success(api.get('/api/settings'))['expiry_warning_days'] == 30
    failure(api.patch('/api/settings', role='operator', json={'expiry_warning_days': 7}), (403,))
    for value in (-1, 36501, 1.5, True, '7', None):
        failure(api.patch('/api/settings', json={'expiry_warning_days': value}), (400,))
    assert success(api.patch('/api/settings', json={'expiry_warning_days': 7}))['expiry_warning_days'] == 7
    cases = [(-1, True, False), (0, False, True), (7, False, True), (8, False, False), (30, False, False)]
    today = datetime(2032, 1, 15, tzinfo=timezone.utc).date()
    created = {}
    for offset, expired, soon in cases:
        med = success(api.post('/api/medications', role='medical', json={
            'name': f'Demo-{offset}', 'quantity': 2, 'unit': 'units', 'low_stock_threshold': 2,
            'expiry_date': (today + timedelta(days=offset)).isoformat()}))
        assert med['expired'] is expired and med['expiring_soon'] is soon
        assert med['low_stock'] is True and med['expiry_warning_days'] == 7
        created[offset] = med['id']
    # Read, update and privileged export all use the same current rule.
    success(api.patch('/api/settings', json={'expiry_warning_days': 0}))
    rows = {r['id']: r for r in success(api.get('/api/medications', role='medical'))}
    assert rows[created[0]]['expiring_soon'] is True
    assert rows[created[7]]['expiring_soon'] is False
    edited = success(api.patch(f"/api/medications/{created[7]}", role='medical', json={'quantity': 3}))
    assert edited['expiring_soon'] is False and edited['low_stock'] is False
    payload = api.get('/api/export/json', role='medical').get_json()
    exported = next(m for m in payload['medications'] if m['id'] == created[7])
    assert exported['expiry_warning_days'] == 0 and exported['expiring_soon'] is False
    assert success(api.patch('/api/settings', json={'expiry_warning_days': 36500}))['expiry_warning_days'] == 36500
    assert success(API(app_factory().test_client()).get('/api/settings'))['expiry_warning_days'] == 36500
    assert api.get('/api/audit').headers['X-Audit-Chain-Valid'] == 'true'


def test_legacy_settings_migrated_once_and_audited(api, app_factory):
    with sqlite3.connect(app_factory.db_path) as db:
        db.execute("UPDATE records SET data=json_remove(data, '$.expiry_warning_days') WHERE kind='settings' AND id='mission'")
    restarted = API(app_factory().test_client())
    assert success(restarted.get('/api/settings'))['expiry_warning_days'] == 30
    audits = success(restarted.get('/api/audit'))
    migrations = [e for e in audits if e['action'] == 'settings_default_migration']
    assert migrations[-1]['after']['expiry_warning_days'] == 30
    before = len([e for e in audits if e['action'] == 'settings_default_migration'])
    again = API(app_factory().test_client())
    assert len([e for e in success(again.get('/api/audit')) if e['action'] == 'settings_default_migration']) == before
    assert again.get('/api/audit').headers['X-Audit-Chain-Valid'] == 'true'
