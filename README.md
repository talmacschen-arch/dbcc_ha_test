# DBCC HA Test Suite

Greenplum 高可用混沌测试套件，用于验证 DBCC 平台在各类故障场景下的自动容灾能力。

## 架构概述

```
┌──────────────┐
│ run_tests.py │  测试编排器：加载场景、顺序执行、汇总结果
└──────┬───────┘
       │
┌──────▼───────┐     ┌────────────────────────────────────────────┐
│  scenarios/  │     │  5 个故障场景 (s01–s05)                     │
│  base.py     │────▶│  每个场景继承 BaseScenario，执行统一生命周期  │
└──────┬───────┘     └────────────────────────────────────────────┘
       │
┌──────▼───────┐
│   utils/     │  SSH 远程执行 · GP 命令封装 · 集群健康检查 · ECS API
└──────────────┘
```

**核心设计原则：**
- **VIP 中心化** — 常规场景下数据库查询均通过 VIP (`192.168.197.99`) 执行，验证 HA 切换的真实效果
- **直连探活** — VIP 冲突场景（如网络隔离）下，支持绕过 VIP 直连 Master IP 执行 `SELECT 1` 探活
- **DBCC 自动容灾** — 框架不实现主备切换逻辑，仅注入故障 → 等待 DBCC 完成 → 验证 → 手动恢复
- **结构化生命周期** — 每个场景遵循 `pre_check → inject_fault → wait_and_validate → restore → post_check` 五阶段模型

## 集群拓扑

| 节点 | 角色 | IP | 主机名 |
|------|------|-----|--------|
| mdw  | Master Coordinator | 192.168.195.236 | synxdb-0001 |
| std  | Standby Coordinator | 192.168.199.179 | synxdb-0002 |
| sdw1 | Segment Host 1 | 192.168.194.60 | synxdb-0003 |
| sdw2 | Segment Host 2 | 192.168.193.23 | synxdb-0004 |

**VIP:** `192.168.197.99` (由 DBCC 在主备间漂移)

## 测试场景

| 场景 | 描述 | 故障方式 | 预期恢复 |
|------|------|---------|---------|
| **S01** | Master 进程自愈 | `pkill -9 postgres` (mdw) | Systemd 自动重启 postgres 进程 |
| **S02** | Master 关机 + HA 切换 | ECS 关机 (mdw) | DBCC 激活 Standby、VIP 漂移到 std，恢复后重启 dbcc-agent |
| **S03** | Standby 网络隔离 | 安全组切换 deny-all (std) | DBCC 检测异常、激活 mdw（直连探活验证）、关机 std 消除 VIP 冲突，恢复后重启 dbcc-agent |
| **S04** | Segment 进程故障 | `kill -9 postmaster` (sdw1 gpseg0) | FTS 检测 → Mirror 提升为 Primary |
| **S05** | Segment 主机关机 | ECS 关机 (sdw1) | 所有 sdw1 segment 标记 down，Mirror 接管，恢复后重启 PXF |

## 快速开始

### 前置条件

- Python 3.6+
- 华为云 SDK：`huaweicloudsdkcore`、`huaweicloudsdkecs`、`huaweicloudsdkvpc`
- 4 节点 Greenplum 集群已部署、DBCC HA 已启用
- root 和 gpadmin 用户 SSH 免密登录已配置

### 运行测试

```bash
# 运行全部场景（顺序执行，场景间 120 秒冷却）
python3 run_tests.py

# 运行指定场景
python3 run_tests.py s01 s03

# 查看可用场景列表
python3 run_tests.py --list
```

### 基础设施管理（手动）

```bash
# 查看所有 ECS 状态
python3 hw_ecs_manage.py status all

# 查看安全组
python3 hw_ecs_manage.py show-sg mdw

# 关机/开机
python3 hw_ecs_manage.py stop mdw
python3 hw_ecs_manage.py start all

# 切换安全组（模拟网络隔离）
python3 hw_ecs_manage.py switch-sg sg-chenqiang sg-deny-all-test std
```

## 项目结构

