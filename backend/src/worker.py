import time,json,traceback
from pathlib import Path
import redis
from src.config.database import Base,engine,Session
from src.config.settings import REDIS_URL
from src.models import identity,source,run,inspection
from src.models.run import Run
from src.services.calculation import calculate
from src.services.delivery import tick
from src.services.queue import RECONCILE_QUEUE
from src.services.reconciliation import process as reconcile,recover_stale
Base.metadata.create_all(engine)
r=redis.Redis.from_url(REDIS_URL)
last_recovery=0.0
while True:
 Path('/tmp/worker-heartbeat').write_text(str(time.time()))
 try:
  item=r.lpop('report-jobs')
  if item:
   with Session() as db:
    job=db.get(Run,int(item))
    if job and job.status=='queued':
     job.status='running';db.commit()
     try: job.result=calculate(job.snapshot['sources'],job.snapshot['policy']);job.status='completed'
     except Exception as exc: job.status='failed';job.error=str(exc);traceback.print_exc()
     db.commit()
  reconcile_item=r.lpop(RECONCILE_QUEUE)
  if reconcile_item:
   with Session() as db: reconcile(db,int(reconcile_item))
  if time.time()-last_recovery>10:
   with Session() as db: recover_stale(db)
   last_recovery=time.time()
  with Session() as db: tick(db)
 except Exception: traceback.print_exc()
 time.sleep(1)
