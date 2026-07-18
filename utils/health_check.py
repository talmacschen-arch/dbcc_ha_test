"""集群健康检查 & 等待恢复

集群数据面健康（coordinator/standby/segment）统一直连当前 master 判定（绕过 VIP），
VIP 是否回绑作为独立一项单独检查——这样 VIP 未回绑不会把健康的集群误判成整体宕机。
"""

import time
from config import VIP, POLL_INTERVAL, DBCC_STABILIZE_WAIT
from utils.gp_commands import (
    get_segment_config, get_segment_config_direct, can_connect_via_vip,
    gpstate_standby, psql_direct,
)
from utils.log import setup_logger

log = setup_logger()


def full_health_check():
    """全面检查集群健康状态，返回状态字典。

    数据面（coordinator/standby/segment）直连当前 master 判定；
    VIP 回绑单独作为一项（vip_reachable），与数据面健康解耦。
    """
    status = {
        "coordinator_up": False,
        "master_host": None,
        "standby_configured": False,
        "standby_synced": False,
        "total_segments": 0,
        "up_segments": 0,
        "down_segments": [],
        "all_synced": False,
        "role_balanced": False,
        "vip_reachable": can_connect_via_vip(),
    }

    segments, master_host = get_segment_config_direct()
    if segments is None:
        return status

    status["coordinator_up"] = True
    status["master_host"] = master_host

    # 分离 coordinator 和 data segments
    data_segs = [s for s in segments if s["content"] >= 0]
    coord_segs = [s for s in segments if s["content"] == -1]

    # standby 状态
    standby = [s for s in coord_segs if s["role"] == "m"]
    if standby:
        status["standby_configured"] = True
        status["standby_synced"] = standby[0]["mode"] == "s" and standby[0]["status"] == "u"

    # segment 状态
    status["total_segments"] = len(data_segs)
    up = [s for s in data_segs if s["status"] == "u"]
    down = [s for s in data_segs if s["status"] == "d"]
    status["up_segments"] = len(up)
    status["down_segments"] = [
        {"content": s["content"], "hostname": s["hostname"], "role": s["role"], "datadir": s["datadir"]}
        for s in down
    ]
    status["all_synced"] = all(s["mode"] == "s" for s in data_segs)
    status["role_balanced"] = all(s["role"] == s["preferred_role"] for s in data_segs)

    return status


def print_health(status):
    """打印健康状态摘要（数据面 与 VIP 分开呈现）"""
    if status["coordinator_up"]:
        coord = f"UP (master={status.get('master_host') or '?'})"
    else:
        coord = "DOWN (直连候选 master 均不可达)"
    log.info(f"  [数据面] Coordinator: {coord}")
    log.info(f"  [数据面] Standby: {'已配置' if status['standby_configured'] else '未配置'}"
             f"{'(已同步)' if status['standby_synced'] else '(未同步)' if status['standby_configured'] else ''}")
    log.info(f"  [数据面] Segments: {status['up_segments']}/{status['total_segments']} up"
             f"  全部同步: {status['all_synced']}  角色均衡: {status['role_balanced']}")
    if status["down_segments"]:
        for d in status["down_segments"]:
            log.info(f"    DOWN: content={d['content']} host={d['hostname']} role={d['role']}")
    log.info(f"  [VIP]    可达: {status['vip_reachable']} (独立项，与数据面健康解耦)")


def assert_healthy(phase="pre-check"):
    """断言集群健康。

    数据面（coordinator/standby/segment，直连 master 判定）与 VIP 回绑分开判定、分开归因：
    VIP 未回绑不会再被误报成 "Coordinator 不在线 / segment 0/0"。两类问题都会导致整体 FAIL，
    但错误信息各自独立，便于定位。
    """
    log.info(f"[{phase}] 检查集群健康状态...")
    status = full_health_check()
    print_health(status)

    data_errors = []
    if not status["coordinator_up"]:
        # 直连候选 master 均不可达才判 coordinator 不在线；此时 standby/segment 无从得知，不级联报错
        data_errors.append("Coordinator 不在线 (直连候选 master 均探活失败)")
    else:
        if not status["standby_synced"]:
            data_errors.append("Standby 未同步")
        if status["down_segments"]:
            data_errors.append(f"{len(status['down_segments'])} 个 segment down")
        if not status["all_synced"]:
            data_errors.append("存在未同步的 segment")
        if not status["role_balanced"]:
            data_errors.append("角色不均衡")

    vip_error = None
    if not status["vip_reachable"]:
        vip_error = "VIP 未回绑/不可达 (独立项；集群数据面本身状态见上)"

    if data_errors or vip_error:
        parts = []
        if data_errors:
            parts.append(f"数据面[{'; '.join(data_errors)}]")
        if vip_error:
            parts.append(f"VIP[{vip_error}]")
        raise RuntimeError(f"[{phase}] 检查未通过 -> " + " | ".join(parts))
    log.info(f"[{phase}] 集群健康 ✓ (数据面 + VIP 均正常)")


