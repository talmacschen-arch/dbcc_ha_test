#!/usr/bin/env python3
"""DBCC HA 测试入口"""

import argparse
import sys
import os
import time

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.log import setup_logger

SCENARIOS = {
    "s01": ("scenarios.s01_mdw_kill_postgres",   "mdw pkill -9 postgres，进程自动恢复"),
    "s02": ("scenarios.s02_mdw_shutdown",         "mdw 关机，DBCC 激活 std + VIP 切换"),
    "s03": ("scenarios.s03_std_network_isolate",  "std 安全组隔离，DBCC 回切 mdw"),
    "s04": ("scenarios.s04_sdw1_kill_segment",    "sdw1 kill primary segment，修复"),
    "s05": ("scenarios.s05_sdw1_shutdown",        "sdw1 关机，修复"),
}


def load_scenario(name):
    """动态加载场景类"""
    module_path, _ = SCENARIOS[name]
    mod = __import__(module_path, fromlist=["Scenario"])
    return mod.Scenario()


def list_scenarios():
    print("可用场景:")
    for name, (_, desc) in SCENARIOS.items():
        print(f"  {name}  {desc}")


def main():
    parser = argparse.ArgumentParser(description="DBCC HA 测试")
    parser.add_argument("scenarios", nargs="*", help="要运行的场景 (s01-s05)，不指定则运行全部")
    parser.add_argument("--list", action="store_true", help="列出所有场景")
    args = parser.parse_args()

    if args.list:
        list_scenarios()
        return

    log = setup_logger()

    # 确定要运行的场景
    if args.scenarios:
        to_run = []
        for s in args.scenarios:
            if s not in SCENARIOS:
                print(f"错误: 未知场景 '{s}'，可选: {', '.join(SCENARIOS.keys())}")
                sys.exit(1)
            to_run.append(s)
    else:
        to_run = list(SCENARIOS.keys())
        log.info("未指定场景，将运行全部场景")

    # 逐个运行
    results = {}
    for idx, name in enumerate(to_run):
        # 测试之间等待 2 分钟，确保集群状态稳定
        if idx > 0:
            log.info("等待 120 秒，确保集群状态稳定后再运行下一个测试...")
            time.sleep(120)

        _, desc = SCENARIOS[name]
        log.info(f"\n{'#'*60}")
        log.info(f"# 开始: {name} - {desc}")
        log.info(f"{'#'*60}")

        scenario = load_scenario(name)
        result = scenario.run()
        results[name] = result

        if result == "FAIL":
            log.error(f"{name} 失败，终止后续测试")
            break

    # 汇总
    log.info(f"\n{'='*60}")
    log.info("测试汇总")
    log.info(f"{'='*60}")
    for name, result in results.items():
        _, desc = SCENARIOS[name]
        status = "PASS ✓" if result == "PASS" else "FAIL ✗"
        log.info(f"  {name}  {status:10s}  {desc}")

    total = len(results)
    passed = sum(1 for r in results.values() if r == "PASS")
    log.info(f"\n  总计: {passed}/{total} 通过")

    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
