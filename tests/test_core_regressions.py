"""Regression checks for fictional telemetry; rules are not clinical guidance."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from conftest import success, utc_now


def matching(api, **criteria):
    rows = success(api.get('/api/alerts?limit=1000'))
    return [row for row in rows if all(row.get(k) == v for k, v in criteria.items())]


def test_distinct_critical_occurrences_survive_acknowledgement_and_gets(api, telemetry):
    first = success(api.post('/api/telemetry', json={**telemetry, 'id': 'critical-1', 'value': 150}))
    alert1 = matching(api, source_record_id=first['id'])[0]
    assert alert1['severity'] == 'CRITICAL' and not alert1['acknowledged']
    success(api.post(f"/api/alerts/{alert1['id']}/acknowledge", role='medical'))
    second = success(api.post('/api/telemetry', json={**telemetry, 'id': 'critical-2', 'value': 175}))
    alert2 = matching(api, source_record_id=second['id'])[0]
    assert alert2['id'] != alert1['id'] and alert2['value'] == 175
    assert not alert2['acknowledged'] and not alert2['resolved']
    assert matching(api, source_record_id=first['id'])[0]['acknowledged']
    # Idempotent replays and repeated dashboard reads cannot create occurrences.
    assert api.post('/api/telemetry', json={**telemetry, 'id': 'critical-2', 'value': 175}).status_code == 200
    for _ in range(3):
        success(api.get('/api/snapshot'))
    assert len(matching(api, source_record_id=second['id'])) == 1
    success(api.post('/api/telemetry', json={**telemetry, 'value': 75}))
    third = success(api.post('/api/telemetry', json={**telemetry, 'value': 165}))
    assert len(matching(api, source_record_id=third['id'], severity='CRITICAL', acknowledged=False)) == 1
    audit = success(api.get('/api/audit'))
    assert any(row['action'] == 'alert_generated' and row['entity_id'] == alert2['id'] for row in audit)


def test_resolved_persisting_freshness_rearms_once_and_recovery_closes_it(api):
    crew = success(api.post('/api/crew', json={'name': 'Fictional Test', 'role': 'Simulation'}))
    crew_id = crew['id']
    criteria = dict(entity_type='crew', entity_id=crew_id, metric='heart_rate', rule='missing')
    first = matching(api, **criteria)[0]
    success(api.post(f"/api/alerts/{first['id']}/acknowledge", role='medical'))
    assert len(matching(api, **criteria)) == 1
    success(api.post(f"/api/alerts/{first['id']}/resolve", role='medical'))
    found = matching(api, **criteria)
    assert len(found) == 2
    second = next(row for row in found if row['id'] != first['id'])
    assert not second['acknowledged'] and not second['resolved']
    for _ in range(3):
        success(api.get('/api/snapshot'))
    assert len(matching(api, **criteria)) == 2
    success(api.post('/api/telemetry', json={'crew_id': crew_id, 'metric': 'heart_rate', 'value': 72,
                                             'unit': 'bpm', 'source': 'fictional', 'timestamp': utc_now()}))
    recovered = matching(api, **criteria)
    assert all(row['resolved'] for row in recovered)
    assert any(row.get('resolution_reason') for row in recovered)


def test_mission_tagged_increment_crosses_identical_displayed_mission_total(api, crew_id):
    before = success(api.get('/api/snapshot'))['mission']['radiation_total']
    assert before < 0.9
    success(api.patch('/api/settings', json={'thresholds': {'radiation_cumulative':
        {'min': 0, 'max': 0.9, 'critical_min': 0, 'critical_max': 1, 'unit': 'mSv'}}}))
    # At exactly the critical bound the strict 'above critical_max' rule is false.
    value = float(Decimal('1') - Decimal(str(before)))
    success(api.post('/api/radiation', json={'crew_id': crew_id, 'value': value, 'unit': 'mSv', 'source': 'fictional'}))
    assert success(api.get('/api/snapshot'))['mission']['radiation_total'] == 1
    assert not matching(api, entity_type='radiation', entity_id='mission', severity='CRITICAL')
    increment = success(api.post('/api/radiation', json={'crew_id': 'mission', 'value': 0.0000004,
                                                       'unit': 'mSv', 'source': 'fictional'}))
    mission = success(api.get('/api/snapshot'))['mission']
    alert = matching(api, entity_type='radiation', entity_id='mission', severity='CRITICAL', source_record_id=increment['id'])
    assert len(alert) == 1 and alert[0]['value'] == mission['radiation_total'] == 1.0000004
    assert mission['radiation_by_crew']['mission'] == 0.0000004
    assert 'without deduplication' in mission['radiation_total_notice']


def test_bounded_snapshot_preserves_latest_totals_and_paged_history(app, api, crew_id):
    from backend.core import get_db, insert_record
    stamp = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    with app.app_context():
        db = get_db()
        db.execute('BEGIN IMMEDIATE')
        for i in range(2700):
            insert_record('measurement', {'crew_id': crew_id, 'metric': 'heart_rate', 'value': 75,
                'unit': 'bpm', 'source': 'bulk fictional', 'timestamp': stamp, 'simulated': True}, f'bulk-measurement-{i}')
        for i in range(1100):
            insert_record('radiation', {'crew_id': 'mission', 'value': 0.01, 'unit': 'mSv',
                'source': 'bulk fictional', 'timestamp': stamp, 'event': '', 'simulated': True}, f'bulk-dose-{i}')
        for i in range(510):
            insert_record('alert', {'entity_type': 'radiation', 'entity_id': 'mission', 'metric': 'radiation_cumulative',
                'value': 123, 'unit': 'mSv', 'rule': 'above critical_max', 'severity': 'CRITICAL',
                'detected_at': stamp, 'acknowledged': False, 'resolved': False, 'guidance': 'Fictional'}, f'bulk-alert-{i}')
        db.commit()
    snapshot = success(api.get('/api/snapshot'))
    windows = snapshot['history_window']
    for name in ('measurements', 'radiation', 'alerts'):
        assert windows[name]['truncated'] and windows[name]['returned'] <= windows[name]['limit']
        assert len(snapshot[name]) == windows[name]['returned']
        assert windows[name]['total'] > windows[name]['returned']
    assert windows['alerts']['active_critical'] >= 510
    assert windows['alerts']['unacknowledged_critical'] >= 510
    assert all(not a['acknowledged'] and a['severity'] == 'CRITICAL' for a in snapshot['alerts'])
    assert snapshot['mission']['radiation_by_crew']['mission'] == 11
    assert snapshot['mission']['radiation_total'] > 11
    # A high-volume metric must not displace latest readings of other metrics.
    for metric in ('spo2', 'bp_systolic', 'weight'):
        assert any(m['crew_id'] == crew_id and m['metric'] == metric for m in snapshot['measurements'])
    ids = set()
    for offset in range(0, 2700, 500):
        response = api.get(f'/api/telemetry?limit=500&offset={offset}&crew_id={crew_id}&metric=heart_rate')
        page = success(response)
        assert response.headers['X-Total-Count'] == str(2713)
        assert response.headers['X-Limit'] == '500' and response.headers['X-Offset'] == str(offset)
        assert not (ids & {r['id'] for r in page})
        ids.update(r['id'] for r in page)
    assert len(ids) == 2713
    assert api.get('/api/telemetry?limit=1001').status_code == 400
    assert api.get('/api/alerts?resolved=maybe').status_code == 400
    assert 'metadata only' in success(api.get('/api/settings'))['retention_notice']
    again = success(api.get('/api/snapshot'))
    assert again['history_window']['alerts']['total'] == windows['alerts']['total']


def test_stale_condition_resolves_then_rearms_without_duplicate_gets(api):
    crew = success(api.post('/api/crew', json={'name': 'Old Fictional', 'role': 'Simulation'}))
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat().replace('+00:00', 'Z')
    success(api.post('/api/telemetry', json={'crew_id': crew['id'], 'metric': 'heart_rate', 'value': 70,
                                             'unit': 'bpm', 'source': 'fictional', 'timestamp': old}))
    criteria = dict(entity_type='crew', entity_id=crew['id'], metric='heart_rate', rule='stale')
    first = matching(api, **criteria)[0]
    success(api.post(f"/api/alerts/{first['id']}/resolve", role='medical'))
    assert len(matching(api, **criteria)) == 2
    success(api.get('/api/snapshot'))
    assert len(matching(api, **criteria)) == 2


def test_equal_timestamp_latest_and_inclusive_history_bounds(app, api, crew_id):
    from backend.core import get_db, insert_record
    stamp = utc_now()
    with app.app_context():
        db = get_db()
        db.execute('BEGIN IMMEDIATE')
        for ident, timestamp_value, value in [('tie-old', stamp.replace('Z', '.000Z'), 66), ('tie-new', stamp, 77)]:
            insert_record('measurement', {'crew_id': crew_id, 'metric': 'heart_rate', 'value': value,
                'unit': 'bpm', 'source': 'ties', 'timestamp': timestamp_value, 'simulated': True}, ident)
        db.commit()
    snapshot = success(api.get('/api/snapshot'))
    crew = next(c for c in snapshot['crew'] if c['id'] == crew_id)
    assert crew['latest']['heart_rate']['id'] == 'tie-new'
    rows = [m for m in snapshot['measurements'] if m['id'] in ('tie-old', 'tie-new')]
    assert [m['id'] for m in rows] == ['tie-new', 'tie-old']
    history = success(api.get(f'/api/telemetry?crew_id={crew_id}&metric=heart_rate&since={stamp}&until={stamp}'))
    assert {m['id'] for m in history} == {'tie-old', 'tie-new'}


def test_simulation_batch_evaluates_complete_mission_total_once(app, api):
    from backend.app import simulate_once
    success(api.patch('/api/settings', json={'thresholds': {'radiation_cumulative':
        {'min': 0, 'max': 0.001, 'critical_min': 0, 'critical_max': 0.002, 'unit': 'mSv'}}}))
    before = len(matching(api, entity_type='radiation', entity_id='mission', severity='CRITICAL'))
    assert simulate_once(app) is True
    assert simulate_once(app) is False
    alerts = matching(api, entity_type='radiation', entity_id='mission', severity='CRITICAL')
    assert len(alerts) == before + 1
    newest = next(a for a in alerts if (a.get('condition_key') or '').startswith('threshold:batch:'))
    assert newest['value'] == success(api.get('/api/snapshot'))['mission']['radiation_total']
