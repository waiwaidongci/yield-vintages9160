from sqlalchemy import Column,Integer,String,JSON,Text,Float,UniqueConstraint
from src.config.database import Base

class Inspection(Base):
    """按期间导入的抽检单。批号只作为单据标识，不参与与试制底稿的配对。"""
    __tablename__='yield_inspections'
    id=Column(Integer,primary_key=True); natural_key=Column(String(180),unique=True); data=Column(JSON,nullable=False); created=Column(Float)

class Reconciliation(Base):
    """一次抽检对账。snapshot 在排队时冻结底稿、抽检单与口径版本。"""
    __tablename__='yield_reconciliations'
    id=Column(Integer,primary_key=True); name=Column(String(120)); status=Column(String(20))
    snapshot=Column(JSON); checksum=Column(String(64)); result=Column(JSON); error=Column(Text)
    attempts=Column(Integer,default=0); created=Column(Float); updated=Column(Float)
    started=Column(Float); finished=Column(Float)

class ReconciliationGroup(Base):
    """按日期×试制线×班次保存的计算断点；重试时已完成分组直接复用。"""
    __tablename__='yield_reconciliation_groups'
    id=Column(Integer,primary_key=True); reconciliation_id=Column(Integer,index=True)
    group_key=Column(String(64)); status=Column(String(20)); result=Column(JSON); updated=Column(Float)
    __table_args__=(UniqueConstraint('reconciliation_id','group_key',name='uq_reconciliation_group'),)
