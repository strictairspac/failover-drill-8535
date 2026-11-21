import os
from sqlalchemy import select
from src.config.database import Base, engine, Session
from src.models.entities import KeyRecord, RekeyRun, User, Node, Scenario
from src.utils.security import hash_password

def seed():
    Base.metadata.create_all(engine)
    if os.getenv('SEED_DEMO','true').lower() != 'true': return
    with Session() as db:
        for role in ('admin','operator','viewer'):
            email = role + '@hengshi.local'
            if not db.scalar(select(User).where(User.email == email)):
                db.add(User(email=email, role=role, password_hash=hash_password('Drill2026!')))
        for name, role, pos in [('hs-primary','leader',8100),('hs-east','follower',8100),('hs-west','follower',8098)]:
            if not db.scalar(select(Node).where(Node.name == name)):
                db.add(Node(name=name, role=role, term=6, log_position=pos, available=True, fenced=False, vote_enabled=True))
        for name, fence in [('晚班隔离演练',True),('缺少隔离证明',False)]:
            if not db.scalar(select(Scenario).where(Scenario.name == name)):
                db.add(Scenario(name=name, fail_primary=True, fence_primary=fence, max_data_loss=0, required_term=7))
        for label, retain in [('k-2026-11', True), ('k-2027-05', True)]:
            if not db.scalar(select(KeyRecord).where(KeyRecord.label == label)):
                db.add(KeyRecord(label=label, status='active', retain_for_recovery=retain))
        db.commit()
        # 演示脚本：自然键查重，不覆盖用户已推进的演练。
        if db.scalar(select(RekeyRun).where(RekeyRun.label == '晚班换钥演练')) is None:
            old = db.scalar(select(KeyRecord).where(KeyRecord.label == 'k-2026-11'))
            new = db.scalar(select(KeyRecord).where(KeyRecord.label == 'k-2027-05'))
            if old and new:
                from src.schemas.rekey import RekeyRunInput
                from src.services.rekey_service import create_run
                create_run(db, RekeyRunInput(label='晚班换钥演练', old_key_id=old.id,
                                             new_key_id=new.id, page_count=12,
                                             batch_size=4, retire_at_tick=6))
                db.commit()
if __name__ == '__main__': seed()
