import pytest
from sqlalchemy import select

from src.models.entities import DataPage, Replica
from src.schemas.rekey import FailoverInput, RekeyRunInput
from src.services import rekey_service as svc


def keys(db):
    old = db.scalar(select(svc.KeyRecord).where(svc.KeyRecord.label == 'k-old'))
    new = db.scalar(select(svc.KeyRecord).where(svc.KeyRecord.label == 'k-new'))
    return old, new


def make_run(db, page_count=12, batch_size=4, retire_at_tick=6):
    old, new = keys(db)
    return svc.create_run(db, RekeyRunInput(label='test-run', old_key_id=old.id, new_key_id=new.id,
                                            page_count=page_count, batch_size=batch_size,
                                            retire_at_tick=retire_at_tick))


def test_new_writes_use_new_key_immediately(db):
    run = make_run(db)
    page = db.scalars(select(DataPage).where(DataPage.run_id == run.id)
                      .order_by(DataPage.page_no)).first()
    out = svc.write_page(db, run, page.id, 'fresh')
    assert out['key_id'] == run.new_key_id
    assert out['value'] == 'fresh'
    assert out['version'] == 2


def test_batch_rewrite_resumes_after_interrupt_and_retry_is_idempotent(db):
    run = make_run(db)
    batch = svc.interrupt_batch(db, run)
    assert batch['status'] == 'interrupted'
    done = [i for i in batch['items'] if i['status'] == 'done']
    assert 0 < len(done) < len(batch['items'])
    first = svc.retry_batch(db, run, batch['batch_no'])
    assert first['already_applied'] is False
    assert first['changed_pages'] == len(batch['items']) - len(done)
    second = svc.retry_batch(db, run, batch['batch_no'])
    assert second['already_applied'] is True
    assert second['changed_pages'] == 0


def test_concurrent_new_write_wins_over_stale_batch(db):
    run = make_run(db, page_count=4, batch_size=4, retire_at_tick=9)
    prepared = svc.prepare_batch(db, run)
    target = prepared['items'][0]['page_id']
    svc.write_page(db, run, target, 'newer')
    out = svc.retry_batch(db, run, prepared['batch_no'])
    page = db.get(DataPage, target)
    assert page.value == 'newer'
    assert page.version == 2
    assert page.rewrite_count == 0
    item = next(i for i in out['batch']['items'] if i['page_id'] == target)
    assert item['status'] == 'superseded'


def test_retirement_blocks_old_key_reads_but_keeps_recovery_reads(db):
    run = make_run(db, page_count=8, batch_size=2, retire_at_tick=2)
    page = db.scalars(select(DataPage).where(DataPage.run_id == run.id)
                      .order_by(DataPage.page_no)).first()
    read_before = svc.read_page(db, run, _west(db, run).id, page.id)
    assert read_before['allowed'] is True and read_before['key_role'] == 'old'
    retired = svc.retire_now(db, run)
    assert retired['retired'] is True
    blocked = svc.read_page(db, run, _west(db, run).id, page.id)
    assert blocked['allowed'] is False
    assert '旧页' in blocked['reason'] or '旧钥' in blocked['reason']
    recovery = svc.read_page(db, run, None, page.id, recovery=True)
    assert recovery['allowed'] is True
    assert '恢复' in recovery['reason']


def test_old_key_not_retained_blocks_recovery(db):
    old, new = keys(db)
    old.retain_for_recovery = False
    db.flush()
    run = make_run(db, page_count=4, batch_size=2, retire_at_tick=2)
    svc.retire_now(db, run)
    page = db.scalars(select(DataPage).where(DataPage.run_id == run.id)
                      .order_by(DataPage.page_no)).first()
    out = svc.read_page(db, run, None, page.id, recovery=True)
    assert out['allowed'] is False


