"""场景3: std 切换安全组模拟网络中断，DBCC 自动回切 mdw，手动恢复"""

import time
from scenarios.base import BaseScenario
from utils.ecs_api import ecs_isolate, ecs_restore_network, ecs_stop, ecs_start
from utils.ssh import wait_for_host, remove_localhost_from_hosts, run_on_host
from utils.gp_commands import (
    can_connect_via_vip, cleanup_coordinator_dir, gpinitstandby,
)
from utils.health_check import (
    wait_for_vip, assert_healthy, wait_for_master_direct, check_master_direct,
)
from utils.log import Timer
from config import TIMEOUT_DBCC_FAILOVER, TIMEOUT_ECS_BOOT


class Scenario(BaseScenario):
    name = "s03_std_network_isolate"
    description = "std 安全组隔离，DBCC 激活 mdw + VIP 切换，手动恢复 standby"

    def inject_fault(self):
        self.log.info("切换 std 安全组到 sg-deny-all-test (网络隔离)")
        ecs_isolate("std")
        time.sleep(5)
        self.log.info("std 网络已隔离")

    def wait_and_validate(self):
        # DBCC 发现 std (当前 master) 不可达 → 激活 mdw → VIP 切回 mdw
        # 注意: 网络隔离后 std 上 VIP 仍存在，导致 VIP 冲突不可用，
        # 因此用直连 mdw 的方式判断 DBCC 是否已将 mdw 激活为 master
        self.log.info("等待 DBCC 完成 HA 切换 (直连 mdw 探活)...")
        with Timer("dbcc_failover") as t:
            ok = wait_for_master_direct("mdw", timeout=TIMEOUT_DBCC_FAILOVER)
        self.timers["dbcc_failover"] = t.elapsed

        if not ok:
            raise RuntimeError("DBCC HA 切换未在超时内完成 (mdw 直连不可用)")
        self.log.info(f"DBCC HA 切换完成，mdw 已激活为 master，耗时 {t.elapsed:.0f}s")

        # 关机 std ECS — 消除 VIP 冲突
        self.log.info("关机 std ECS (消除 VIP 冲突)...")
        ok = ecs_stop("std")
        if not ok:
            raise RuntimeError("std ECS 关机失败")
        self.log.info("std ECS 已关机")

        # std 关机后 VIP 冲突消除，验证 VIP 可用
        if can_connect_via_vip():
            self.log.info("std 关机后 VIP 连接验证成功")
        else:
            self.log.warning("std 已关机但 VIP 仍不可达，可能需要等待 ARP 刷新")

    def restore(self):
        # 1. 关机 std (安全组仍是 deny-all，先关机再改)
        self.log.info("关机 std ECS...")
        ok = ecs_stop("std")
        if not ok:
            raise RuntimeError("std ECS 关机失败")

        # 2. 切回安全组
        self.log.info("恢复 std 安全组到 sg-chenqiang")
        ecs_restore_network("std")

        # 3. 开机 std
        self.log.info("开机 std ECS...")
        ok = ecs_start("std")
        if not ok:
            raise RuntimeError("std ECS 开机失败")

        # 4. 等待 std SSH 可达
        if not wait_for_host("std", timeout=TIMEOUT_ECS_BOOT):
            raise RuntimeError("std 开机后 SSH 不可达")

        # 4.1 清理 /etc/hosts 中的 127.0.0.1 条目
        remove_localhost_from_hosts("std")

        # 5. 清理 std 上的 coordinator 目录
        cleanup_coordinator_dir("std")

        # 6. 在 mdw (当前 master) 上重做 standby
        self.log.info("在 mdw 上执行 gpinitstandby -s std")
        result = gpinitstandby(standby_host="std", master_host="mdw")
        if not result.ok:
            raise RuntimeError(f"gpinitstandby 失败: {result.stderr}")
        self.log.info("standby 重建完成")

        # 7. 重启 mdw 和 std 上的 dbcc-agent
        self.log.info("重启 mdw 和 std 上的 dbcc-agent...")
        for host in ("mdw", "std"):
            result = run_on_host(host, "systemctl restart dbcc-agent")
            if not result.ok:
                self.log.warning(f"{host} dbcc-agent 重启失败: {result.stderr}")
            else:
                self.log.info(f"{host} dbcc-agent 已重启")

    def emergency_restore(self):
        """紧急恢复：确保安全组恢复"""
        try:
            ecs_stop("std")
        except Exception:
            self.log.error("紧急恢复: std 关机失败，需手动处理")
        try:
            ecs_restore_network("std")
        except Exception:
            self.log.error("紧急恢复: std 安全组恢复失败，需手动处理")
        try:
            ecs_start("std")
            wait_for_host("std", timeout=TIMEOUT_ECS_BOOT)
        except Exception:
            self.log.error("紧急恢复: std 开机失败，需手动处理")
