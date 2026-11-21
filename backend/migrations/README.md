# 数据结构
本小型项目首次启动使用 SQLAlchemy metadata 建立 users/nodes/scenarios/drills/audits 五表。自然键 users.email、nodes.name、scenarios.name 唯一。drills.scenario_id 为外键，快照在入队时固定。上线结构演进须补充版本化迁移，本项目不支持对既有不同结构数据库自动升级。

换钥演练另建六表：
- rekey_keys：密钥环，label 唯一；retain_for_recovery 决定退役后旧钥是否留作故障恢复。
- rekey_runs：一次换钥的 old/new 密钥、批量、tick 与退役时间；创建时装载页面并固定备库。
- rekey_pages：(run_id, page_no) 唯一；key_id 表示当前页归哪把钥，version 用于并发写入优先判定。
- rekey_replicas：(run_id, name) 唯一；applied_version 记录该备库已复制到第几页，new_key_ready 表示是否已获新钥。
- rekey_batches / rekey_batch_items：(run_id, batch_no) 与 (batch_id, page_id) 唯一；items 保存 expected_version，保证同批重试不重复改页。
- rekey_failovers：保存切主输入快照与含“新钥全覆盖”门禁的结果。

六表由 seed 的 create_all 在首次启动建立；与其他表一致，不支持对既有不同结构自动升级。
