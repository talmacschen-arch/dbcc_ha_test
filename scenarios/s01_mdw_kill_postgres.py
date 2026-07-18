"""场景1: mdw pkill -9 postgres，进程自动原地拉起"""

import time
from scenarios.base import BaseScenario
from utils.ssh import run_on_host
from utils.gp_commands import can_connect_via_vip
from utils.health_check import wait_for_vip
from utils.log import Timer
from config import TIMEOUT_PROCESS_RECOVERY, POLL_INTERVAL


class Scenario(BaseScenario):
    name = "s01_mdw_kill_postgres"
    description = "mdw pkill -9 postgres，验证进程自动恢复"

    def inject_fault(self):
        self.log.info("在 mdw 上执行 pkill -9 postgres")
        run_on_host("mdw", "pkill -9 postgres", user="root", timeout=10)
        time.sleep(2)
        # 确认进程已被杀
        result = run_on_host("mdw", "pgrep -c postgres", user="root", timeout=5)
        if result.ok and int(result.stdout.strip() or "0") > 0:
            self.log.warning("仍有 postgres 进程存活，可能 kill 不彻底")
        else:
            self.log.info("postgres 进程已全部杀掉")

    def wait_and_validate(self):
        # 确认 DB 已不可用
        if can_connect_via_vip():
            self.log.warning("kill 后 VIP 仍可连接，可能 kill 不彻底")

        # 等待进程自动拉起 (2~5 分钟)
        self.log.info(f"等待 postgres 进程自动恢复 (最长 {TIMEOUT_PROCESS_RECOVERY}s)...")
        with Timer("process_recovery") as t:
            ok = wait_for_vip(timeout=TIMEOUT_PROCESS_RECOVERY, interval=POLL_INTERVAL)
        self.timers["process_recovery"] = t.elapsed

        if not ok:
            raise RuntimeError(f"postgres 进程未在 {TIMEOUT_PROCESS_RECOVERY}s 内自动恢复")
        self.log.info(f"postgres 已自动恢复，耗时 {t.elapsed:.0f}s")
