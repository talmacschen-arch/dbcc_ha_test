"""场景2: mdw 关机，DBCC 自动激活 std 并切换 VIP，然后手动恢复"""

import time
from scenarios.base import BaseScenario
from utils.ecs_api import ecs_stop, ecs_start
from utils.ssh import wait_for_host, remove_localhost_from_hosts, run_on_host
from utils.gp_commands import (
    can_connect_via_vip, cleanup_coordinator_dir, gpinitstandby, gpstate_standby,
)
from utils.health_check import wait_for_vip, assert_healthy
from utils.log import Timer
from config import TIMEOUT_DBCC_FAILOVER, TIMEOUT_ECS_BOOT


class Scenario(BaseScenario):
    name = "s02_mdw_shutdown"
    description = "mdw 关机，DBCC 激活 std + VIP 切换，手动恢复 standby"

    def inject_fault(self):
        self.log.info("关机 mdw ECS")
        ok = ecs_stop("mdw")
        if not ok:
            raise RuntimeError("mdw ECS 关机失败")
        self.log.info("mdw 已关机")

    def wait_and_validate(self):
        # 等待 DBCC 激活 std 并切换 VIP
        self.log.info("等待 DBCC 完成 HA 切换 (激活 std, VIP 切换)...")
        with Timer("dbcc_failover") as t:
            ok = wait_for_vip(timeout=TIMEOUT_DBCC_FAILOVER)
        self.timers["dbcc_failover"] = t.elapsed

        if not ok:
            raise RuntimeError("DBCC HA 切换未在超时内完成")
        self.log.info(f"DBCC HA 切换完成，耗时 {t.elapsed:.0f}s")

        # 验证 std 已成为 master
        if can_connect_via_vip():
            self.log.info("通过 VIP 连接 DB 成功，std 已激活为 master")
        else:
            raise RuntimeError("VIP 可达但 DB 连接失败")

    def restore(self):
        # 1. 开机 mdw
        self.log.info("开机 mdw ECS...")
        ok = ecs_start("mdw")
        if not ok:
            raise RuntimeError("mdw ECS 开机失败")

        # 2. 等待 mdw SSH 可达
        if not wait_for_host("mdw", timeout=TIMEOUT_ECS_BOOT):
            raise RuntimeError("mdw 开机后 SSH 不可达")

        # 2.1 清理 /etc/hosts 中的 127.0.0.1 条目
        remove_localhost_from_hosts("mdw")

        # 3. 清理 mdw 上的 coordinator 目录
        cleanup_coordinator_dir("mdw")

        # 4. 在 std (当前 master) 上重做 standby
        self.log.info("在 std 上执行 gpinitstandby -s mdw")
        result = gpinitstandby(standby_host="mdw", master_host="std")
        if not result.ok:
            raise RuntimeError(f"gpinitstandby 失败: {result.stderr}")
        self.log.info("standby 重建完成")

        # 5. 重启 mdw 和 std 上的 dbcc-agent
        self.log.info("重启 mdw 和 std 上的 dbcc-agent...")
        for host in ("mdw", "std"):
            result = run_on_host(host, "systemctl restart dbcc-agent")
            if not result.ok:
                self.log.warning(f"{host} dbcc-agent 重启失败: {result.stderr}")
            else:
                self.log.info(f"{host} dbcc-agent 已重启")

    def emergency_restore(self):
        try:
            ecs_start("mdw")
            wait_for_host("mdw", timeout=TIMEOUT_ECS_BOOT)
        except Exception:
            self.log.error("紧急恢复: mdw 开机失败，需手动处理")
