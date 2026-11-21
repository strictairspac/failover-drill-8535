from fastapi import APIRouter, Depends, Response
from src.config.database import get_db
from src.middlewares.auth import admin, current_user, writable
from src.schemas.rekey import (FailoverInput, KeyInput, PageWriteInput,
                               ReplicaInput, RekeyRunInput)
from src.services import rekey_service as svc
from src.controllers.actions import audit
import json

router = APIRouter()


@router.get('/rekey/keys')
def keys(db=Depends(get_db), user=Depends(current_user)):
    return svc.list_keys(db)


@router.post('/rekey/keys', status_code=201)
def create_key(data: KeyInput, db=Depends(get_db), user=Depends(admin)):
    out = svc.create_key(db, data)
    audit(db, user, f"rekey:key:{out['label']}")
    db.commit()
    return out


@router.get('/rekey/runs')
def runs(db=Depends(get_db), user=Depends(current_user)):
    return svc.list_runs(db)


@router.post('/rekey/runs', status_code=201)
def create_run(data: RekeyRunInput, db=Depends(get_db), user=Depends(writable)):
    run = svc.create_run(db, data)
    audit(db, user, f'rekey:start:{run.id}')
    db.commit()
    return svc.snapshot(db, run)


@router.get('/rekey/runs/{run_id}')
def detail(run_id: int, db=Depends(get_db), user=Depends(current_user)):
    return svc.snapshot(db, svc.get_run(db, run_id))


@router.post('/rekey/runs/{run_id}/pages/{page_id}/write')
def write(run_id: int, page_id: int, data: PageWriteInput, db=Depends(get_db),
          user=Depends(writable)):
    run = svc.get_run(db, run_id)
    out = svc.write_page(db, run, page_id, data.value)
    audit(db, user, f'rekey:write:{run_id}:{page_id}')
    db.commit()
    return out


@router.post('/rekey/runs/{run_id}/advance')
def advance(run_id: int, db=Depends(get_db), user=Depends(writable)):
    run = svc.get_run(db, run_id)
    out = svc.advance(db, run)
    audit(db, user, f'rekey:advance:{run_id}:{run.tick}')
    db.commit()
    return out


@router.post('/rekey/runs/{run_id}/batch/prepare')
def prepare_batch(run_id: int, db=Depends(get_db), user=Depends(writable)):
    out = svc.prepare_batch(db, svc.get_run(db, run_id))
    db.commit()
    return out


@router.post('/rekey/runs/{run_id}/batch/interrupt')
def interrupt(run_id: int, db=Depends(get_db), user=Depends(writable)):
    run = svc.get_run(db, run_id)
    out = svc.interrupt_batch(db, run)
    audit(db, user, f'rekey:interrupt:{run_id}:{out["batch_no"]}')
    db.commit()
    return out


@router.post('/rekey/runs/{run_id}/batches/{batch_no}/retry')
def retry(run_id: int, batch_no: int, db=Depends(get_db), user=Depends(writable)):
    run = svc.get_run(db, run_id)
    out = svc.retry_batch(db, run, batch_no)
    audit(db, user, f'rekey:retry:{run_id}:{batch_no}')
    db.commit()
    return out


@router.post('/rekey/runs/{run_id}/catchup')
def catchup(run_id: int, replica_id: int | None = None, db=Depends(get_db),
            user=Depends(writable)):
    run = svc.get_run(db, run_id)
    out = svc.catchup(db, run, replica_id)
    audit(db, user, f'rekey:catchup:{run_id}')
    db.commit()
    return out


@router.post('/rekey/runs/{run_id}/replicas/{replica_id}')
def update_replica(run_id: int, replica_id: int, data: ReplicaInput,
                   db=Depends(get_db), user=Depends(admin)):
    out = svc.update_replica(db, svc.get_run(db, run_id), replica_id, data)
    audit(db, user, f'rekey:replica:{run_id}:{replica_id}')
    db.commit()
    return out


@router.post('/rekey/runs/{run_id}/retire')
def retire(run_id: int, db=Depends(get_db), user=Depends(admin)):
    out = svc.retire_now(db, svc.get_run(db, run_id))
    audit(db, user, f'rekey:retire:{run_id}')
    db.commit()
    return out


@router.get('/rekey/runs/{run_id}/read')
def read(run_id: int, node_id: int | None = None, page_id: int = 0,
         recovery: bool = False, db=Depends(get_db), user=Depends(current_user)):
    return svc.read_page(db, svc.get_run(db, run_id), node_id, page_id, recovery)


@router.post('/rekey/runs/{run_id}/failover')
def failover(run_id: int, data: FailoverInput, db=Depends(get_db), user=Depends(writable)):
    run = svc.get_run(db, run_id)
    out = svc.failover(db, run, data)
    audit(db, user, f'rekey:failover:{run_id}')
    db.commit()
    return out


@router.get('/rekey/runs/{run_id}/failovers')
def failover_history(run_id: int, db=Depends(get_db), user=Depends(current_user)):
    return svc.failovers(db, svc.get_run(db, run_id))


@router.get('/rekey/runs/{run_id}/export')
def export(run_id: int, db=Depends(get_db), user=Depends(current_user)):
    run = svc.get_run(db, run_id)
    out = {'run': svc.snapshot(db, run), 'failovers': svc.failovers(db, run)}
    return Response(json.dumps(out, ensure_ascii=False, indent=2, default=str),
                    media_type='application/json',
                    headers={'Content-Disposition': f'attachment; filename="rekey-{run.id}.json"'})
