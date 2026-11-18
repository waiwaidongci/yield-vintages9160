import csv,io,time,uuid
from fastapi import HTTPException
from src.config.settings import CONFIG
from src.models.inspection import Inspection
from src.models.source import Period
from src.services.audit import log
from src.services.validation import validate_inspection

COLUMNS=('batch_no','period','department','shift','input')


def save_inspection(db,user,data):
 validate_inspection(data)
 obj=Inspection(natural_key='inspection-'+uuid.uuid4().hex,data=data,created=time.time())
 db.add(obj)
 if not db.query(Period).filter_by(name=data['period']).first():
  db.add(Period(name=data['period'],description='业务抽检自动登记期间'))
 log(db,user.email,'inspection.create',data['batch_no'])
 db.commit();return obj


def import_csv(db,user,period,text):
 """按所选期间导入抽检单 CSV。日期列缺失或写错时以所选期间为准。"""
 period=period.strip()
 if not period: raise HTTPException(422,'请先选择导入期间')
 stream=io.StringIO(text.lstrip('﻿'))
 reader=csv.DictReader(stream)
 if not reader.fieldnames: raise HTTPException(422,'CSV 缺少表头')
 aliases={name:name for name in COLUMNS}
 labels={label:name for name,label in CONFIG['inspection_fields'].items()}
 header={}
 for column in reader.fieldnames:
  key=(column or '').strip()
  if key in aliases: header[column]=aliases[key]
  elif key in labels: header[column]=labels[key]
  else: raise HTTPException(422,'无法识别的列：'+key)
 # 期间以导入时选择为准，CSV 可带日期列但会被覆盖。
 missing=(set(COLUMNS)-{'period'})-set(header.values())
 if missing: raise HTTPException(422,'CSV 缺少列：'+'、'.join(CONFIG['inspection_fields'][m] for m in sorted(missing)))
 created=0
 errors=[]
 for line,raw in enumerate(reader,start=2):
  record={header[k]:(v or '').strip() for k,v in raw.items() if k in header}
  record['period']=period
  try:
   if record['input']=='': raise ValueError('投入数不能为空')
   record['input']=int(float(record['input']))
   validate_inspection(record)
  except ValueError as exc:
   errors.append('第%d行：%s'%(line,exc));continue
  db.add(Inspection(natural_key='inspection-'+uuid.uuid4().hex,data=record,created=time.time()))
  created+=1
 if errors:
  db.rollback();raise HTTPException(422,'；'.join(errors[:6]))
 if not created: raise HTTPException(422,'CSV 没有数据行')
 if not db.query(Period).filter_by(name=period).first():
  db.add(Period(name=period,description='业务抽检自动登记期间'))
 log(db,user.email,'inspection.import','%s 导入 %d 条'%(period,created))
 db.commit();return {'period':period,'created':created}
