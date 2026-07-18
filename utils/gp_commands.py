"""Greenplum 命令封装"""

import subprocess
from config import VIP, GP_PORT, GP_USER, COORDINATOR_DATA_DIR, CLUSTER
from utils.ssh import run_gp_cmd, run_on_host, CmdResult
from utils.log import setup_logger

log = setup_logger()


def _local_psql(sql, dbname="postgres", flags="", timeout=30):
    """在测试运行机本地通过 VIP 执行 psql（PGPASSWORD 已在 .bashrc 中配置）"""
    cmd = f'psql -h {VIP} -p {GP_PORT} -U {GP_USER} -d {dbname} {flags} -c "{sql}"'
    log.debug(f"[local psql] {cmd}")
    try:
        r = subprocess.run(["bash", "-lc", cmd],
                           capture_output=True, text=True, timeout=timeout)
        return CmdResult(r.returncode, r.stdout, r.stderr)
    except subprocess.TimeoutExpired:
        return CmdResult(-1, "", f"psql 超时 ({timeout}s)")


_SEGMENT_CONFIG_SQL = (
    "SELECT content, role, preferred_role, mode, status, hostname, port, datadir "
    "FROM gp_segment_configuration ORDER BY content, role"
)


def _parse_segment_rows(stdout):
    """把 psql -t -A -F'|' 的输出解析成结构化 segment 列表"""
    segments = []
    for line in stdout.strip().splitlines():
        parts = line.strip().split("|")
        if len(parts) < 8:
            continue
        segments.append({
            "content": int(parts[0]),
            "role": parts[1],
            "preferred_role": parts[2],
            "mode": parts[3],
            "status": parts[4],
            "hostname": parts[5],
            "port": int(parts[6]),
            "datadir": parts[7],
        })
    return segments


def get_segment_config():
    """通过 VIP 查询 gp_segment_configuration，返回结构化列表"""
    result = _local_psql(_SEGMENT_CONFIG_SQL, flags="-t -A -F'|'")
    if not result.ok:
        log.warning(f"查询 gp_segment_configuration 失败: {result.stderr}")
        return None
    return _parse_segment_rows(result.stdout)


def get_segment_config_direct(hosts=None):
    """直连 coordinator 候选主机查询 gp_segment_configuration（绕过 VIP）。

    gp_segment_configuration 是 coordinator 上的全局 catalog，只有当前活跃 master
    接受连接（standby 处于 recovery，不接受普通连接），因此依次尝试候选主机，用第一个
    能连上的 coordinator 返回结果——连上的那个就是当前活跃 master。

    Args:
        hosts: 候选 coordinator 主机名/IP 列表，默认 ["mdw", "std"]。

    Returns:
        (segments, master_host)：成功时返回 (结构化列表, 应答的 master 主机名)；
        全部连不上时返回 (None, None)。
    """
    candidates = hosts or ["mdw", "std"]
    for host in candidates:
        result = psql_direct(host, _SEGMENT_CONFIG_SQL, flags="-t -A -F'|'")
        if result.ok:
            segments = _parse_segment_rows(result.stdout)
            if segments:
                return segments, host
        else:
            log.debug(f"直连 {host} 查询 gp_segment_configuration 失败: {result.stderr}")
    log.warning(f"直连候选 master {candidates} 均无法查询 gp_segment_configuration")
    return None, None


def gpstate(host="mdw"):
    """运行 gpstate，返回输出"""
    result = run_gp_cmd("gpstate", host=host, timeout=60)
    return result


def gpstate_standby(host="mdw"):
    """运行 gpstate -f，返回 standby 状态"""
    result = run_gp_cmd("gpstate -f", host=host, timeout=30)
    if not result.ok:
        return "error"
    output = result.stdout + result.stderr
    if "Standby host passive" in output:
        return "passive"
    if "No standby master configured" in output or "not configured" in output.lower():
        return "not_configured"
    return "unknown"


def can_connect_via_vip():
    """通过 VIP 测试 DB 是否可连接"""
    result = _local_psql("SELECT 1", flags="-t -A")
    return result.ok and "1" in result.stdout


def psql_direct(host, sql, dbname="postgres", flags="", timeout=10):
    """直连指定 host 执行 psql（绕过 VIP）"""
    ip = host if host not in CLUSTER else CLUSTER[host]["ip"]
    cmd = f'psql -h {ip} -p {GP_PORT} -U {GP_USER} -d {dbname} {flags} -c "{sql}"'
    log.debug(f"[direct psql -> {host}({ip})] {cmd}")
    try:
        r = subprocess.run(["bash", "-lc", cmd],
                           capture_output=True, text=True, timeout=timeout)
        return CmdResult(r.returncode, r.stdout, r.stderr)
    except subprocess.TimeoutExpired:
        return CmdResult(-1, "", f"psql 直连超时 ({timeout}s)")


def check_segments_alive():
    """通过 VIP + 分布式查询探活，验证所有 segment 可达"""
    sql = "SELECT count(1) from gp_dist_random('gp_id')"
    result = _local_psql(sql, flags="-a -q")
    if not result.ok:
        log.warning(f"segment 探活失败: {result.stderr}")
        return False, None
    for line in result.stdout.strip().splitlines():
        line = line.strip()
        if line.isdigit():
            count = int(line)
            log.info(f"segment 探活成功，gp_dist_random 返回 {count} 行")
            return True, count
    log.warning(f"segment 探活: 无法解析结果\n{result.stdout}")
    return False, None


