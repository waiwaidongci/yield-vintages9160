from fastapi import APIRouter,Depends,HTTPException
from fastapi.responses import Response
from src.config.database import database
from src.controllers.auth import current,editor,admin
from src.models.inspection import Inspection,Reconciliation
from src.schemas.requests import InspectionInput,InspectionImport,ReconciliationInput
from src.services.inspection import save_inspection,import_csv
from src.services.reconciliation import queue_reconciliation,retry
from src.services.export import csv_text
from src.utils.serialize import row
router=APIRouter()

@router.get('/inspections')
def inspections(db=Depends(database),u=Depends(current)): return [row(x) for x in db.query(Inspection).order_by(Inspection.id).all()]

@router.post('/inspections')
def add_inspection(b:InspectionInput,db=Depends(database),u=Depends(editor)): return row(save_inspection(db,u,b.data))

@router.post('/inspections/import')
def import_inspections(b:InspectionImport,db=Depends(database),u=Depends(editor)): return import_csv(db,u,b.period,b.csv)

@router.get('/reconciliations')
def reconciliations(db=Depends(database),u=Depends(current)): return [row(x) for x in db.query(Reconciliation).order_by(Reconciliation.id.desc()).all()]

@router.post('/reconciliations')
def create(b:ReconciliationInput,db=Depends(database),u=Depends(editor)): return row(queue_reconciliation(db,u,b))

@router.get('/reconciliations/{id}')
def detail(id:int,db=Depends(database),u=Depends(current)):
 obj=db.get(Reconciliation,id)
 if not obj: raise HTTPException(404,'对账不存在')
 return row(obj)

@router.post('/reconciliations/{id}/retry')
def retry_reconciliation(id:int,db=Depends(database),u=Depends(editor)): return row(retry(db,u,id))

@router.get('/reconciliations/{id}/csv')
def download(id:int,db=Depends(database),u=Depends(current)):
 obj=db.get(Reconciliation,id)
 if not obj or obj.status!='completed': raise HTTPException(409,'对账尚未完成')
 if not obj.result.get('pairs'): raise HTTPException(409,'没有确定配对可导出')
 return Response(csv_text({'rows':obj.result['pairs']}),media_type='text/csv',headers={'Content-Disposition':f'attachment; filename="reconciliation-{id}.csv"'})
