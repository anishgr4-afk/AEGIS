import json
import sqlite3
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from conftest import API, failure, parse_utc, success


@pytest.fixture
def clock(app, monkeypatch):
    ops = sys.modules['backend.ops']
    current = [datetime.now(timezone.utc)]
    monkeypatch.setattr(ops, 'now', lambda: current[0].isoformat().replace('+00:00', 'Z'))
    return current


def row(api, ident, role='operator'):
    return next(r for r in success(api.get('/api/messages', role=role)) if r['id'] == ident)


def pdf_text(data):
    return subprocess.run(['pdftotext', '-', '-'], input=data, check=True, stdout=subprocess.PIPE).stdout.decode('utf-8')


def test_incoming_is_withheld_in_post_retry_get_sync_and_exports_until_due(api, clock):
    success(api.patch('/api/settings', json={'earth_connected': True, 'delay_minutes': 18.7}))
    payload = {'direction': 'incoming', 'subject': 'secret-subject-4871', 'body': 'secret-body-4871', 'priority': 'high'}
    key = str(uuid.uuid4())
    headers = {'Idempotency-Key': key}
    response = api.post('/api/messages', role='operator', json=payload, headers=headers)
    created = success(response)
    assert response.status_code == 201 and created['status'] == 'transmitted'
    assert created['content_pending'] and 'subject' not in created and 'body' not in created
    assert parse_utc(created['due_at']) - parse_utc(created['transmitted_at']) == timedelta(minutes=18.7)
    assert success(api.post('/api/messages', role='operator', json=payload, headers=headers)) == created
    assert row(api, created['id']) == created
    failure(api.post(f"/api/messages/{created['id']}/acknowledge", role='operator'), (409,))
    for fmt in ('json', 'csv', 'pdf'):
        export = api.get('/api/export/' + fmt, role='operator')
        assert export.status_code == 200
        text = pdf_text(export.data) if fmt == 'pdf' else export.get_data(as_text=True)
        assert payload['subject'] not in text and payload['body'] not in text
    # A simulated failure and retry retain history, without exposing content.
    failed = success(api.post(f"/api/messages/{created['id']}/fail", role='operator', json={'reason': 'simulated failure'}))
    assert failed['status'] == 'failed' and 'body' not in failed
    retried = success(api.post(f"/api/messages/{created['id']}/retry", role='operator'))
    assert retried['status'] == 'transmitted' and 'subject' not in retried
    clock[0] = parse_utc(retried['due_at']) - timedelta(microseconds=1)
    assert row(api, created['id'])['status'] == 'transmitted'
    clock[0] = parse_utc(retried['due_at'])
    delivered = row(api, created['id'])  # polling alone progresses due messages
    assert delivered['status'] == 'delivered'
    assert delivered['subject'] == payload['subject'] and delivered['body'] == payload['body']
    assert parse_utc(delivered['delivered_at']) >= parse_utc(delivered['due_at'])
    assert [h['status'] for h in delivered['history']] == ['queued', 'transmitted', 'failed', 'transmitted', 'delivered']
    acknowledged = success(api.post(f"/api/messages/{created['id']}/acknowledge", role='operator'))
    assert acknowledged['acknowledgment_scope'] == 'local_record'
    assert acknowledged['status'] == 'acknowledged'
    assert parse_utc(acknowledged['acknowledged_at']) < parse_utc(created['transmitted_at']) + timedelta(minutes=37.4)
    # Idempotent write replay returns the original response, not a later state.
    assert success(api.post('/api/messages', role='operator', json=payload, headers=headers)) == created
    audit = success(api.get('/api/audit'))
    assert payload['subject'] not in json.dumps(audit) and payload['body'] not in json.dumps(audit)
    assert sum(e['action'] == 'message_create' and e['entity_id'] == created['id'] for e in audit) == 1


