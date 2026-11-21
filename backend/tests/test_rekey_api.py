import os

os.environ.setdefault('DATABASE_URL', 'sqlite://')
os.environ.setdefault('JWT_SECRET', 'test-secret')
os.environ.setdefault('REDIS_URL', 'redis://127.0.0.1:6379/15')

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.config.database import Base, get_db
from src.main import app
from src.models.entities import KeyRecord, User
from src.utils.security import hash_password


@pytest.fixture()
def client():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as db:
        db.add_all([User(email=f'{r}@hengshi.local', role=r,
                         password_hash=hash_password('Drill2026!'))
                    for r in ('admin', 'operator', 'viewer')])
        db.add_all([KeyRecord(label='k-old', status='active'),
                    KeyRecord(label='k-new', status='active')])
        db.commit()

    def override():
        with Session() as session:
            yield session

    app.dependency_overrides[get_db] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def login(client, role='operator'):
    r = client.post('/auth/login', json={'email': f'{role}@hengshi.local',
                                         'password': 'Drill2026!'})
    assert r.status_code == 200, r.text
    return {'Authorization': 'Bearer ' + r.json()['access_token']}


def start(client, headers, **over):
    keys = {k['label']: k['id'] for k in client.get('/rekey/keys', headers=headers).json()}
    payload = {'label': 'api-run', 'old_key_id': keys['k-old'], 'new_key_id': keys['k-new'],
               'page_count': 8, 'batch_size': 4, 'retire_at_tick': 6}
    payload.update(over)
    r = client.post('/rekey/runs', json=payload, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def test_viewer_cannot_start_or_write_but_can_read(client):
    viewer = login(client, 'viewer')
    operator = login(client, 'operator')
    snap = start(client, operator)
    run_id, page_id, node_id = snap['run']['id'], snap['pages'][0]['id'], snap['replicas'][0]['id']
    assert client.post('/rekey/runs', json={'label': 'x', 'old_key_id': 1, 'new_key_id': 2},
                       headers=viewer).status_code == 403
    assert client.post(f'/rekey/runs/{run_id}/pages/{page_id}/write', json={'value': 'v'},
                       headers=viewer).status_code == 403
    assert client.get(f'/rekey/runs/{run_id}', headers=viewer).status_code == 200
    assert client.get(f'/rekey/runs/{run_id}/read', params={'node_id': node_id, 'page_id': page_id},
                      headers=viewer).status_code == 200


def test_new_write_visible_immediately_and_report_blocked_read(client):
    h = login(client)
    snap = start(client, h)
    run_id = snap['run']['id']
    page = snap['pages'][-1]
    east = next(r for r in snap['replicas'] if r['name'] == 'hs-east')
    # east 落后且无新钥：读新写入页受阻，并给出原因
    blocked = client.get(f'/rekey/runs/{run_id}/read',
                         params={'node_id': east['id'], 'page_id': page['id']}, headers=h).json()
    assert blocked['allowed'] is False and blocked['reason']
    w = client.post(f'/rekey/runs/{run_id}/pages/{page["id"]}/write',
                    json={'value': 'brand-new'}, headers=h)
    assert w.status_code == 200
    assert w.json()['key_id'] == snap['run']['new_key_id']
    # 主库已持有新钥，立即可读新值
    primary = next(r for r in snap['replicas'] if r['name'] == 'hs-primary')
    got = client.get(f'/rekey/runs/{run_id}/read',
                     params={'node_id': primary['id'], 'page_id': page['id']}, headers=h).json()
    assert got['allowed'] is True and got['value'] == 'brand-new' and got['key_role'] == 'new'


def test_interrupt_retry_and_retirement_flow(client):
    admin = login(client, 'admin')
    snap = start(client, admin)
    run_id = snap['run']['id']
    batch = client.post(f'/rekey/runs/{run_id}/batch/interrupt', headers=admin).json()
    assert batch['status'] == 'interrupted'
    done = [i for i in batch['items'] if i['status'] == 'done']
    assert done
    first = client.post(f"/rekey/runs/{run_id}/batches/{batch['batch_no']}/retry", headers=admin).json()
    assert first['already_applied'] is False
    again = client.post(f"/rekey/runs/{run_id}/batches/{batch['batch_no']}/retry", headers=admin).json()
    assert again['already_applied'] is True and again['changed_pages'] == 0

    detail = client.get(f'/rekey/runs/{run_id}', headers=admin).json()
    old_page_id = next(p['id'] for p in detail['pages'] if p['key_id'] != detail['run']['new_key_id'])
    retired = client.post(f'/rekey/runs/{run_id}/retire', headers=admin)
    assert retired.status_code == 200 and retired.json()['retired'] is True
    west = next(r for r in detail['replicas'] if r['name'] == 'hs-west')
    blocked = client.get(f'/rekey/runs/{run_id}/read',
                         params={'node_id': west['id'], 'page_id': old_page_id}, headers=admin).json()
    assert blocked['allowed'] is False
    recovery = client.get(f'/rekey/runs/{run_id}/read',
                          params={'page_id': old_page_id, 'recovery': True}, headers=admin).json()
    assert recovery['allowed'] is True
    assert client.post(f'/rekey/runs/{run_id}/advance', headers=admin).status_code == 409


def test_failover_is_gated_until_full_new_key_coverage(client):
    h = login(client)
    snap = start(client, h)
    run_id = snap['run']['id']
    gate = {'fail_primary': True, 'fence_primary': True, 'max_data_loss': 10000,
            'required_term': 7}
    early = client.post(f'/rekey/runs/{run_id}/failover', json=gate, headers=h).json()
    assert early['result']['decision'] == 'blocked'
    assert any(c['rule'] == '新钥全覆盖' and not c['pass'] for c in early['result']['checks'])

    for _ in range(2):
        client.post(f'/rekey/runs/{run_id}/advance', headers=h)
    snap = client.get(f'/rekey/runs/{run_id}', headers=h).json()
    assert snap['old_key_pages'] == 0
    client.post(f'/rekey/runs/{run_id}/catchup', headers=h)
    out = client.post(f'/rekey/runs/{run_id}/failover', json=gate, headers=h).json()
    assert out['result']['decision'] == 'approved'
    assert out['result']['winner']
    assert any(c['rule'] == '新钥全覆盖' and c['pass'] for c in out['result']['checks'])


def test_admin_only_retire_and_export(client):
    operator = login(client, 'operator')
    admin = login(client, 'admin')
    snap = start(client, operator)
    run_id = snap['run']['id']
    assert client.post(f'/rekey/runs/{run_id}/retire', headers=operator).status_code == 403
    assert client.post(f'/rekey/runs/{run_id}/retire', headers=admin).status_code == 200
    export = client.get(f'/rekey/runs/{run_id}/export', headers=operator)
    assert export.status_code == 200
    assert export.headers['content-disposition'].startswith('attachment')
    body = export.json()
    assert body['run']['retired'] is True


def test_cannot_start_two_active_runs_on_same_old_key(client):
    h = login(client)
    start(client, h, label='first')
    keys = {k['label']: k['id'] for k in client.get('/rekey/keys', headers=h).json()}
    r = client.post('/rekey/runs', headers=h, json={
        'label': 'second', 'old_key_id': keys['k-old'], 'new_key_id': keys['k-new'],
        'page_count': 8, 'batch_size': 4, 'retire_at_tick': 6})
    assert r.status_code == 409


def test_only_admin_registers_keys(client):
    operator = login(client, 'operator')
    admin = login(client, 'admin')
    body = {'label': 'k-third', 'retain_for_recovery': True}
    assert client.post('/rekey/keys', json=body, headers=operator).status_code == 403
    assert client.post('/rekey/keys', json=body, headers=admin).status_code == 201
