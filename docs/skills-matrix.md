# DBCC HA Test Suite — 技能矩阵

本文档梳理开发和维护本测试套件所需的技能点，供团队成员参考。
（操作说明见 [`../SKILL.md`](../SKILL.md)，架构见 [`../README.md`](../README.md)。）

---

## 1. Greenplum 数据库

### 核心概念
- **MPP 架构**：理解 Coordinator（Master/Standby）与 Segment（Primary/Mirror）的角色分工
- **gp_segment_configuration**：系统表，记录每个 segment 的角色（`role`）、首选角色（`preferred_role`）、状态（`status`）、同步模式（`mode`）
- **FTS (Fault Tolerance Service)**：故障检测服务，定期探测 Primary segment 存活状态
  - `gp_fts_probe_interval`：探测间隔
  - `gp_fts_probe_timeout`：探测超时
  - `gp_fts_probe_retries`：探测重试次数
  - `gp_segment_connect_timeout`：FTS 连接 segment 的 TCP 超时，直接决定 Primary 故障检测速度（主机宕机场景下 FTS 需等待此超时才能判定 Primary 失败并触发 Mirror 提升）

### 关键运维命令
| 命令 | 用途 | 本项目使用场景 |
|------|------|---------------|
| `gpstart` / `gpstop` | 启停集群 | 重载配置（`gpstop -u`） |
| `gprecoverseg -a` | 增量恢复 segment（pg_rewind + 启动，WAL 同步在后台进行） | S04/S05 增量恢复 |
| `gprecoverseg -aF` | 全量恢复 segment（pg_basebackup） | 增量恢复无法修复时手动执行 |
| `gprecoverseg -r` | Rebalance 到首选角色（需所有 segment 已同步） | 恢复后将 Primary/Mirror 归位 |
| `gpinitstandby` | 初始化/重建 Standby Coordinator | S02/S03 重建 Standby |
| `gpconfig` | 修改集群配置参数 | 调整 FTS 探测参数 |
| `psql` | 数据库查询 | 健康检查、segment 状态查询（VIP 或直连） |

### 必须理解的流程
- **Mirror 提升**：Primary segment 宕机 → FTS 检测 → Mirror 提升为 Primary → 集群降级运行
- **Segment 恢复**：增量恢复（gprecoverseg -a） → 等待 WAL 后台同步完成 → Rebalance（gprecoverseg -r） → 验证角色与同步状态 → 重启 PXF
- **Standby 重建**：清理旧 Coordinator 数据目录 → `gpinitstandby -s <host>` → 验证同步 → 重启 dbcc-agent

---

## 2. DBCC 平台 HA 机制

### 核心理解
- DBCC 独立于 Greenplum 运行，负责监控 Coordinator 健康状态
- **自动容灾流程**：检测 Master 故障 → 激活 Standby → 漂移 VIP → Standby 成为新 Master
- **VIP (Virtual IP)**：`192.168.197.99`，由 DBCC 管理，始终指向当前活跃的 Master
- **测试框架的边界**：只注入故障和做手动恢复，不参与自动容灾逻辑

### VIP 冲突场景
- **问题**：网络隔离（安全组 deny-all）后，被隔离节点上的 VIP 仍绑定在网卡上，DBCC 将 VIP 强制附加到新 Master 时，同网段出现两个相同 IP，导致 VIP 路由不通
- **解决**：绕过 VIP，直连目标 Master IP 执行 `SELECT 1` 探活（`psql_direct`），待被隔离节点关机后 VIP 冲突自动消除
- **适用场景**：S03（std 网络隔离）

### 需要掌握的判断能力
- 通过直连当前 Master 判断集群数据面健康，VIP 回绑作为独立一项判断
- 判断 DBCC 容灾是否已完成：常规场景通过 VIP 可达判断；VIP 冲突场景通过直连 Master IP 探活判断
- 识别 VIP 冲突场景：网络隔离 ≠ 节点宕机，被隔离节点仍持有 VIP 会导致冲突
- 区分"DBCC 自动处理"和"需要手动恢复"的边界

---

## 3. 华为云 ECS 操作

### SDK 使用
- **HuaweiCloud Python SDK**：`huaweicloudsdkcore`、`huaweicloudsdkecs`、`huaweicloudsdkvpc`
- 认证方式：AK/SK + Project ID（从环境变量读取，见 `.env.example`）
- API 区域：`cn-north-4`