def test_sync_incoming_and_offline_reconnection_keep_content_hidden(api, clock):
    success(api.patch('/api/settings', json={'earth_connected': False, 'delay_minutes': 18.7}))
    payload = {'direction': 'incoming', 'subject': 'offline-incoming-subject', 'body': 'offline-incoming-body', 'priority': 'normal'}
    created = success(api.post('/api/messages', role='operator', json=payload))
    assert created['status'] == 'queued' and created['content_pending']
    clock[0] += timedelta(days=1)
    assert row(api, created['id'])['status'] == 'queued'
    success(api.patch('/api/settings', json={'earth_connected': True}))
    started = row(api, created['id'])
    assert started['status'] == 'transmitted' and 'body' not in started
    ident = str(uuid.uuid4())
    item = {'id': ident, **payload}
    assert ident in success(api.post('/api/sync', role='operator', json={'messages': [item]}))['added']
    assert ident in success(api.post('/api/sync', role='operator', json={'messages': [item]}))['duplicates']
    assert success(api.post('/api/sync', role='operator', json={'messages': [{**item, 'body': 'different'}]}))['conflicts']
    assert 'body' not in row(api, ident)
    clock[0] = parse_utc(started['due_at'])
    assert row(api, created['id'])['body'] == payload['body']


def test_outgoing_acknowledgment_is_local_only_and_no_remote_reply(api, clock):
    success(api.patch('/api/settings', json={'delay_minutes': 18.7, 'earth_connected': True}))
    sent = success(api.post('/api/messages', role='operator', json={'subject': 'outbound', 'body': 'payload'}))
    failure(api.post(f"/api/messages/{sent['id']}/acknowledge", role='operator'), (409,))
    clock[0] = parse_utc(sent['due_at'])
    assert row(api, sent['id'])['status'] == 'delivered'
    ack = success(api.post(f"/api/messages/{sent['id']}/acknowledge", role='operator'))
    assert ack['acknowledgment_scope'] == 'local_record'
    assert parse_utc(ack['acknowledged_at']) < parse_utc(sent['transmitted_at']) + timedelta(minutes=37.4)
    clock[0] += timedelta(minutes=19)
    assert row(api, sent['id'])['status'] == 'acknowledged'
    assert not any(m.get('direction') == 'incoming' for m in success(api.get('/api/messages', role='operator')))


def test_incident_sensitivity_blocks_visibility_history_actions_and_exports(api, app):
    sensitive = success(api.post('/api/incidents', role='medical', json={
        'title': 'private-medical-incident-093', 'report': 'private-treatment-093',
        'checklist': [{'text': 'private-checklist-093', 'done': False}], 'sensitivity': 'medical'}))
    ident = sensitive['id']
    revised = success(api.patch(f'/api/incidents/{ident}', role='medical', json={'report': 'private-revision-093'}))
    assert 'private-treatment-093' in json.dumps(revised['history'])
    operational = success(api.post('/api/incidents', role='operator', json={'title': 'Operational event', 'report': 'Operational report'}))
    assert operational['sensitivity'] == 'operational'
    failure(api.post('/api/incidents', role='operator', json={'title': 'x', 'sensitivity': 'medical'}), (403,))
    failure(api.patch(f'/api/incidents/{ident}', role='operator', json={'status': 'resolved'}), (404,))
    failure(api.post(f'/api/incidents/{ident}/acknowledge', role='operator'), (404,))
    failure(api.patch(f'/api/incidents/{ident}', role='medical', json={'sensitivity': 'operational'}), (409,))
    # Previously unclassified records default to restricted rather than leaking legacy notes.
    core = sys.modules['backend.core']
    with app.app_context():
        core.insert_record('incident', {'title': 'legacy-private-093', 'report': 'legacy-report-093', 'status': 'open'}, 'legacy-incident')
        core.get_db().commit()
    operator = success(api.get('/api/incidents', role='operator'))
    assert any(i['id'] == operational['id'] for i in operator)
    for marker in ('private-medical-incident-093', 'private-treatment-093', 'private-revision-093', 'private-checklist-093', 'legacy-private-093', 'legacy-report-093'):
        assert marker not in json.dumps(operator)
        for fmt in ('json', 'csv', 'pdf'):
            response = api.get('/api/export/' + fmt, role='operator')
            assert response.status_code == 200
            text = pdf_text(response.data) if fmt == 'pdf' else response.get_data(as_text=True)
            assert marker not in text
    medical = success(api.get('/api/incidents', role='medical'))
    assert ident in {i['id'] for i in medical} and 'legacy-incident' in {i['id'] for i in medical}
    assert 'private-revision-093' in api.get('/api/export/json', role='medical').get_data(as_text=True)


