"""DBCC HA 测试 - 全局配置"""

import os

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

# ========== 集群拓扑 ==========
CLUSTER = {
    "mdw":  {"ip": "192.168.195.236", "hostname": "synxdb-0001", "ecs_id": "41b83973-b91e-4369-b41a-e757ef055900"},
    "std":  {"ip": "192.168.199.179", "hostname": "synxdb-0002", "ecs_id": "5f450779-b9d0-4c72-9ccf-5056b9dd2483"},
    "sdw1": {"ip": "192.168.194.60",  "hostname": "synxdb-0003", "ecs_id": "1bc1ded3-f5da-4100-baaf-02fdf335e40a"},
    "sdw2": {"ip": "192.168.193.23",  "hostname": "synxdb-0004", "ecs_id": "2a06599b-83f5-4719-bc94-dbf973cf1e61"},
}

VIP = "192.168.197.99"

# ========== GP 配置 ==========
GP_USER = "gpadmin"
GP_PORT = 5432
COORDINATOR_DATA_DIR = "/data0/light2data/coordinator/gpseg-1"
TARGET_SEGMENT_DATADIR = "/data0/light2data/primary/gpseg0"

# ========== 安全组 ==========
SG_NORMAL = "sg-chenqiang"
SG_DENY = "sg-deny-all-test"

# ========== 超时 (秒) ==========
TIMEOUT_PROCESS_RECOVERY = 300      # 场景1: postgres 进程自愈
TIMEOUT_DBCC_FAILOVER = 600         # 场景2/3: DBCC 完成 HA 切换
TIMEOUT_ECS_BOOT = 120              # ECS 开关机
TIMEOUT_SEGMENT_RECOVERY = 600      # 场景4/5: segment 恢复 (需大于 probe_interval + connect_timeout)
TIMEOUT_GPRECOVERSEG = 1800         # gprecoverseg 执行超时
TIMEOUT_RESYNC = 600                # gprecoverseg 后等待 resync

POLL_INTERVAL = 10                  # 轮询间隔
DBCC_STABILIZE_WAIT = 120           # VIP 可连后等待 DBCC 检测结果稳定

# ========== 日志 ==========
LOG_DIR = os.path.join(_PROJECT_ROOT, "logs")