### 关键操作
| 操作 | API | 用途 |
|------|-----|------|
| 查询 ECS 状态 | `ShowServer` | 确认节点在线/离线 |
| 关机 | `BatchStopServers` | 模拟主机宕机 (S02/S05) |
| 开机 | `BatchStartServers` | 恢复宕机节点 |
| 查询安全组 | `ShowPort` / `ListPorts` | 确认网络隔离状态 |
| 切换安全组 | `UpdatePort` | 模拟网络隔离 / 恢复 (S03) |

### 网络隔离模拟
- **隔离**：将节点网卡绑定到 deny-all 安全组 → 模拟网络分区
- **VIP 冲突**：被隔离节点的 VIP 不会自动释放，DBCC 在新 Master 上附加 VIP 后形成 IP 冲突，需关机被隔离节点才能消除
- **恢复**：切回正常安全组 → 需要先关机再开机才能完全恢复网络栈

---

## 4. Linux & SSH 远程管理

### SSH 自动化
- **免密登录**：root 和 gpadmin 用户均通过 SSH Key 认证
- **远程命令执行**：`subprocess` 调用 `ssh` 命令，`StrictHostKeyChecking=no`
- **用户切换**：通过 `su - gpadmin -c '...'` 在远程节点执行 GP 命令
- **Shell 引号转义**：嵌套 SSH + su 场景下的命令引号处理

### 进程管理
- `pkill -9 postgres`：强制杀死 postgres 进程（S01）
- `kill -9 <pid>`：杀死指定 segment 的 postmaster（S04）
- 通过 `postmaster.pid` 文件获取 segment 进程 PID

### 服务管理
- `systemctl restart dbcc-agent`：重启 DBCC Agent 服务（S02/S03 恢复后）
- `pxf cluster restart`：重启 PXF 集群（S05 恢复后，需 `su - gpadmin` 执行）

### 系统管理
- `/etc/hosts` 修改：某些恢复场景需要清理 localhost 条目
- 文件系统操作：清理 Coordinator 数据目录

---

## 5. Python 开发

### 项目使用的模式
- **动态模块加载**：`importlib.import_module()` 按名称加载场景模块
- **Dataclass**：`CmdResult(rc, stdout, stderr)` 封装命令执行结果
- **Context Manager**：`Timer` 类用于阶段计时
- **模板方法模式**：`BaseScenario` 定义生命周期骨架，子类实现具体步骤
- **轮询等待模式**：循环检查 + sleep + 超时判断

### 编码规范
- 无外部测试框架依赖（不使用 pytest/unittest）
- 日志分级：DEBUG（文件）+ INFO（控制台）
- 每个场景一个模块，导出 `Scenario` 类
- 配置集中在 `config.py`，敏感凭据从环境变量读取

---

## 6. 测试与故障排查

### 测试设计能力
- **混沌工程思维**：设计故障注入 → 观察系统行为 → 验证恢复
- **前置/后置检查**：确保测试前集群健康、测试后完全恢复
- **应急恢复设计**：每个场景的 `emergency_restore()` 保证即使测试失败也能恢复集群

### 故障排查清单
| 问题 | 排查方向 |
|------|---------|
| VIP 不可达 | DBCC 服务状态、网络配置、安全组规则、是否存在 VIP 冲突（多节点同时持有 VIP） |
| Segment 未恢复 | FTS 日志、`gp_segment_configuration`、segment 日志 |
| gprecoverseg 失败 | 数据目录权限、磁盘空间、网络连通性 |
| gpinitstandby 失败 | 旧数据目录是否清理、SSH 互信、pg_hba.conf |
| ECS 操作超时 | 华为云控制台确认、API 凭证有效性 |
| 测试间状态残留 | 120 秒冷却是否足够、上一场景是否完全恢复 |

---

## 技能等级参考

| 技能领域 | 阅读/维护代码 | 新增场景 | 架构调整 |
|---------|:----------:|:-------:|:-------:|
| Greenplum 运维 | 必须 | 必须 | 必须 |
| DBCC HA 机制 | 了解 | 必须 | 深入 |
| 华为云 ECS | 了解 | 按需 | 按需 |
| SSH 自动化 | 了解 | 必须 | 必须 |
| Python | 基础 | 中级 | 中级 |
| 混沌工程 | 了解 | 必须 | 深入 |
