import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DB_FILE = Path(tempfile.gettempdir()) / ('yield-vintages-test-%d.db' % os.getpid())
os.environ.setdefault('DATABASE_URL', 'sqlite:///' + str(DB_FILE))
os.environ.setdefault('REDIS_URL', 'redis://localhost:6379/15')
os.environ.setdefault('JWT_SECRET', 'test-secret')
os.environ.setdefault('PROJECT_FILE', str(ROOT / 'project.json'))

import pytest
from src.config.database import Base, Session, engine
from src.models import identity, inspection, run, source  # noqa: F401
from src.services import queue, reconciliation


@pytest.fixture()
def db(monkeypatch):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(queue, 'enqueue', lambda *args, **kwargs: None)
    monkeypatch.setattr(reconciliation, 'enqueue', lambda *args, **kwargs: None)
    with Session() as session:
        yield session
