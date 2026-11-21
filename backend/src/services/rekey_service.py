"""换钥演练：密钥环、写路径、分批改页、备库追赶、退役与切主门禁。

模型不接触真实存储引擎。旧页装载在本地表里，改页批次可中断、可重放；
到期后旧钥只保留给故障恢复读取，不再对外提供。
"""
import hashlib
import json
import math
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, select

from src.models.entities import (DataPage, FailoverRun, KeyRecord, RekeyRun,
                                 Replica, RewriteBatch, RewriteItem)
from src.services.election import elect
from src.utils.serialize import record

BASE_LOG_POSITION = 8000


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _sha(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def get_run(db, run_id):
    run = db.get(RekeyRun, run_id)
    if run is None:
        raise HTTPException(404, '换钥演练不存在')
    return run


def list_runs(db):
    return [record(r) for r in db.scalars(select(RekeyRun).order_by(RekeyRun.id.desc()))]


def create_run(db, payload):
    if payload.old_key_id == payload.new_key_id:
        raise HTTPException(400, '新旧密钥必须不同')
    old_key, new_key = db.get(KeyRecord, payload.old_key_id), db.get(KeyRecord, payload.new_key_id)
    if old_key is None or new_key is None:
        raise HTTPException(404, '密钥不存在')
    if old_key.status != 'active':
        raise HTTPException(409, '旧钥已退役或销毁，不能再次作为换钥起点')
    if new_key.status != 'active':
        raise HTTPException(409, '新钥必须处于可用状态')
    active = db.scalar(select(RekeyRun.id).where(RekeyRun.old_key_id == old_key.id,
                                                 RekeyRun.status == 'rewriting'))
    if active:
        raise HTTPException(409, '该旧钥已有一场进行中的换钥演练，请先推进或退役')
    run = RekeyRun(label=payload.label, old_key_id=old_key.id, new_key_id=new_key.id,
                   batch_size=payload.batch_size, tick=0,
                   retire_at_tick=payload.retire_at_tick, status='rewriting')
    db.add(run)
    db.flush()
    # 页面在换钥开始时按旧钥装载；随后所有新写入立即改用新钥。
    for no in range(1, payload.page_count + 1):
        db.add(DataPage(run_id=run.id, page_no=no, label=f'page-{no:03d}', value=f'v{no}',
                        key_id=old_key.id, version=1, rewrite_count=0, updated_at=now()))
    total = payload.page_count
    # east 故意落后 4 页且尚未拿到新钥，用来验证“追不平、只靠新钥读不全不能接班”。
    for name, role, applied, key_ready in [
            ('hs-primary', 'leader', total, True),
            ('hs-east', 'follower', max(total - 4, 0), False),
            ('hs-west', 'follower', total, True)]:
        db.add(Replica(run_id=run.id, name=name, role=role, term=6,
                       log_position=BASE_LOG_POSITION + applied,
                       available=True, fenced=False, vote_enabled=True,
                       new_key_ready=key_ready, old_key_available=True,
                       applied_version=applied))
    db.flush()
    return run


def _pages(db, run):
    return db.scalars(select(DataPage).where(DataPage.run_id == run.id)
                      .order_by(DataPage.page_no)).all()


def _replicas(db, run):
    return db.scalars(select(Replica).where(Replica.run_id == run.id)
                      .order_by(Replica.id)).all()


def _keys(db, run):
    return [k for k in (db.get(KeyRecord, run.old_key_id), db.get(KeyRecord, run.new_key_id)) if k]


def _old_key_page_count(db, run):
    return db.scalar(select(func.count()).select_from(DataPage).where(
        DataPage.run_id == run.id, DataPage.key_id != run.new_key_id)) or 0


def _page_total(db, run):
    return db.scalar(select(func.count()).select_from(DataPage).where(
        DataPage.run_id == run.id)) or 0


def _batch_payload(db, batch):
    items = db.scalars(select(RewriteItem).where(RewriteItem.batch_id == batch.id)
                       .order_by(RewriteItem.id)).all()
    no_by_id = {p.id: p.page_no for p in db.scalars(
        select(DataPage).where(DataPage.run_id == batch.run_id)).all()}
    return {'batch_no': batch.batch_no, 'status': batch.status,
            'items': [{'page_id': i.page_id, 'page': f'page-{no_by_id.get(i.page_id, 0):03d}',
                       'expected_version': i.expected_version,
                       'status': i.status} for i in items]}


def snapshot(db, run):
    total = _page_total(db, run)
    old_pages = _old_key_page_count(db, run)
    batches = [_batch_payload(db, b) for b in db.scalars(
        select(RewriteBatch).where(RewriteBatch.run_id == run.id)
        .order_by(RewriteBatch.batch_no)).all()]
    replicas = []
    for r in _replicas(db, run):
        item = record(r)
        item['readable_pages'] = min(r.applied_version, total)
        item['covers_all_with_new_key'] = bool(
            r.new_key_ready and old_pages == 0 and r.applied_version >= total)
        replicas.append(item)
    return {'run': record(run), 'keys': [record(k) for k in _keys(db, run)],
            'pages': [record(p) for p in _pages(db, run)],
            'replicas': replicas, 'batches': batches,
            'page_total': total, 'old_key_pages': old_pages,
            'progress': {'rewritten': total - old_pages, 'total': total},
            'retired': run.status == 'retired'}


def list_keys(db):
    return [record(k) for k in db.scalars(select(KeyRecord).order_by(KeyRecord.id))]


def create_key(db, payload):
    key = KeyRecord(label=payload.label, status='active',
                    retain_for_recovery=payload.retain_for_recovery)
    db.add(key)
    db.flush()
    return record(key)


def write_page(db, run, page_id, value):
    page = db.get(DataPage, page_id)
    if page is None or page.run_id != run.id:
        raise HTTPException(404, '页面不存在')
    # 换钥开始后所有新写入直接落到新钥；对应旧页无需再改。
    page.value = value
    page.key_id = run.new_key_id
    page.version += 1
    page.updated_at = now()
    db.flush()
    return record(page)


def _create_batch(db, run):
    candidates = db.scalars(select(DataPage).where(
        DataPage.run_id == run.id, DataPage.key_id != run.new_key_id
    ).order_by(DataPage.page_no).limit(run.batch_size)).all()
    if not candidates:
        return None
    batch_no = (db.scalar(select(func.max(RewriteBatch.batch_no)).where(
        RewriteBatch.run_id == run.id)) or 0) + 1
    batch = RewriteBatch(run_id=run.id, batch_no=batch_no, status='pending')
    db.add(batch)
    db.flush()
    for page in candidates:
        db.add(RewriteItem(batch_id=batch.id, page_id=page.id,
                           expected_version=page.version, status='pending'))
    db.flush()
    return batch


def _apply_pending(db, run, batch, limit=None):
    """幂等改页：按 expected_version 比对，已改过或已被写入抢先的条目不回写。"""
    items = db.scalars(select(RewriteItem).where(
        RewriteItem.batch_id == batch.id, RewriteItem.status == 'pending'
    ).order_by(RewriteItem.id)).all()
    if limit is not None:
        items = items[:limit]
    changed = 0
    for item in items:
        page = db.get(DataPage, item.page_id)
        if page is None:
            item.status = 'missing'
            continue
        if page.key_id == run.new_key_id:
            # 同批重放：该页已经改过，或已被新写入覆盖。两种情况都保留现有值。
            item.status = 'superseded' if page.version > item.expected_version else 'already'
            continue
        if page.version != item.expected_version:
            # 批次装载后页面被并发写入：新写的值和新钥优先，旧批次不得覆盖。
            item.status = 'superseded'
            continue
        page.key_id = run.new_key_id
        page.rewrite_count += 1
        item.status = 'done'
        changed += 1
    db.flush()
    return changed


def _pending_batch(db, run):
    return db.scalar(select(RewriteBatch).where(
        RewriteBatch.run_id == run.id,
        RewriteBatch.status.in_(['pending', 'interrupted'])
    ).order_by(RewriteBatch.batch_no))


def _apply_one_batch(db, run):
    batch = _pending_batch(db, run)
    if batch is None:
        batch = _create_batch(db, run)
    if batch is None:
        return None
    _apply_pending(db, run, batch)
    batch.status = 'applied'
    db.flush()
    return batch


def _catch_up_locked(db, run, step):
    total = _page_total(db, run)
    for replica in _replicas(db, run):
        if replica.available and replica.applied_version < total:
            before = replica.applied_version
            replica.applied_version = min(total, before + step)
            replica.log_position += replica.applied_version - before


def advance(db, run):
    if run.status == 'retired':
        raise HTTPException(409, '演练已退役，不能再推进改页')
    run.tick += 1
    completed = None
    if run.tick >= run.retire_at_tick:
        _retire(db, run)
    else:
        completed = _apply_one_batch(db, run)
        # 可达备库随每次推进复制一页；落后太多的备库需要单独触发追赶。
        _catch_up_locked(db, run, 1)
    db.flush()
    out = snapshot(db, run)
    out['advanced_batch'] = _batch_payload(db, completed) if completed else None
    return out


def retry_batch(db, run, batch_no):
    """重放同一批：已完成条目不再改页，仅继续未完成条目。"""
    batch = db.scalar(select(RewriteBatch).where(
        RewriteBatch.run_id == run.id, RewriteBatch.batch_no == batch_no))
    if batch is None:
        raise HTTPException(404, '改页批次不存在')
    if run.status == 'retired':
        raise HTTPException(409, '演练已退役，不能再重试改页')
    if batch.status == 'applied':
        return {'batch': _batch_payload(db, batch), 'already_applied': True, 'changed_pages': 0}
    changed = _apply_pending(db, run, batch)
    batch.status = 'applied'
    db.flush()
    return {'batch': _batch_payload(db, batch), 'already_applied': False,
            'changed_pages': changed}


def interrupt_batch(db, run):
    """模拟改页中断：本批只应用一部分条目，批次保持 interrupted，可继续重放。"""
    if run.status == 'retired':
        raise HTTPException(409, '演练已退役，不能再中断改页')
    batch = _pending_batch(db, run) or _create_batch(db, run)
    if batch is None:
        raise HTTPException(409, '没有待改写的旧页')
    if batch.status == 'pending':
        _apply_pending(db, run, batch, limit=max(1, math.ceil(run.batch_size / 2)))
        batch.status = 'interrupted'
    db.flush()
    return _batch_payload(db, batch)


def prepare_batch(db, run):
    """只装载下一批并记录各页期望版本，不改页；用于演示中断与并发写入。"""
    if run.status == 'retired':
        raise HTTPException(409, '演练已退役，不能再装载改页批次')
    batch = _pending_batch(db, run)
    if batch is None:
        batch = _create_batch(db, run)
    if batch is None:
        raise HTTPException(409, '没有待改写的旧页')
    return _batch_payload(db, batch)


def update_replica(db, run, replica_id, payload):
    replica = db.get(Replica, replica_id)
    if replica is None or replica.run_id != run.id:
        raise HTTPException(404, '备库不存在')
    for field in ('available', 'fenced', 'vote_enabled', 'new_key_ready'):
        value = getattr(payload, field, None)
        if value is not None:
            setattr(replica, field, value)
    db.flush()
    return record(replica)


def catchup(db, run, replica_id=None):
    targets = _replicas(db, run)
    if replica_id is not None:
        targets = [r for r in targets if r.id == replica_id]
        if not targets:
            raise HTTPException(404, '备库不存在')
    total = _page_total(db, run)
    for replica in targets:
        if replica.available:
            before = replica.applied_version
            replica.applied_version = total
            replica.log_position += total - before
            replica.old_key_available = run.status != 'retired'
            replica.new_key_ready = True
    db.flush()
    return snapshot(db, run)


def _retire(db, run):
    if run.status == 'retired':
        return
    run.status = 'retired'
    run.retired_at_tick = run.tick
    old_key = db.get(KeyRecord, run.old_key_id)
    # 保留旧钥仅用于故障恢复读旧页；不保留则直接销毁。
    old_key.status = 'retired' if old_key.retain_for_recovery else 'destroyed'
    for replica in _replicas(db, run):
        replica.old_key_available = False
    db.flush()


def retire_now(db, run):
    if run.status == 'retired':
        raise HTTPException(409, '演练已经退役')
    run.tick = max(run.tick, run.retire_at_tick)
    _retire(db, run)
    return snapshot(db, run)


def read_page(db, run, node_id, page_id, recovery=False):
    page = db.get(DataPage, page_id)
    if page is None or page.run_id != run.id:
        raise HTTPException(404, '页面不存在')
    key = db.get(KeyRecord, page.key_id)
    old_key = db.get(KeyRecord, run.old_key_id)
    base = {'page_id': page.id, 'page': page.label, 'value': page.value,
            'version': page.version, 'key': key.label if key else '未知密钥',
            'key_role': 'new' if page.key_id == run.new_key_id else 'old',
            'run_retired': run.status == 'retired', 'recovery': recovery}

    if recovery:
        # 故障恢复直接读归档，不经过备库；旧钥保留才可读旧页。
        if page.key_id == run.new_key_id:
            return {**base, 'allowed': True, 'reason': '新钥页面，按常规读取'}
        if old_key is None or old_key.status == 'destroyed' or not old_key.retain_for_recovery:
            return {**base, 'allowed': False, 'reason': '旧钥已销毁或未保留，无法恢复旧页'}
        return {**base, 'allowed': True, 'reason': '故障恢复：旧钥保留，可读旧页'}

    node = db.get(Replica, node_id)
    if node is None or node.run_id != run.id:
        raise HTTPException(404, '备库不存在')
    base['node'] = node.name
    if not node.available:
        return {**base, 'allowed': False, 'reason': '节点不可达'}
    if node.applied_version < page.page_no:
        return {**base, 'allowed': False,
                'reason': f'备库仅追到第 {node.applied_version} 页，尚未复制该页'}
    if page.key_id == run.new_key_id:
        if not node.new_key_ready:
            return {**base, 'allowed': False, 'reason': '备库尚未获得新钥，无法解密'}
        return {**base, 'allowed': True, 'reason': '备库使用新钥读取'}
    if run.status == 'retired':
        return {**base, 'allowed': False,
                'reason': '换钥已到期，旧页不能再用旧钥对外读；故障恢复请走保留旧钥通道'}
    if not node.old_key_available:
        return {**base, 'allowed': False, 'reason': '备库旧钥不可用'}
    return {**base, 'allowed': True, 'reason': '退役前旧页仍由保留旧钥提供读取'}


def _coverage(db, run):
    total = _page_total(db, run)
    old_pages = _old_key_page_count(db, run)
    out = []
    for r in _replicas(db, run):
        out.append({'name': r.name, 'new_key_ready': r.new_key_ready,
                    'applied_version': r.applied_version, 'page_total': total,
                    'old_key_pages': old_pages,
                    'eligible': bool(r.new_key_ready and r.applied_version >= total
                                     and old_pages == 0)})
    return out


def failover(db, run, payload):
    replicas = _replicas(db, run)
    if not replicas:
        raise HTTPException(400, '没有可参与选主的备库')
    coverage = _coverage(db, run)
    # 只靠新钥读不全的备库保留投票权，但被排除在候选之外；其余规则不变。
    unable = [c['name'] for c in coverage if not c['eligible']]
    nodes = [{'id': r.id, 'name': r.name, 'role': r.role, 'term': r.term,
              'log_position': r.log_position, 'available': r.available,
              'fenced': r.fenced, 'vote_enabled': r.vote_enabled} for r in replicas]
    scenario = {'fail_primary': payload.fail_primary, 'fence_primary': payload.fence_primary,
                'max_data_loss': payload.max_data_loss, 'required_term': payload.required_term}
    old_key = db.get(KeyRecord, run.old_key_id)
    new_key = db.get(KeyRecord, run.new_key_id)
    snapshot_input = {'nodes': nodes, 'scenario': scenario,
                      'rekey': {'old_key': old_key.label, 'new_key': new_key.label,
                                'retired': run.status == 'retired',
                                'old_key_pages': _old_key_page_count(db, run),
                                'disqualified': unable}}
    try:
        result = elect(nodes, scenario, disqualified=unable)
    except ValueError as exc:
        result = {'decision': 'blocked', 'winner': None, 'error': str(exc), 'checks': [],
                  'candidates': [], 'model_version': 'quorum-fencing-rpo-v1',
                  'input_sha256': _sha(snapshot_input), 'simulation': True}

    # 换钥新增门禁：赢家必须能只靠新钥读全所有页面。
    winner_coverage = next((c for c in coverage if c['name'] == result.get('winner')), None)
    winner_ok = bool(winner_coverage and winner_coverage['eligible'])
    if result.get('decision') == 'approved' and not winner_ok:
        result['checks'].append({
            'rule': '新钥全覆盖', 'pass': False,
            'evidence': f"{result.get('winner')} 无法只靠新钥读全数据"})
        result['decision'] = 'blocked'
        result['winner'] = None
    elif result.get('decision') == 'approved':
        result['checks'].append({
            'rule': '新钥全覆盖', 'pass': True,
            'evidence': f"{result['winner']} 已用新钥覆盖全部 {winner_coverage['page_total']} 页"})
    else:
        result['checks'].append({
            'rule': '新钥全覆盖', 'pass': False,
            'evidence': f"{len(unable)} 个备库无法只靠新钥读全数据：" + '、'.join(unable) if unable
                        else '新钥覆盖条件未导致阻断'})
    result['new_key_coverage'] = coverage

    failover = FailoverRun(run_id=run.id, snapshot=snapshot_input, result=result)
    db.add(failover)
    if result.get('decision') == 'approved':
        for replica in replicas:
            replica.role = 'leader' if replica.name == result['winner'] else 'follower'
            replica.term = result['new_term']
    db.flush()
    return {'failover_id': failover.id, 'result': result, 'replicas': [record(r) for r in replicas]}


def failovers(db, run):
    return [record(f) for f in db.scalars(select(FailoverRun).where(
        FailoverRun.run_id == run.id).order_by(FailoverRun.id.desc()))]
