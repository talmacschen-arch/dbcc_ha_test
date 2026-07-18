"""场景5: sdw1 关机，验证集群降级可用后修复"""

import time
from scenarios.base import BaseScenario
from utils.ecs_api import ecs_stop, ecs_start
from utils.ssh import wait_for_host, remove_localhost_from_hosts, run_on_host
from utils.gp_commands import (
    can_connect_via_vip, get_segment_config, check_segments_alive,
    gprecoverseg, gprecoverseg_rebalance, gpstate_e_check,
    set_fts_params_for_test, restore_fts_params,
)
from utils.health_check import wait_for_full_resync
from utils.log import Timer
from config import TIMEOUT_ECS_BOOT, TIMEOUT_SEGMENT_RECOVERY, TIMEOUT_RESYNC, POLL_INTERVAL, DBCC_STABILIZE_WAIT


class Scenario(BaseScenario):
    name = "s05_sdw1_shutdown"
    description = "sdw1 关机，验证集群降级可用后修复"

    def pre_check(self):
        self.log.info("设置 FTS 测试参数...")
        set_fts_params_for_test()
        super().pre_check()

    def inject_fault(self):
        self.log.info("关机 sdw1 ECS")
        ok = ecs_stop("sdw1")
        if not ok:
            raise RuntimeError("sdw1 ECS 关机失败")
        self.log.info("sdw1 已关机")

    def wait_and_validate(self):
        # 等待 FTS 检测并完成状态转变 (最多 10 分钟)
        self.log.info(f"等待 FTS 检测到 sdw1 故障并完成状态转变 (最长 {TIMEOUT_SEGMENT_RECOVERY}s)...")
        start = time.time()
        validated = False

        while time.time() - start < TIMEOUT_SEGMENT_RECOVERY:
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

            # 检查预期状态:
            #   - sdw1 上的 segment 应该 status='d'
            #   - sdw2 上的 primary 应该 status='u'
            #   - 有 down segment 的 pair 应该 mode='n' (not syncing)
            data_segs = [s for s in segments if s["content"] >= 0]
            sdw1_segs = [s for s in data_segs if "sdw1" in s["hostname"] or "synxdb-0003" in s["hostname"]]
            sdw1_all_down = all(s["status"] == "d" for s in sdw1_segs) if sdw1_segs else False
            active_primaries = [s for s in data_segs if s["role"] == "p" and s["status"] == "u"]
            all_primaries_up = len(active_primaries) == 8  # 8 个 content 的 primary 都应该 up

            if sdw1_all_down and all_primaries_up:
                elapsed = time.time() - start
                self.log.info(f"FTS 状态转变完成 (耗时 {elapsed:.0f}s): sdw1 全部 down, 所有 primary up")
                validated = True
                break

            time.sleep(POLL_INTERVAL)

        if not validated:
            raise RuntimeError(f"FTS 状态转变未在 {TIMEOUT_SEGMENT_RECOVERY}s 内完成")

        # 验证集群仍可工作: VIP 可连 + 分布式查询探活
        if not can_connect_via_vip():
            raise RuntimeError("sdw1 关机后集群 VIP 不可连接")
        self.log.info("VIP 连接正常")

        self.log.info(f"等待 {DBCC_STABILIZE_WAIT}s 让 DBCC 检测结果稳定...")
        time.sleep(DBCC_STABILIZE_WAIT)

        ok, count = check_segments_alive()
        if not ok:
            raise RuntimeError("segment 分布式探活失败")
        self.log.info(f"分布式探活成功，gp_dist_random 返回 {count} 行")

    def restore(self):
        # 1. 开机 sdw1
        self.log.info("开机 sdw1 ECS...")
        ok = ecs_start("sdw1")
        if not ok:
            raise RuntimeError("sdw1 ECS 开机失败")

        # 2. 等待 sdw1 SSH 可达
        if not wait_for_host("sdw1", timeout=TIMEOUT_ECS_BOOT):
            raise RuntimeError("sdw1 开机后 SSH 不可达")

        # 2.1 清理 /etc/hosts 中的 127.0.0.1 条目
        remove_localhost_from_hosts("sdw1")

        # 3. gprecoverseg
        time.sleep(10)  # 等 OS 完全就绪
        result = gprecoverseg()
        if not result.ok:
            raise RuntimeError(f"gprecoverseg 失败: {result.stderr}")

        # 4. 等待 resync
        with Timer("resync") as t:
            ok = wait_for_full_resync(timeout=TIMEOUT_RESYNC)
        self.timers["resync"] = t.elapsed
        if not ok:
            raise RuntimeError("segment resync 超时")

        # 5. rebalance
        result = gprecoverseg_rebalance()
        if not result.ok:
            raise RuntimeError(f"gprecoverseg -r 失败: {result.stderr}")

        # 6. 等待 rebalance 后 resync
        with Timer("rebalance_resync") as t:
            ok = wait_for_full_resync(timeout=TIMEOUT_RESYNC)
        self.timers["rebalance_resync"] = t.elapsed
        if not ok:
            raise RuntimeError("rebalance 后 resync 超时")

    def post_check(self):
        super().post_check()
        self.log.info("恢复 FTS 默认参数...")
        restore_fts_params()

        # 重启 PXF 集群
        self.log.info("重启 PXF 集群...")
        result = run_on_host("mdw", "su - gpadmin -c 'pxf cluster restart'", timeout=120)
        if not result.ok:
            self.log.warning(f"PXF 重启失败: {result.stderr}")
        else:
            self.log.info("PXF 集群已重启")

    def emergency_restore(self):
        try:
            ecs_start("sdw1")
            wait_for_host("sdw1", timeout=TIMEOUT_ECS_BOOT)
        except Exception:
            self.log.error("紧急恢复: sdw1 开机失败，需手动处理")
        try:
            gprecoverseg()
        except Exception:
            self.log.error("紧急恢复: gprecoverseg 失败，需手动处理")
        try:
            restore_fts_params()
        except Exception:
            self.log.error("紧急恢复: 恢复 FTS 参数失败，需手动处理")
        try:
            gpstate_e_check()
        except Exception:
            self.log.error("紧急恢复: gpstate -e 检查失败")
