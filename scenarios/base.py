"""场景基类 - 统一生命周期"""

from utils.log import setup_logger, Timer
from utils.health_check import assert_healthy, print_health, full_health_check


class BaseScenario:
    name = "base"
    description = "基类"

    def __init__(self):
        self.log = setup_logger(scenario=self.name)
        self.timers = {}

    def run(self):
        """执行完整测试流程，返回 PASS/FAIL"""
        self.log.info(f"{'='*60}")
        self.log.info(f"场景: {self.name} - {self.description}")
        self.log.info(f"{'='*60}")

        fault_injected = False
        try:
            # pre-check
            with Timer("pre_check") as t:
                self.pre_check()
            self.timers["pre_check"] = t.elapsed

            # inject fault
            with Timer("inject_fault") as t:
                self.inject_fault()
            self.timers["inject_fault"] = t.elapsed
            fault_injected = True

            # wait for DBCC + validate
            with Timer("wait_and_validate") as t:
                self.wait_and_validate()
            self.timers["wait_and_validate"] = t.elapsed

            # manual restore
            with Timer("restore") as t:
                self.restore()
            self.timers["restore"] = t.elapsed

            # post-check
            with Timer("post_check") as t:
                self.post_check()
            self.timers["post_check"] = t.elapsed

            self.log.info(f"\n结果: PASS")
            self._print_summary()
            return "PASS"

        except Exception as e:
            self.log.error(f"\n结果: FAIL - {e}")
            if fault_injected:
                self.log.info("尝试紧急恢复...")
                try:
                    self.emergency_restore()
                except Exception as er:
                    self.log.error(f"紧急恢复失败: {er}")
            else:
                self.log.info("故障注入未成功，跳过紧急恢复")
            self._print_summary()
            return "FAIL"

    def pre_check(self):
        """前置检查：集群完全健康"""
        assert_healthy("pre-check")

    def inject_fault(self):
        raise NotImplementedError

    def wait_and_validate(self):
        raise NotImplementedError

    def restore(self):
        """默认无需修复"""
        pass

    def post_check(self):
        """后置检查：集群完全健康"""
        assert_healthy("post-check")

    def emergency_restore(self):
        """紧急恢复，子类可覆盖"""
        pass

    def _print_summary(self):
        self.log.info(f"\n--- 耗时统计 ---")
        for phase, elapsed in self.timers.items():
            self.log.info(f"  {phase:25s} {elapsed:.1f}s")
        total = sum(self.timers.values())
        self.log.info(f"  {'合计':25s} {total:.1f}s")
