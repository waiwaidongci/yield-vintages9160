import time

import pytest
from fastapi import HTTPException
from src.models.identity import User
from src.models.inspection import Inspection, Reconciliation, ReconciliationGroup
from src.models.source import Policy, Source
from src.schemas.requests import ReconciliationInput
from src.services import reconciliation as service
from src.services.inspection import import_csv


def make_user(db, role='admin'):
    user = User(email=role + '@demo.local', role=role, password='x')
    db.add(user)
    db.commit()
    return user


def seed_period(db, period='2026-11-03'):
    drafts = [
        (100, 80, 10, 10, '试制线A', '早班'),
        (112, 90, 12, 10, '试制线A', '早班'),
        (300, 250, 30, 20, '试制线A', '早班'),
        (500, 400, 50, 50, '试制线B', '晚班'),
    ]
    for i, (inp, fp, rw, scrap, dept, shift) in enumerate(drafts, start=1):
        db.add(Source(natural_key='d%d' % i, data={
            'period': period, 'department': dept, 'shift': shift,
            'input': inp, 'first_pass': fp, 'reworked_good': rw, 'scrap': scrap,
        }))
    db.add(Policy(version='一次通过-v1', data={
        'version': '一次通过-v1', 'numerator': 'first_pass', 'denominator': 'input', 'description': '测试口径',
    }))
    db.commit()


def add_inspection(db, batch, inp, dept='试制线A', shift='早班', period='2026-11-03'):
    row = Inspection(natural_key='i-' + batch,
                     data={'batch_no': batch, 'period': period, 'department': dept, 'shift': shift, 'input': inp},
                     created=time.time())
    db.add(row)
    db.commit()
    return row


def queue(db, user, period='2026-11-03'):
    policy = db.query(Policy).first()
    return service.queue_reconciliation(db, user, ReconciliationInput(name='十一月对账', policy_id=policy.id, period=period))


def test_run_uses_only_essential_pairs(db):
    user = make_user(db)
    seed_period(db)
    add_inspection(db, 'B1', 100)
    add_inspection(db, 'B2', 110)
    add_inspection(db, 'B3', 122)
    add_inspection(db, 'B4', 300)
    obj = queue(db, user)
    assert obj.status == 'queued'
    # 排队时冻结快照，之后改底稿不影响本次对账。
    service.process(db, obj.id)
    db.expire_all()
    obj = db.get(Reconciliation, obj.id)
    assert obj.status == 'completed'
    assert obj.result['summary']['确定配对数'] == 1
    assert obj.result['summary']['未定配对数'] == 6
    assert obj.result['pairs'][0]['底稿编号'] == 3
    assert obj.result['pairs'][0]['抽检单批号'] == 'B4'
    # 只有确定配对进入良率：底稿3 分子250/分母300。
    assert obj.result['summary']['分子合计'] == 250
    assert obj.result['summary']['分母合计'] == 300
    assert obj.result['summary']['加权良率'] == pytest.approx(250 / 300)
    # 底稿4（试制线B）没有任何抽检单，列为未定且不参与计算。
    kinds = {(item['kind'], item['id']) for item in obj.result['undetermined']}
    assert ('draft', 4) in kinds


def test_snapshot_keeps_queued_version_after_concurrent_edit(db):
    user = make_user(db)
    seed_period(db)
    add_inspection(db, 'B4', 300)
    obj = queue(db, user)
    draft = db.query(Source).filter_by(natural_key='d3').first()
    draft.data['scrap'] = 0
    draft.data = dict(draft.data)
    db.commit()
    service.process(db, obj.id)
    db.expire_all()
    obj = db.get(Reconciliation, obj.id)
    assert obj.result['pairs'][0]['底稿投入'] == 300
    assert obj.snapshot['drafts'][2]['scrap'] == 20


def test_resume_reuses_completed_groups_without_double_counting(db):
    user = make_user(db)
    seed_period(db)
    add_inspection(db, 'B4', 300)
    add_inspection(db, 'C1', 500, dept='试制线B', shift='晚班')
    obj = queue(db, user)
    groups = obj.snapshot['groups']
    assert len(groups) == 2
    # 模拟首次执行只完成第一组后中断。
    first = groups[0]
    drafts = {d['id']: d for d in obj.snapshot['drafts']}
    inspections = {i['id']: i for i in obj.snapshot['inspections']}
    partial = service._run_group(
        first,
        [drafts[i] for i in first['draft_ids']],
        [inspections[i] for i in first['inspection_ids']],
        obj.snapshot['policy'],
    )
    db.add(ReconciliationGroup(reconciliation_id=obj.id, group_key=first['key'], status='completed',
                               result=partial, updated=time.time()))
    obj.status = 'running'
    db.commit()
    # 重试把 running 重新排队，并复用已完成分组。
    service.retry(db, user, obj.id)
    group_calls = []
    original = service._run_group
    original_combine = service._combine

    def spy(meta, *args, **kwargs):
        group_calls.append(meta['key'])
        return original(meta, *args, **kwargs)

    def spy_combine(data, results):
        combine_calls.append(results)
        return original_combine(data, results)

    combine_calls = []
    service._run_group = spy
    service._combine = spy_combine
    try:
        service.process(db, obj.id)
    finally:
        service._run_group = original
        service._combine = original_combine
    db.expire_all()
    obj = db.get(Reconciliation, obj.id)
    assert obj.status == 'completed'
    assert group_calls == [groups[1]['key']]
    assert len(combine_calls) == 1
    # 两个分组合并后，第二组不会把第一组的批重复计入。
    assert obj.result['summary']['底稿数'] == 4
    assert obj.result['summary']['确定配对数'] == 2