```
dbcc_ha_test/
├── run_tests.py              # 主入口：场景加载、顺序执行、结果汇总
├── config.py                 # 全局配置：集群拓扑、超时、安全组、日志路径
├── hw_ecs_manage.py          # 华为云 ECS 管理 CLI 工具
├── scenarios/
│   ├── base.py               # BaseScenario：定义五阶段生命周期 + 应急恢复
│   ├── s01_mdw_kill_postgres.py   # 场景1：Master postgres 进程自愈
│   ├── s02_mdw_shutdown.py        # 场景2：Master 关机 + DBCC 主备切换
│   ├── s03_std_network_isolate.py # 场景3：Standby 网络隔离 + 切回
│   ├── s04_sdw1_kill_segment.py   # 场景4：Segment 进程故障恢复
│   └── s05_sdw1_shutdown.py       # 场景5：Segment 主机关机恢复
├── utils/
│   ├── ssh.py                # SSH 远程命令执行 (run_on_host, run_gp_cmd)
│   ├── gp_commands.py        # Greenplum 命令封装 (psql, psql_direct, gpstart, gprecoverseg 等)
│   ├── health_check.py       # 集群健康检查 (VIP 可达、Master 直连探活、Segment 状态、同步状态)
│   ├── ecs_api.py            # ECS API 封装 (关机、开机、网络隔离/恢复)
│   └── log.py                # 日志 & 计时工具
└── logs/                     # 测试日志输出目录
```

## 场景生命周期

每个场景继承 `BaseScenario`，执行以下生命周期：

```
┌─────────────┐    ┌──────────────┐    ┌────────────────────┐    ┌───────────┐    ┌────────────┐
│  pre_check  │───▶│ inject_fault │───▶│ wait_and_validate  │───▶│  restore  │───▶│ post_check │
│ 验证集群健康 │    │   注入故障    │    │  等待恢复 & 验证   │    │  手动恢复  │    │ 验证恢复完成│
└─────────────┘    └──────────────┘    └────────────────────┘    └───────────┘    └────────────┘
        │                                                                                │
        └────────── 任意阶段失败 → emergency_restore() 应急恢复 ─────────────────────────┘
```

每个阶段独立计时，结果汇总输出。

## 恢复流程说明

### Coordinator 恢复（S02/S03）

1. 启动宕机节点 ECS（如网络隔离场景，先关机 std → 恢复安全组 → 再开机）
2. 等待 SSH 可达
3. 清理旧 Coordinator 数据目录 (`gpseg-1`)
4. 在新 Master 上执行 `gpinitstandby -s <旧节点>` 重建 Standby
5. 重启 mdw 和 std 上的 `dbcc-agent` 服务

> **S03 VIP 冲突说明**：网络隔离（deny-all 安全组）后，std 上的 VIP 仍然绑定在网卡上，DBCC 虽然会将 VIP 强制附加到 mdw，但由于同网段存在两个相同 IP，会导致 VIP 路由不通。因此 S03 使用**直连 mdw IP 执行 `SELECT 1`** 来判断 DBCC 切换是否成功，待关机 std 消除 VIP 冲突后再验证 VIP 可用性。

### Segment 恢复（S04/S05）

1. 启动宕机节点（S05）
2. `gprecoverseg -a`：增量恢复（命令返回代表 pg_rewind + segment 启动完成，WAL 同步仍在后台进行）
3. 等待 WAL 同步完成（轮询 `gp_segment_configuration` 直到所有 segment 达到 `mode='s'`）
4. `gprecoverseg -r`：Rebalance 回首选角色
5. 等待 Rebalance 后 WAL 同步完成
6. 恢复 FTS 参数，重启 PXF 集群（S05）

## 超时配置

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `TIMEOUT_PROCESS_RECOVERY` | 300s | postgres 进程自愈等待 |
| `TIMEOUT_DBCC_FAILOVER` | 600s | DBCC 主备切换等待 |
| `TIMEOUT_ECS_BOOT` | 120s | ECS 开关机等待 |
| `TIMEOUT_SEGMENT_RECOVERY` | 600s | Segment 恢复等待（需大于 FTS probe_interval + connect_timeout） |
| `TIMEOUT_GPRECOVERSEG` | 1800s | gprecoverseg 命令执行超时（pg_rewind + segment 启动） |
| `TIMEOUT_RESYNC` | 600s | gprecoverseg 后 WAL 同步等待（轮询至 mode='s'） |
| `POLL_INTERVAL` | 10s | 健康检查轮询间隔 |
| `DBCC_STABILIZE_WAIT` | 120s | VIP 可连后等待 DBCC 检测结果稳定 |

## 日志

测试日志存储在 `logs/` 目录，命名格式：`ha_test_<场景>_<时间戳>.log`

- 文件日志：DEBUG 级别（详细诊断信息）
- 控制台日志：INFO 级别（关键状态更新）