def get_current_master():
    """获取当前实际 master 的主机名（直连 coordinator 候选主机，绕过 VIP）"""
    segments, _ = get_segment_config_direct()
    if segments is None:
        return None
    for s in segments:
        if s["content"] == -1 and s["role"] == "p":
            return s["hostname"]
    return None


def check_master_direct(master_host=None):
    """直连当前 master 执行 SELECT 1 探活（绕过 VIP）

    Args:
        master_host: 指定 master 主机名/IP。为 None 时自动从集群查询当前 master。

    Returns:
        (bool, str): (是否存活, master 主机名)
    """
    if master_host is None:
        master_host = get_current_master()
        if master_host is None:
            log.warning("无法确定当前 master（VIP 不可达或查询失败）")
            return False, None

    log.info(f"直连 master ({master_host}) 执行 SELECT 1 探活...")
    result = psql_direct(master_host, "SELECT 1", flags="-t -A")
    alive = result.ok and "1" in result.stdout
    if alive:
        log.info(f"master ({master_host}) 探活成功")
    else:
        log.warning(f"master ({master_host}) 探活失败: {result.stderr}")
    return alive, master_host


def wait_for_master_direct(master_host, timeout, interval=None):
    """轮询等待指定 master 直连可用

    Args:
        master_host: master 主机名/IP（必须指定，不自动查询，因为故障期间 VIP 可能不可达）
        timeout: 超时秒数
        interval: 轮询间隔

    Returns:
        bool: 是否在超时前探活成功
    """
    interval = interval or POLL_INTERVAL
    log.info(f"等待 master ({master_host}) 直连可用 (超时 {timeout}s)...")
    start = time.time()
    while time.time() - start < timeout:
        result = psql_direct(master_host, "SELECT 1", flags="-t -A")
        if result.ok and "1" in result.stdout:
            elapsed = time.time() - start
            log.info(f"master ({master_host}) 直连可用 (耗时 {elapsed:.0f}s)")
            return True
        time.sleep(interval)
    log.warning(f"master ({master_host}) 直连等待超时 ({timeout}s)")
    return False


def wait_for_vip(timeout, interval=None):
    """等待 VIP 可连接（DBCC 完成切换）"""
    interval = interval or POLL_INTERVAL
    log.info(f"等待 VIP ({VIP}) 可连接 (超时 {timeout}s)...")
    start = time.time()
    while time.time() - start < timeout:
        if can_connect_via_vip():
            elapsed = time.time() - start
            log.info(f"VIP 已可连接 (耗时 {elapsed:.0f}s)")
            log.info(f"等待 {DBCC_STABILIZE_WAIT}s 让 DBCC 检测结果稳定...")
            time.sleep(DBCC_STABILIZE_WAIT)
            return True
        time.sleep(interval)
    log.warning(f"VIP 等待超时 ({timeout}s)")
    return False


def wait_for_segments_up(expected_up, timeout, interval=None):
    """等待指定数量的 segment 上线"""
    interval = interval or POLL_INTERVAL
    log.info(f"等待 segments up >= {expected_up} (超时 {timeout}s)...")
    start = time.time()
    while time.time() - start < timeout:
        status = full_health_check()
        if status["up_segments"] >= expected_up:
            log.info(f"Segments up: {status['up_segments']} (耗时 {time.time() - start:.0f}s)")
            return True
        log.debug(f"  当前 up: {status['up_segments']}/{status['total_segments']}")
        time.sleep(interval)
    log.warning(f"等待超时，当前 up: {status['up_segments']}")
    return False


def wait_for_full_resync(timeout, interval=None):
    """等待所有 segment 完全同步"""
    interval = interval or POLL_INTERVAL
    log.info(f"等待全部 segment 同步 (超时 {timeout}s)...")
    start = time.time()
    while time.time() - start < timeout:
        status = full_health_check()
        if status["all_synced"] and not status["down_segments"]:
            log.info(f"全部同步完成 (耗时 {time.time() - start:.0f}s)")
            return True
        log.debug(f"  synced={status['all_synced']} down={len(status['down_segments'])}")
        time.sleep(interval)
    log.warning(f"同步等待超时 ({timeout}s)")
    return False