def test_retry_does_not_requeue_completed_reconciliation(db):
    user = make_user(db)
    seed_period(db)
    add_inspection(db, 'B4', 300)
    obj = queue(db, user)
    service.process(db, obj.id)
    with pytest.raises(HTTPException) as exc:
        service.retry(db, user, obj.id)
    assert exc.value.status_code == 409


def test_queue_requires_period_with_drafts_and_inspections(db):
    user = make_user(db)
    seed_period(db)
    with pytest.raises(HTTPException) as exc:
        queue(db, user)
    assert exc.value.status_code == 422
    add_inspection(db, 'B4', 300, period='2026-11-30')
    with pytest.raises(HTTPException) as exc:
        queue(db, user)
    assert exc.value.status_code == 422


def test_failed_reconciliation_records_error_and_can_retry(db):
    user = make_user(db)
    seed_period(db)
    add_inspection(db, 'B4', 300)
    db.query(Policy).first().data = {
        'version': '一次通过-v1', 'numerator': 'first_pass', 'denominator': 'non_scrap', 'description': 'x',
    }
    # 让分母为 0 触发失败：投入300、报废300 的底稿。
    db.add(Source(natural_key='zero', data={
        'period': '2026-11-03', 'department': '试制线C', 'shift': '早班',
        'input': 300, 'first_pass': 0, 'reworked_good': 0, 'scrap': 300,
    }))
    zero = db.query(Source).filter_by(natural_key='zero').first()
    db.add(Inspection(natural_key='i-zero', data={
        'batch_no': 'Z1', 'period': '2026-11-03', 'department': '试制线C', 'shift': '早班', 'input': 300,
    }, created=time.time()))
    db.commit()
    obj = queue(db, user)
    service.process(db, obj.id)
    db.expire_all()
    obj = db.get(Reconciliation, obj.id)
    assert obj.status == 'failed'
    assert '口径分母为0' in obj.error
    assert obj.attempts == 1
    service.retry(db, user, obj.id)
    assert db.get(Reconciliation, obj.id).status == 'queued'


def test_import_csv_forces_selected_period(db):
    user = make_user(db)
    result = import_csv(db, user, '2026-11-03', 'batch_no,period,department,shift,input\nA1,1999-01-01,试制线A,早班,100\nA2,,试制线A,早班,110\n')
    assert result == {'period': '2026-11-03', 'created': 2}
    rows = db.query(Inspection).order_by(Inspection.id).all()
    assert {row.data['period'] for row in rows} == {'2026-11-03'}
    assert {row.data['batch_no'] for row in rows} == {'A1', 'A2'}
    # 中文表头同样可导入。
    import_csv(db, user, '2026-11-04', '抽检单批号,试制线,班次,投入数\n中1,试制线A,早班,120\n')
    assert db.query(Inspection).filter_by(natural_key='missing').first() is None


def test_import_csv_rejects_bad_rows_atomically(db):
    user = make_user(db)
    before = db.query(Inspection).count()
    with pytest.raises(HTTPException) as exc:
        import_csv(db, user, '2026-11-03', 'batch_no,department,shift,input\nA1,试制线A,早班,0\nA2,试制线A,早班,100\n')
    assert exc.value.status_code == 422
    assert '第2行' in exc.value.detail
    assert db.query(Inspection).count() == before


def test_completed_reconciliation_is_not_reprocessed(db):
    user = make_user(db)
    seed_period(db)
    add_inspection(db, 'B4', 300)
    obj = queue(db, user)
    service.process(db, obj.id)
    db.expire_all()
    done = db.get(Reconciliation, obj.id)
    first_result = done.result
    assert service.process(db, obj.id) is None
    db.expire_all()
    again = db.get(Reconciliation, obj.id)
    assert again.result == first_result
    assert again.attempts == 1


def test_stale_running_is_requeued_and_interrupted_attempt_cannot_overwrite(db):
    user = make_user(db)
    seed_period(db)
    add_inspection(db, 'B4', 300)
    obj = queue(db, user)
    first = service.claim(db, obj.id)
    assert first.status == 'running' and first.attempts == 1
    # 模拟执行中断：updated 早于回收阈值。
    first.updated = time.time() - 3600
    db.commit()
    assert service.recover_stale(db) == [obj.id]
    db.expire_all()
    assert db.get(Reconciliation, obj.id).status == 'queued'
    # 第二次认领执行完成后，第一次的收尾不得覆盖结果。
    second = service.claim(db, obj.id)
    assert second.attempts == 2
    service._finish(db, obj.id, 2, 'completed', result={'summary': {'确定配对数': 1}})
    assert service._finish(db, obj.id, 1, 'failed', error='旧执行') is None
    db.expire_all()
    final = db.get(Reconciliation, obj.id)
    assert final.status == 'completed'
    assert final.result == {'summary': {'确定配对数': 1}}


def test_recover_stale_requeues_orphaned_queue_entry(db, monkeypatch):
    user = make_user(db)
    seed_period(db)
    add_inspection(db, 'B4', 300)
    obj = queue(db, user)
    obj.updated = time.time() - 3600
    db.commit()
    enqueued = []
    monkeypatch.setattr(service, 'enqueue', lambda name, job_id: enqueued.append(job_id))
    service.recover_stale(db)
    assert enqueued == [obj.id]
