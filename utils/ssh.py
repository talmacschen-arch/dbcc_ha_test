"""SSH 远程执行 & GP 命令封装 — 测试运行机通过 SSH 操作集群节点"""

import subprocess
import time
from dataclasses import dataclass
from config import CLUSTER, GP_USER, POLL_INTERVAL
from utils.log import setup_logger

log = setup_logger()


@dataclass
class CmdResult:
    rc: int
    stdout: str
    stderr: str

    @property
    def ok(self):
        return self.rc == 0


def _resolve_host(host):
    """将 host 名 (mdw/std/sdw1/sdw2) 解析为 IP"""
    if host in CLUSTER:
        return CLUSTER[host]["ip"]
    return host


def run_on_host(host, cmd, user="root", timeout=30):
    """通过 SSH 在指定 host 上执行命令"""
    ip = _resolve_host(host)
    ssh_cmd = [
        "ssh", "-o", "ConnectTimeout=5",
        "-o", "StrictHostKeyChecking=no",
        "-o", "BatchMode=yes",
        f"{user}@{ip}", cmd
    ]
    log.debug(f"[ssh {user}@{ip}] {cmd}")
    try:
        r = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=timeout)
        result = CmdResult(r.returncode, r.stdout, r.stderr)
    except subprocess.TimeoutExpired:
        result = CmdResult(-1, "", f"SSH 超时 ({timeout}s)")

    if not result.ok:
        log.debug(f"  rc={result.rc} stderr={result.stderr.strip()}")
    return result


def run_gp_cmd(cmd, host="mdw", timeout=120):
    """以 gpadmin 身份执行 GP 命令：SSH root 到节点，再 su - gpadmin 执行"""
    wrapped = f"su - {GP_USER} -c {_shell_quote(cmd)}"
    return run_on_host(host, wrapped, user="root", timeout=timeout)


def is_host_reachable(host, timeout=5):
    """检测 host 是否 SSH 可达"""
    ip = _resolve_host(host)
    try:
        r = subprocess.run(
            ["ssh", "-o", f"ConnectTimeout={timeout}",
             "-o", "StrictHostKeyChecking=no",
             "-o", "BatchMode=yes",
             f"root@{ip}", "true"],
            capture_output=True, timeout=timeout + 5
        )
        return r.returncode == 0
    except subprocess.TimeoutExpired:
        return False


def wait_for_host(host, timeout=180, interval=None):
    """等待 host SSH 可达"""
    interval = interval or POLL_INTERVAL
    log.info(f"等待 {host} SSH 可达 (超时 {timeout}s)...")
    start = time.time()
    while time.time() - start < timeout:
        if is_host_reachable(host):
            log.info(f"{host} 已可达 (耗时 {time.time() - start:.0f}s)")
            return True
        time.sleep(interval)
    log.warning(f"{host} 等待超时 ({timeout}s)")
    return False


def wait_for_host_unreachable(host, timeout=120, interval=None):
    """等待 host SSH 不可达（用于确认关机完成）"""
    interval = interval or POLL_INTERVAL
    log.info(f"等待 {host} 不可达 (超时 {timeout}s)...")
    start = time.time()
    while time.time() - start < timeout:
        if not is_host_reachable(host):
            log.info(f"{host} 已不可达 (耗时 {time.time() - start:.0f}s)")
            return True
        time.sleep(interval)
    log.warning(f"{host} 仍然可达，等待超时 ({timeout}s)")
    return False


def remove_localhost_from_hosts(host):
    """删除远程主机 /etc/hosts 中的 127.0.0.1 条目"""
    log.info(f"清理 {host} 的 /etc/hosts 中 127.0.0.1 条目...")
    result = run_on_host(host, "sed -i '/^127\\.0\\.0\\.1/d' /etc/hosts", user="root", timeout=10)
    if result.ok:
        log.info(f"{host} /etc/hosts 127.0.0.1 条目已清理")
    else:
        log.warning(f"{host} 清理 /etc/hosts 失败: {result.stderr}")
    return result


def _shell_quote(s):
    """简单 shell 引用"""
    return "'" + s.replace("'", "'\\''") + "'"
