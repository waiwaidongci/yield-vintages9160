import hashlib
import json
import time
import traceback
from fastapi import HTTPException
from src.config.settings import CONFIG
from src.models.source import Source, Policy
from src.models.inspection import Inspection, Reconciliation, ReconciliationGroup
from src.services.audit import log
from src.services.pairing import pair_group
from src.services.validation import validate_policy
from src.services.queue import enqueue, RECONCILE_QUEUE

MAX_INPUT_DIFF = CONFIG['max_input_diff']

METHOD = ('按所选期间冻结底稿、抽检单与口径版本；仅在同日期、同试制线、同班次且投入差不超过'
          '{diff}件的记录之间建立候选。先求全批一对一最大配对，再只保留在每一种最大配对方案中'
          '都相同的配对进入良率核算；未唯一确定的配对不参与计算并逐条列出原因。汇总率按分子'
          '合计/分母合计加权，不对批次良率做简单平均。批号仅作单据标识，不参与配对。').format(diff=MAX_INPUT_DIFF)


def snapshot(db, period, policy_id):
    policy = db.get(Policy, policy_id)
    if not policy:
        raise HTTPException(404, '口径版本不存在')
    validate_policy(policy.data)
    if period == '全部':
        raise HTTPException(422, '抽检对账需选择具体期间')
    drafts = [
        _draft(row) for row in db.query(Source).order_by(Source.id).all()
        if row.data.get('period') == period
    ]
    inspections = [
        _inspection(row) for row in db.query(Inspection).order_by(Inspection.id).all()
        if row.data.get('period') == period
    ]
    if not drafts:
        raise HTTPException(422, '所选期间没有试制底稿')
    if not inspections:
        raise HTTPException(422, '所选期间尚未导入抽检单')
    groups = _groups(period, drafts, inspections)
    return {
        'period': period,
        'policy': policy.data,
        'max_input_diff': MAX_INPUT_DIFF,
        'drafts': drafts,
        'inspections': inspections,
        'groups': groups,
    }


def queue_reconciliation(db, user, body):
    data = snapshot(db, body.period, body.policy_id)
    checksum = _checksum(data)
    now = time.time()
    obj = Reconciliation(name=body.name, status='queued', snapshot=data, checksum=checksum,
                         attempts=0, created=now, updated=now)
    db.add(obj)
    log(db, user.email, 'reconciliation.queue', body.name)
    db.commit()
    enqueue(RECONCILE_QUEUE, obj.id)
    return obj


def retry(db, user, reconciliation_id):
    obj = db.get(Reconciliation, reconciliation_id)
    if not obj:
        raise HTTPException(404, '对账不存在')
    if obj.status not in ('failed', 'running'):
        raise HTTPException(409, '仅失败或中断的对账可以重试')
    obj.status = 'queued'
    obj.error = None
    obj.updated = time.time()
    db.add(obj)
    log(db, user.email, 'reconciliation.retry', obj.id)
    db.commit()
    enqueue(RECONCILE_QUEUE, obj.id)
    return obj


def recover_stale(db, running_age=120, queued_age=120):
    """回收中断的任务：running 超时改排队；queued 过久（入队失败或丢失）重新入队。

    已完成分组在重跑时直接复用；重复入队由 claim 的原子认领吸收。
    """
    now = time.time()
    ids = []
    stale = db.query(Reconciliation).filter(
        Reconciliation.status == 'running', Reconciliation.updated < now - running_age,
    ).all()
    for obj in stale:
        obj.status = 'queued'
        obj.updated = now
        ids.append(obj.id)
    orphans = db.query(Reconciliation).filter(
        Reconciliation.status == 'queued', Reconciliation.updated < now - queued_age,
    ).all()
    for obj in orphans:
        obj.updated = now
        ids.append(obj.id)
    db.commit()
    seen = set()
    for reconciliation_id in ids:
        if reconciliation_id in seen:
            continue
        seen.add(reconciliation_id)
        enqueue(RECONCILE_QUEUE, reconciliation_id)
    return sorted(seen)


