// API实体字段约定：来源{id,natural_key,data}、抽检单{id,natural_key,data}、口径{id,version,data}、
// 报告{id,status,snapshot,checksum,result,approved}、对账{id,status,snapshot,result,error,attempts}、计划{next_at,enabled}。
export const ENTITY_KEYS=['sources','inspections','reconciliations','policies','runs','schedules','deliveries'];
