---
name: dbcc_ha_test
description: Greenplum DBCC high-availability chaos test suite. Inject faults (process kill, node shutdown, network isolation) and validate automatic failover and recovery. Use when user needs to run HA tests, verify DBCC failover, or test disaster recovery scenarios.
metadata: { "openclaw": { "emoji": "🔬", "requires": { "bins": ["python3", "ssh", "psql"] } } }
---

# DBCC HA Test Suite

对 Greenplum/SynxDB 集群注入故障（进程 kill、节点关机、网络隔离），验证 DBCC 平台的自动容灾与恢复能力。框架只负责“注入故障 → 等待 DBCC 处理 → 校验 → 手动恢复”，不实现主备切换逻辑本身。

## When to Use

✅ **使用本 skill 当用户要：**

- “跑一次 DBCC HA 演练 / 高可用测试 / 故障切换验证”
- “验证 coordinator 宕机后能否自动切到 standby”
- “测试网络隔离 / 节点关机 / segment 故障下的容灾”
- “跑某个场景 s01 / s02 / s03 / s04 / s05”
- “演练完集群没恢复，帮忙排查/恢复”
- “新增一个故障场景”

## When NOT to Use

❌ **不要用本 skill 当：**

- 性能压测 / benchmark → 用专门的负载工具
- 升级后功能回归 → 用 `post-upgrade-test` skill
- 只是查询集群状态而不注入故障 → 直接 psql / gpstate
- 生产环境（本 skill 会真实关机、隔离网络、杀进程，**仅限测试集群**）

## Prerequisites

- 运行机到集群各节点（mdw/std/sdw1/sdw2）**SSH 免密**（root 与 gpadmin）。
- `psql` 可用；数据库口令通过 `PGPASSWORD` 环境变量提供（不写进代码）。
- 华为云 ECS 凭据通过环境变量提供（关机/开机/安全组切换用）——见 Configuration。
- Python 依赖：`huaweicloudsdkcore`、`huaweicloudsdkecs`、`huaweicloudsdkvpc`（`pip3 install`）。
- `metadata.requires.bins`：`python3`、`ssh`、`psql`。

## Configuration

### 集群拓扑与超时 — `config.py`

`CLUSTER`（各节点 ip/hostname/ecs_id）、`VIP`、`GP_USER`/`GP_PORT`、各类 `TIMEOUT_*` 与 `POLL_INTERVAL` 均在 `config.py` 集中配置。换集群时改这里。

### 华为云凭据 — 环境变量（勿硬编码）

`hw_ecs_manage.py` 从环境变量读取凭据，缺失即报错。复制 `.env.example` 为 `.env` 并填值（`.env` 已被 `.gitignore` 排除；脚本会自动加载同目录 `.env`）：

```bash
export HW_ECS_AK="..."
export HW_ECS_SK="..."
export HW_ECS_PROJECT_ID="..."
# 可选：HW_ECS_ENDPOINT / HW_VPC_ENDPOINT（默认 cn-north-4）
```

### 数据库口令

```bash
export PGPASSWORD="..."   # 供 psql 健康检查 / 状态查询使用
```

## Usage

主入口 `run_tests.py`（顺序执行，场景间有 120s 冷却）：

```bash
python3 run_tests.py            # 运行全部场景（s01→s05，任一 FAIL 即终止）
python3 run_tests.py s01 s03    # 只运行指定场景
python3 run_tests.py --list     # 列出所有场景
```

基础设施手动管理 `hw_ecs_manage.py`（一般由场景自动调用，也可手动）：

```bash
python3 hw_ecs_manage.py status all       # 查看 ECS 状态
python3 hw_ecs_manage.py show-sg mdw      # 查看节点安全组
python3 hw_ecs_manage.py stop  mdw        # 关机
python3 hw_ecs_manage.py start all        # 开机
```

退出码：全部通过为 0，否则非 0；结尾打印 `PASS/FAIL` 汇总。

## Scenarios

| 场景 | 故障注入 | 期望 DBCC 行为 | 手动恢复 |
|------|----------|----------------|----------|
| `s01` | mdw `pkill -9 postgres` | 进程级自愈（拉起 postgres） | 无需 |
| `s02` | mdw 关机 | 激活 std 为新 master + VIP 切换 | 重建 standby、重启 dbcc-agent |
| `s03` | std 安全组隔离（网络分区） | 回切 mdw 为 master + VIP 切换 | 关机消除 VIP 冲突、重建 standby |
| `s04` | sdw1 kill primary segment | FTS 检测 → mirror 提升 | `gprecoverseg` 恢复 + rebalance |
| `s05` | sdw1 关机 | mirror 提升、集群降级运行 | 开机 + `gprecoverseg` + 重启 PXF |

## Scenario Lifecycle

每个场景继承 `BaseScenario`（`scenarios/base.py`），按固定五阶段执行，任一阶段异常都会触发 `emergency_restore()` 兜底恢复：

```
pre_check → inject_fault → wait_and_validate → restore → post_check
                                                          （失败时 emergency_restore）
```

## Health Check（判活方式，重要）

`utils/health_check.py`：

- **集群数据面健康**（coordinator/standby/segment）**直连当前 master 判定**（绕过 VIP）——`get_segment_config_direct()` 依次尝试候选 coordinator，谁应答谁就是活跃 master。
- **VIP 回绑**是**独立一项**（`vip_reachable`），与数据面健康**解耦**。
- 因此 VIP 未回绑不会把健康的集群误报成 “coordinator down / 0/0”；VIP 与数据面各自独立归因，任一异常都会导致整体 FAIL。

## 新增场景

1. 建文件 `scenarios/s06_<描述>.py`，`import` 后继承 `BaseScenario`，导出 `Scenario` 类；
2. 实现生命周期方法 + `emergency_restore()`；
3. 在 `run_tests.py` 的 `SCENARIOS` 表登记一行（键为 `s06`，值为模块路径与描述）。

```python
from scenarios.base import BaseScenario

class Scenario(BaseScenario):
    name = "s06_描述"
    description = "场景描述"

    def inject_fault(self): ...
    def wait_and_validate(self): ...
    def restore(self): ...
    def emergency_restore(self): ...
```

## 更多背景

领域知识（Greenplum 运维、DBCC HA 机制、华为云 ECS、SSH 自动化、混沌工程等）见 [`docs/skills-matrix.md`](docs/skills-matrix.md)；整体架构与拓扑见 [`README.md`](README.md)。