def claim(db, reconciliation_id):
    """原子认领排队任务，避免同一对账被两个 worker 重复执行。"""
    now = time.time()
    claimed = (
        db.query(Reconciliation)
        .filter(Reconciliation.id == reconciliation_id, Reconciliation.status == 'queued')
        .update({'status': 'running', 'updated': now}, synchronize_session=False)
    )
    db.commit()
    obj = db.get(Reconciliation, reconciliation_id)
    if obj is not None:
        db.refresh(obj)
    if not claimed:
        return None
    obj.attempts = (obj.attempts or 0) + 1
    obj.started = obj.started or now
    obj.updated = now
    db.commit()
    return obj


def process(db, reconciliation_id):
    """执行一次对账；可中断后重试。已完成的日期×线×班分组直接复用，不重复计批。"""
    obj = claim(db, reconciliation_id)
    if obj is None:
        return None
    token = obj.attempts
    try:
        result = _run_groups(db, obj)
    except Exception as exc:
        traceback.print_exc()
        db.rollback()
        return _finish(db, reconciliation_id, token, 'failed', error=str(exc))
    return _finish(db, reconciliation_id, token, 'completed', result=result)


def _finish(db, reconciliation_id, token, status, result=None, error=None):
    """只有仍由本次认领执行的对账才写入终态，避免被回收后重复收尾。"""
    now = time.time()
    values = {'status': status, 'updated': now, 'finished': now}
    if result is not None:
        values['result'] = result
        values['error'] = None
    if error is not None:
        values['error'] = error
    updated = (
        db.query(Reconciliation)
        .filter(Reconciliation.id == reconciliation_id,
                Reconciliation.status == 'running',
                Reconciliation.attempts == token)
        .update(values, synchronize_session=False)
    )
    db.commit()
    if not updated:
        return None
    obj = db.get(Reconciliation, reconciliation_id)
    if obj is not None:
        db.refresh(obj)
    return obj


def _run_groups(db, obj):
    data = obj.snapshot
    drafts = {d['id']: d for d in data['drafts']}
    inspections = {i['id']: i for i in data['inspections']}
    existing = {
        group.group_key: group
        for group in db.query(ReconciliationGroup)
        .filter_by(reconciliation_id=obj.id)
        .order_by(ReconciliationGroup.id)
        .all()
    }
    results = []
    for meta in data['groups']:
        key = meta['key']
        record = existing.get(key)
        if record and record.status == 'completed' and record.result:
            results.append(record.result)
            continue
        group_drafts = [drafts[i] for i in meta['draft_ids']]
        group_inspections = [inspections[i] for i in meta['inspection_ids']]
        result = _run_group(meta, group_drafts, group_inspections, data['policy'])
        if record:
            record.status = 'completed'
            record.result = result
            record.updated = time.time()
        else:
            db.add(ReconciliationGroup(reconciliation_id=obj.id, group_key=key, status='completed',
                                       result=result, updated=time.time()))
        obj.updated = time.time()
        db.commit()
        results.append(result)
    return _combine(data, results)


def _run_group(meta, drafts, inspections, policy):
    pairing = pair_group(drafts, inspections, meta['max_input_diff'])
    pairs = []
    numerator = 0
    denominator = 0
    for u, v in pairing['pairs']:
        draft, inspection = drafts[u], inspections[v]
        num = draft['first_pass'] if policy['numerator'] == 'first_pass' else draft['first_pass'] + draft['reworked_good']
        den = draft['input'] if policy['denominator'] == 'input' else draft['input'] - draft['scrap']
        if den <= 0:
            raise ValueError('口径分母为0：%s %s %s（底稿 %s）' % (draft['period'], draft['department'], draft['shift'], draft['id']))
        numerator += num
        denominator += den
        pairs.append({
            '底稿编号': draft['id'],
            '抽检单编号': inspection['id'],
            '抽检单批号': inspection['batch_no'],
            '底稿投入': draft['input'],
            '抽检投入': inspection['input'],
            '投入差': abs(draft['input'] - inspection['input']),
            '分子': num,
            '分母': den,
            '良率': num / den,
        })
    return {
        'period': meta['period'],
        'department': meta['department'],
        'shift': meta['shift'],
        'draft_count': len(drafts),
        'inspection_count': len(inspections),
        'matching_size': pairing['matching_size'],
        'determined_count': len(pairs),
        'undetermined_count': len(pairing['undetermined']),
        'numerator': numerator,
        'denominator': denominator,
        'yield': numerator / denominator if denominator else 0,
        'pairs': pairs,
        'undetermined': [
            dict(item, period=meta['period'], department=meta['department'], shift=meta['shift'])
            for item in pairing['undetermined']
        ],
    }


