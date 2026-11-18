import json,random
from src.config.database import Base,engine,Session
from src.config.settings import CONFIG
from src.models.identity import User
from src.models.source import Source,Policy,Period
from src.models.inspection import Inspection
from src.models import run
from src.utils.security import password_hash
from src.services.validation import validate_source,validate_inspection

def demo_rows():
 rng=random.Random(42);rows=[]
 for i in range(240):
  n=rng.randint(100,1200);fp=int(n*rng.uniform(.7,.96));rw=rng.randint(0,n-fp);rows.append({'period':'2026-11-'+str(1+i%20).zfill(2),'department':['试制线A','试制线B','试制线C'][i%3],'shift':['早班','晚班'][i%2],'input':n,'first_pass':fp,'reworked_good':rw,'scrap':n-fp-rw})
 return rows

def demo_inspections(rows):
 """为前两个期间生成抽检单：批号刻意不同于底稿，投入数多数与同线同班底稿相近，
 个别差 8 件造成候选交叉、个别差 20 件无法配对，用于演示确定性配对与未定原因。"""
 rng=random.Random(2026);out=[]
 periods=sorted(set(r['period'] for r in rows))[:2]
 for period in periods:
  same=[r for r in rows if r['period']==period]
  for index,row in enumerate(same):
   delta=rng.choice([0,0,0,1,-1,2,-2,8,-8,20])
   data={'batch_no':'抽检-%s-%02d'%(period[-2:],index+1),'period':period,'department':row['department'],
         'shift':row['shift'],'input':max(1,row['input']+delta)}
   validate_inspection(data);out.append(data)
 return out

def seed():
 Base.metadata.create_all(engine)
 with Session() as db:
  for role in ('admin','analyst','viewer'):
   if not db.query(User).filter_by(email=role+'@demo.local').first():db.add(User(email=role+'@demo.local',role=role,password=password_hash('Demo2026!')))
  rows=demo_rows()
  for name in sorted(set(r['period'] for r in rows)):
   if not db.query(Period).filter_by(name=name).first():db.add(Period(name=name,description='合成演示业务期间，seed=42'))
  for i,data in enumerate(rows):
   validate_source(data);key='demo-'+str(i+1)
   if not db.query(Source).filter_by(natural_key=key).first():db.add(Source(natural_key=key,data=data))
  for i,data in enumerate(demo_inspections(rows)):
   key='inspection-demo-'+str(i+1)
   if not db.query(Inspection).filter_by(natural_key=key).first():db.add(Inspection(natural_key=key,data=data,created=0.0))
  if not db.query(Policy).filter_by(version=CONFIG['policy']['version']).first():db.add(Policy(version=CONFIG['policy']['version'],data=CONFIG['policy']))
  db.commit();print(json.dumps({'users':db.query(User).count(),'sources':db.query(Source).count(),'inspections':db.query(Inspection).count(),'policies':db.query(Policy).count(),'periods':db.query(Period).count()},ensure_ascii=False))
if __name__=='__main__':seed()
