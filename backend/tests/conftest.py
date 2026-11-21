import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('DATABASE_URL', 'sqlite://')
os.environ.setdefault('JWT_SECRET', 'test-secret')
os.environ.setdefault('REDIS_URL', 'redis://127.0.0.1:6379/15')

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.config.database import Base
from src.models.entities import KeyRecord, User
from src.utils.security import hash_password


@pytest.fixture()
def db():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as session:
        session.add_all([User(email=f'{r}@hengshi.local', role=r,
                              password_hash=hash_password('Drill2026!'))
                         for r in ('admin', 'operator', 'viewer')])
        session.add_all([KeyRecord(label='k-old', status='active'),
                         KeyRecord(label='k-new', status='active')])
        session.commit()
        yield session
