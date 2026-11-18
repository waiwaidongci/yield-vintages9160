# 抽检对账结构

新增三张表，均为追加式变更，不改动已有表：

- `yield_inspections`：按期间导入的抽检单（批号、日期、试制线、班次、投入数）。`natural_key` 唯一，CSV 导入逐行生成。
- `yield_reconciliations`：一次对账。`snapshot` 冻结排队时的底稿、抽检单、口径版本与分组清单；`checksum` 为快照 SHA256；`attempts` 记录认领次数；`status` 复用 queued/running/completed/failed。
- `yield_reconciliation_groups`：按日期×试制线×班次的分组断点，`(reconciliation_id, group_key)` 唯一，`result` 保存已完成分组。重试时已完成分组直接复用，避免重复计批。

## 升级已有库
1. 备份数据库。
2. 启动 backend 由 SQLAlchemy 创建缺失表（初始结构说明见 001）。
3. 如已有 `yield_runs` 等表，无需改列；本次不含破坏性迁移。
4. 需要演示数据时执行 `python -m scripts.seed_demo`；抽检单自然键幂等，不覆盖用户修改。