def test_failover_requires_new_key_coverage(db):
    run = make_run(db, page_count=8, batch_size=2, retire_at_tick=9)
    # 只改一部分页，east 也未拿到新钥
    svc.advance(db, run)
    gate = FailoverInput(fail_primary=True, fence_primary=True, max_data_loss=10000,
                         required_term=7)
    out = svc.failover(db, run, gate)
    assert out['result']['decision'] == 'blocked'
    checks = {c['rule']: c for c in out['result']['checks']}
    assert '新钥全覆盖' in checks
    assert checks['新钥全覆盖']['pass'] is False


def test_failover_allowed_when_fully_rewritten_and_caught_up(db):
    run = make_run(db, page_count=8, batch_size=4, retire_at_tick=9)
    svc.advance(db, run)
    svc.advance(db, run)
    assert svc.snapshot(db, run)['old_key_pages'] == 0
    svc.catchup(db, run, None)
    gate = FailoverInput(fail_primary=True, fence_primary=True, max_data_loss=10000,
                         required_term=7)
    out = svc.failover(db, run, gate)
    assert out['result']['decision'] == 'approved'
    assert out['result']['winner'] in ('hs-east', 'hs-west', 'hs-primary')
    checks = {c['rule']: c for c in out['result']['checks']}
    assert checks['新钥全覆盖']['pass'] is True


def test_west_can_lead_when_only_west_is_caught_up_and_east_lags(db):
    run = make_run(db, page_count=12, batch_size=4, retire_at_tick=9)
    svc.advance(db, run)
    svc.advance(db, run)
    svc.advance(db, run)
    replicas = db.scalars(select(Replica).where(Replica.run_id == run.id)).all()
    east = next(r for r in replicas if r.name == 'hs-east')
    west = next(r for r in replicas if r.name == 'hs-west')
    assert east.applied_version < svc.snapshot(db, run)['page_total']
    assert west.applied_version == svc.snapshot(db, run)['page_total']
    east.new_key_ready = False
    db.flush()
    gate = FailoverInput(fail_primary=True, fence_primary=True, max_data_loss=0,
                         required_term=7)
    out = svc.failover(db, run, gate)
    assert out['result']['decision'] == 'approved'
    assert out['result']['winner'] == 'hs-west'


def test_retired_run_rejects_further_rewrites(db):
    run = make_run(db, retire_at_tick=2)
    svc.retire_now(db, run)
    with pytest.raises(Exception):
        svc.advance(db, run)
    with pytest.raises(Exception):
        svc.interrupt_batch(db, run)


def _west(db, run):
    return db.scalar(select(Replica).where(Replica.run_id == run.id,
                                           Replica.name == 'hs-west'))


def test_undercovered_replica_is_disqualified_not_blocking_a_covered_peer(db):
    run = make_run(db, page_count=8, batch_size=4, retire_at_tick=9)
    svc.advance(db, run)
    svc.advance(db, run)
    assert svc.snapshot(db, run)['old_key_pages'] == 0
    # east 仍未追平且无新钥，west 已全覆盖：west 应接班，east 被排除
    gate = FailoverInput(fail_primary=True, fence_primary=True, max_data_loss=10000,
                         required_term=7)
    out = svc.failover(db, run, gate)
    assert out['result']['decision'] == 'approved'
    assert out['result']['winner'] == 'hs-west'
    east = next(c for c in out['result']['candidates'] if c['name'] == 'hs-east')
    assert east['disqualified'] is True and east['eligible'] is False
    assert any(c['rule'] == '新钥全覆盖' and c['pass'] for c in out['result']['checks'])


def test_no_covered_replica_blocks_failover(db):
    run = make_run(db, page_count=8, batch_size=2, retire_at_tick=9)
    svc.advance(db, run)
    gate = FailoverInput(fail_primary=True, fence_primary=True, max_data_loss=10000,
                         required_term=7)
    out = svc.failover(db, run, gate)
    assert out['result']['decision'] == 'blocked'
    assert out['result']['winner'] is None