def test_medication_idempotency_is_atomic_concurrent_and_survives_restart(api, app_factory, crew_id):
    med = success(api.post('/api/medications', role='medical', json={'name': 'Demo', 'quantity': 10, 'unit': 'vials', 'expiry_date': '2030-01-01'}))
    path = f"/api/medications/{med['id']}/administer"
    payload = {'crew_id': crew_id, 'quantity': 3, 'note': 'simulated'}
    key = str(uuid.uuid4())
    token = api.login('medical')
    def send():
        client = api.client.application.test_client()
        return client.post(path, json=payload, headers={'Authorization': 'Bearer ' + token, 'Idempotency-Key': key})
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: send(), range(2)))
    assert [r.status_code for r in responses] == [201, 201]
    assert responses[0].get_json() == responses[1].get_json()
    failure(api.post(path, role='medical', json={**payload, 'quantity': 2}, headers={'Idempotency-Key': key}), (409,))
    failure(api.post('/api/messages', role='medical', json={'subject': 'x', 'body': 'y'}, headers={'Idempotency-Key': key}), (409,))
    restarted = API(app_factory().test_client())
    replay = restarted.post(path, role='medical', json=payload, headers={'Idempotency-Key': key})
    assert replay.status_code == 201 and replay.get_json() == responses[0].get_json()
    assert next(x for x in success(restarted.get('/api/medications', role='medical')) if x['id'] == med['id'])['quantity'] == 7
    assert len([x for x in success(restarted.get('/api/administrations', role='medical')) if x['medication_id'] == med['id']]) == 1
    assert sum(e['action'] == 'medication_administer' for e in success(restarted.get('/api/audit'))) == 1
    failure(restarted.post(path, role='medical', json=payload, headers={'Idempotency-Key': 'not-a-uuid'}), (400,))


def test_message_key_conflict_and_legacy_id_dedup_remain_compatible(api):
    payload = {'direction': 'outgoing', 'subject': 'original', 'body': 'text'}
    key = str(uuid.uuid4())
    first = api.post('/api/messages', role='operator', json={**payload, 'idempotency_key': key})
    assert first.status_code == 201
    again = api.post('/api/messages', role='operator', json={**payload, 'idempotency_key': key})
    assert again.status_code == 201 and again.get_json() == first.get_json()
    failure(api.post('/api/messages', role='operator', json={**payload, 'body': 'changed', 'idempotency_key': key}), (409,))
    ident = str(uuid.uuid4())
    legacy = success(api.post('/api/messages', role='operator', json={**payload, 'id': ident}))
    assert success(api.post('/api/messages', role='operator', json={**payload, 'id': ident}))['id'] == legacy['id']
    failure(api.post('/api/messages', role='operator', json={**payload, 'id': ident, 'subject': 'different'}), (409,))


def test_concurrent_message_retries_create_one_record(api):
    token = api.login('operator')
    key = str(uuid.uuid4())
    payload = {'direction': 'outgoing', 'subject': 'parallel', 'body': 'same'}
    def send():
        client = api.client.application.test_client()
        return client.post('/api/messages', json=payload, headers={'Authorization': 'Bearer ' + token, 'Idempotency-Key': key})
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: send(), range(2)))
    assert [r.status_code for r in responses] == [201, 201]
    assert responses[0].get_json() == responses[1].get_json()
    ident = responses[0].get_json()['id']
    assert sum(r['id'] == ident for r in success(api.get('/api/messages', role='operator'))) == 1


def test_failed_medication_transaction_does_not_reserve_key(api, crew_id):
    med = success(api.post('/api/medications', role='medical', json={'name': 'Demo', 'quantity': 2, 'unit': 'vials'}))
    path = f"/api/medications/{med['id']}/administer"
    key = {'Idempotency-Key': str(uuid.uuid4())}
    failure(api.post(path, role='medical', json={'crew_id': crew_id, 'quantity': 3}, headers=key), (409,))
    result = success(api.post(path, role='medical', json={'crew_id': crew_id, 'quantity': 1}, headers=key))
    assert result['quantity'] == 1
    assert next(x for x in success(api.get('/api/medications', role='medical')) if x['id'] == med['id'])['quantity'] == 1