def run_query(sql, dbname="postgres"):
    """通过 VIP 执行 SQL 查询，返回结果字符串"""
    result = _local_psql(sql, dbname=dbname, flags="-t -A")
    if result.ok:
        return result.stdout.strip()
    log.warning(f"SQL 执行失败: {result.stderr}")
    return None


def gpstart(host="mdw"):
    """启动集群"""
    log.info("执行 gpstart -a")
    result = run_gp_cmd("gpstart -a", host=host, timeout=120)
    log.info(f"gpstart 结果: rc={result.rc}")
    return result


def gpstop(host="mdw", mode="fast"):
    """停止集群"""
    log.info(f"执行 gpstop -a -M {mode}")
    result = run_gp_cmd(f"gpstop -a -M {mode}", host=host, timeout=120)
    return result


def gprecoverseg(host="mdw", full=False, timeout=None):
    """恢复 segment"""
    if timeout is None:
        from config import TIMEOUT_GPRECOVERSEG
        timeout = TIMEOUT_GPRECOVERSEG
    flag = "-aF" if full else "-a"
    log.info(f"执行 gprecoverseg {flag}")
    result = run_gp_cmd(f"gprecoverseg {flag}", host=host, timeout=timeout)
    log.info(f"gprecoverseg 结果: rc={result.rc}")
    return result


def gprecoverseg_rebalance(host="mdw", timeout=None):
    """rebalance segment 到 preferred role"""
    if timeout is None:
        from config import TIMEOUT_GPRECOVERSEG
        timeout = TIMEOUT_GPRECOVERSEG
    log.info("执行 gprecoverseg -ra")
    result = run_gp_cmd("gprecoverseg -ra", host=host, timeout=timeout)
    log.info(f"gprecoverseg -r 结果: rc={result.rc}")
    if result.ok:
        gpstate_e_check(host=host)
    return result


def gpstate_e_check(host="mdw"):
    """执行 gpstate -e 复核 segment 角色是否已回到 preferred role"""
    log.info("执行 gpstate -e 复核 rebalance 结果...")
    result = run_gp_cmd("gpstate -e", host=host, timeout=60)
    if not result.ok:
        log.warning(f"gpstate -e 执行失败: {result.stderr}")
        return
    output = result.stdout + result.stderr
    log.info(f"gpstate -e 输出:\n{output}")
    if "Segments not running in their preferred role" in output:
        log.warning("存在 segment 未运行在 preferred role 上，rebalance 可能未完全生效")
    else:
        log.info("gpstate -e 确认: 所有 segment 已运行在 preferred role 上")


def gpinitstandby(standby_host, master_host="mdw"):
    """在 master 上初始化 standby"""
    log.info(f"执行 gpinitstandby -s {standby_host} (在 {master_host} 上)")
    result = run_gp_cmd(f"gpinitstandby -a -s {standby_host}", host=master_host, timeout=300)
    log.info(f"gpinitstandby 结果: rc={result.rc}")
    return result


def cleanup_coordinator_dir(host):
    """清理指定 host 上的 coordinator 数据目录"""
    log.info(f"清理 {host}:{COORDINATOR_DATA_DIR}")
    result = run_on_host(host, f"su - gpadmin -c 'rm -rf {COORDINATOR_DATA_DIR}'", user="root", timeout=30)
    if result.ok:
        log.info(f"{host} coordinator 目录已清理")
    else:
        log.warning(f"清理失败: {result.stderr}")
    return result


def gpconfig(param, value, master_value=None, host="mdw"):
    """设置 GP 配置参数"""
    if master_value is not None:
        cmd = f"gpconfig -c {param} -v {value} -m {master_value}"
    else:
        cmd = f"gpconfig -c {param} -v {value}"
    log.info(f"执行 {cmd}")
    result = run_gp_cmd(cmd, host=host, timeout=30)
    if not result.ok:
        log.warning(f"gpconfig 失败: {result.stderr}")
    return result


def gpstop_reload(host="mdw"):
    """gpstop -u 重新加载配置"""
    log.info("执行 gpstop -u (reload 配置)")
    result = run_gp_cmd("gpstop -u", host=host, timeout=60)
    if result.ok:
        log.info("配置已重新加载")
    else:
        log.warning(f"gpstop -u 失败: {result.stderr}")
    return result


def set_fts_params_for_test(host="mdw"):
    """测试前设置 FTS 参数（缩短探测间隔加速故障检测）"""
    log.info("设置 FTS 测试参数...")
    gpconfig("gp_fts_probe_interval", 60, host=host)
    gpconfig("gp_fts_probe_timeout", 60, host=host)
    gpconfig("gp_segment_connect_timeout", 60, master_value=60, host=host)
    gpstop_reload(host=host)
    log.info("FTS 测试参数已生效")


def restore_fts_params(host="mdw"):
    """测试后恢复 FTS 参数"""
    log.info("恢复 FTS 默认参数...")
    gpconfig("gp_fts_probe_interval", 300, host=host)
    gpconfig("gp_fts_probe_timeout", 300, host=host)
    gpconfig("gp_segment_connect_timeout", 1800, master_value=1800, host=host)
    gpstop_reload(host=host)
    log.info("FTS 参数已恢复")