def _combine(data, results):
    groups = sorted(results, key=lambda g: (g['period'], g['department'], g['shift']))
    pairs = [pair for group in groups for pair in group['pairs']]
    undetermined = [item for group in groups for item in group['undetermined']]
    numerator = sum(group['numerator'] for group in groups)
    denominator = sum(group['denominator'] for group in groups)
    rows = [
        {
            '日期': group['period'],
            '试制线': group['department'],
            '班次': group['shift'],
            '底稿数': group['draft_count'],
            '抽检单数': group['inspection_count'],
            '最大配对数': group['matching_size'],
            '确定配对数': group['determined_count'],
            '未定数': group['undetermined_count'],
            '分子合计': group['numerator'],
            '分母合计': group['denominator'],
            '良率': group['yield'],
        }
        for group in groups
    ]
    warnings = ['仅确定配对进入良率；最大配对方案不唯一时，未定配对不参与计算，良率只代表可唯一认定部分。']
    if undetermined:
        warnings.append('存在%d条未定记录，请结合原因与候选清单核对底稿或补录抽检单。' % len(undetermined))
    return {
        'summary': {
            '期间': data['period'],
            '底稿数': len(data['drafts']),
            '抽检单数': len(data['inspections']),
            '最大配对数': sum(group['matching_size'] for group in groups),
            '确定配对数': len(pairs),
            '未定配对数': len(undetermined),
            '分子合计': numerator,
            '分母合计': denominator,
            '加权良率': numerator / denominator if denominator else 0,
            '口径版本': data['policy']['version'],
        },
        'rows': rows,
        'pairs': pairs,
        'undetermined': undetermined,
        'method': METHOD,
        'warnings': warnings,
    }


def _groups(period, drafts, inspections):
    buckets = {}
    for item in drafts:
        key = (period, item['department'], item['shift'])
        buckets.setdefault(key, ([], []))[0].append(item['id'])
    for item in inspections:
        key = (period, item['department'], item['shift'])
        buckets.setdefault(key, ([], []))[1].append(item['id'])
    groups = []
    for (group_period, department, shift), (draft_ids, inspection_ids) in sorted(buckets.items()):
        key = _checksum({'drafts': sorted(draft_ids), 'inspections': sorted(inspection_ids),
                         'period': group_period, 'department': department, 'shift': shift})
        groups.append({
            'key': key, 'period': group_period, 'department': department, 'shift': shift,
            'draft_ids': sorted(draft_ids), 'inspection_ids': sorted(inspection_ids),
            'max_input_diff': MAX_INPUT_DIFF,
        })
    return groups


def _draft(row):
    data = row.data
    return {
        'id': row.id,
        'period': data['period'],
        'department': data['department'],
        'shift': data['shift'],
        'input': data['input'],
        'first_pass': data['first_pass'],
        'reworked_good': data['reworked_good'],
        'scrap': data['scrap'],
    }


def _inspection(row):
    data = row.data
    return {
        'id': row.id,
        'batch_no': data['batch_no'],
        'period': data['period'],
        'department': data['department'],
        'shift': data['shift'],
        'input': data['input'],
    }


def _checksum(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
