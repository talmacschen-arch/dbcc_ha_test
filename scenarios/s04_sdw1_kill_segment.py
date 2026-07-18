"""场景4: sdw1 kill -9 primary segment (gpseg0)，验证集群可用后修复"""

import time
from scenarios.base import BaseScenario
from utils.ssh import run_on_host
from utils.gp_commands import (
    can_connect_via_vip, run_query, gprecoverseg, gprecoverseg_rebalance,
    get_segment_config, set_fts_params_for_test, gpstate_e_check,
)
from utils.health_check import wait_for_full_resync
from utils.log import Timer
from config import TARGET_SEGMENT_DATADIR, TIMEOUT_SEGMENT_RECOVERY, TIMEOUT_RESYNC, POLL_INTERVAL, DBCC_STABILIZE_WAIT


class Scenario(BaseScenario):
    name = "s04_sdw1_kill_segment"
    description = "sdw1 kill -9 primary segment gpseg0，验证集群可用后修复"

    def pre_check(self):
        self.log.info("设置 FTS 测试参数...")
        set_fts_params_for_test()
        super().pre_check()

    def inject_fault(self):
        # 找到 gpseg0 的 postmaster 进程
        result = run_on_host(
            "sdw1",
            f"pgrep -f 'postgres -D {TARGET_SEGMENT_DATADIR}'",
            user="root", timeout=10
        )
        if not result.ok or not result.stdout.strip():
            raise RuntimeError(f"未找到 gpseg0 进程: {result.stderr}")

        pid = result.stdout.strip().splitlines()[0]
        self.log.info(f"在 sdw1 上 kill -9 gpseg0 postmaster (PID {pid})")
        run_on_host("sdw1", f"kill -9 {pid}", user="root", timeout=10)
        time.sleep(5)
        self.log.info("gpseg0 进程已杀掉")

    def wait_and_validate(self):
        # 等待 FTS 检测到 gpseg0 故障并完成状态转变
        timeout = TIMEOUT_SEGMENT_RECOVERY
        self.log.info(f"等待 FTS 检测到 segment 故障并完成状态转变 (最长 {timeout}s)...")
        start = time.time()
        validated = False

        while time.time() - start < timeout:
            segments = get_segment_config()
            if segments is None:
                self.log.warning("无法查询 segment 配置，等待重试...")
                time.sleep(POLL_INTERVAL)
                continue

            # 打印当前状态
            self.log.info("当前 gp_segment_configuration:")
            for seg in segments:
                if seg["content"] < 0:
                    continue
                self.log.info(
                    f"  content={seg['content']:2d}  role={seg['role']}  "
                    f"preferred={seg['preferred_role']}  mode={seg['mode']}  "
                    f"status={seg['status']}  host={seg['hostname']}"
                )

            # 检查预期状态: gpseg0 的原 primary 应该 status='d'，mirror 已提升为 primary
            data_segs = [s for s in segments if s["content"] == 0]
            down_seg = [s for s in data_segs if s["status"] == "d"]
            active_primary = [s for s in data_segs if s["role"] == "p" and s["status"] == "u"]

            if down_seg and active_primary:
                elapsed = time.time() - start
                self.log.info(f"FTS 状态转变完成 (耗时 {elapsed:.0f}s): gpseg0 原 primary down, mirror 已提升")
                validated = True
                break

            time.sleep(POLL_INTERVAL)

        if not validated:
            raise RuntimeError(f"FTS 状态转变未在 {timeout}s 内完成")

        # 验证集群仍可工作 (mirror 已提升)
        if not can_connect_via_vip():
            raise RuntimeError("segment 故障后集群不可连接")
        self.log.info("集群仍可通过 VIP 连接")

        self.log.info(f"等待 {DBCC_STABILIZE_WAIT}s 让 DBCC 检测结果稳定...")
        time.sleep(DBCC_STABILIZE_WAIT)

    def restore(self):
        # 1. gprecoverseg -a
        result = gprecoverseg()
        if not result.ok:
            self.log.warning("增量恢复失败，尝试全量恢复...")
            result = gprecoverseg(full=True)
            if not result.ok:
                raise RuntimeError(f"gprecoverseg 失败: {result.stderr}")

        # 2. 等待 resync 完成
        with Timer("resync") as t:
            ok = wait_for_full_resync(timeout=TIMEOUT_RESYNC)
        self.timers["resync"] = t.elapsed
        if not ok:
            raise RuntimeError("segment resync 超时")

        # 3. rebalance
        result = gprecoverseg_rebalance()
        if not result.ok:
            raise RuntimeError(f"gprecoverseg -r 失败: {result.stderr}")

        # 4. 等待 rebalance 后 resync
        with Timer("rebalance_resync") as t:
            ok = wait_for_full_resync(timeout=TIMEOUT_RESYNC)
        self.timers["rebalance_resync"] = t.elapsed
        if not ok:
            raise RuntimeError("rebalance 后 resync 超时")

    def emergency_restore(self):
        try:
            gprecoverseg(full=True)
        except Exception:
            self.log.error("紧急恢复: gprecoverseg -aF 失败，需手动处理")
        try:
            gpstate_e_check()
        except Exception:
            self.log.error("紧急恢复: gpstate -e 检查失败")
