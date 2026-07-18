"""封装 hw_ecs_manage.py 供场景脚本调用"""

import sys
import os

# 将项目根目录加入 path 以便导入 hw_ecs_manage
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hw_ecs_manage import (
    ecs_client, vpc_client, SERVERS, SECURITY_GROUPS,
    BatchStopServersRequest, BatchStopServersOption, BatchStopServersRequestBody,
    BatchStartServersRequest, BatchStartServersOption, BatchStartServersRequestBody,
    ServerId, ShowServerRequest,
    ListPortsRequest, UpdatePortRequest, UpdatePortRequestBody, UpdatePortOption,
)
from config import SG_NORMAL, SG_DENY, TIMEOUT_ECS_BOOT, POLL_INTERVAL
from utils.log import setup_logger
import time

log = setup_logger()


def _get_server_id(name):
    if name in SERVERS:
        return SERVERS[name]
    raise ValueError(f"未知服务器: {name}")


def _get_port_id(server_id):
    """通过 VPC API 查询 ECS 实例的网卡 port_id"""
    req = ListPortsRequest()
    req.device_id = server_id
    resp = vpc_client.list_ports(req)
    if not resp.ports:
        raise RuntimeError(f"未找到 server {server_id} 的网卡端口")
    return resp.ports[0].id


def _wait_ecs_status(server_id, target_status, timeout=TIMEOUT_ECS_BOOT):
    """等待 ECS 达到目标状态"""
    start = time.time()
    while time.time() - start < timeout:
        req = ShowServerRequest()
        req.server_id = server_id
        resp = ecs_client.show_server(req)
        if resp.server.status == target_status:
            return True
        time.sleep(POLL_INTERVAL)
    return False


def ecs_stop(name):
    """关机 ECS"""
    sid = _get_server_id(name)
    log.info(f"ECS 关机: {name} ({sid})")
    req = BatchStopServersRequest()
    req.body = BatchStopServersRequestBody(
        os_stop=BatchStopServersOption(
            servers=[ServerId(id=sid)], type="SOFT"
        )
    )
    resp = ecs_client.batch_stop_servers(req)
    log.info(f"关机请求已发送 (job_id: {resp.job_id})")
    ok = _wait_ecs_status(sid, "SHUTOFF")
    if ok:
        log.info(f"{name} 已关机")
    else:
        log.warning(f"{name} 关机等待超时")
    return ok


def ecs_start(name):
    """开机 ECS"""
    sid = _get_server_id(name)
    log.info(f"ECS 开机: {name} ({sid})")
    req = BatchStartServersRequest()
    req.body = BatchStartServersRequestBody(
        os_start=BatchStartServersOption(
            servers=[ServerId(id=sid)]
        )
    )
    resp = ecs_client.batch_start_servers(req)
    log.info(f"开机请求已发送 (job_id: {resp.job_id})")
    ok = _wait_ecs_status(sid, "ACTIVE")
    if ok:
        log.info(f"{name} 已开机")
    else:
        log.warning(f"{name} 开机等待超时")
    return ok


def ecs_isolate(name):
    """切换安全组到 deny-all（网络隔离）"""
    sid = _get_server_id(name)
    sg_id = SECURITY_GROUPS[SG_DENY]
    log.info(f"网络隔离: {name} 安全组 -> {SG_DENY}")
    _set_port_sg(sid, [sg_id])


def ecs_restore_network(name):
    """恢复安全组（解除隔离）"""
    sid = _get_server_id(name)
    sg_id = SECURITY_GROUPS[SG_NORMAL]
    log.info(f"恢复网络: {name} 安全组 -> {SG_NORMAL}")
    _set_port_sg(sid, [sg_id])


def _set_port_sg(server_id, sg_ids):
    """通过 VPC update_port 直接设置端口安全组列表"""
    port_id = _get_port_id(server_id)
    req = UpdatePortRequest()
    req.port_id = port_id
    req.body = UpdatePortRequestBody(
        port=UpdatePortOption(security_groups=sg_ids)
    )
    vpc_client.update_port(req)
    log.info(f"安全组切换完成")


def ecs_show_sg(name):
    """查看 ECS 绑定的安全组"""
    sid = _get_server_id(name)
    req = ShowServerRequest()
    req.server_id = sid
    resp = ecs_client.show_server(req)
    sgs = [sg.name for sg in resp.server.security_groups] if resp.server.security_groups else []
    return sgs
