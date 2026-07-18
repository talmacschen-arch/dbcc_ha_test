#!/usr/bin/env python3
"""华为云 ECS 管理脚本 - 开关机 & 安全组切换"""

import os
import sys
import time
from huaweicloudsdkcore.auth.credentials import BasicCredentials
from huaweicloudsdkecs.v2 import (
    EcsClient, ShowServerRequest,
    BatchStartServersRequest, BatchStartServersOption,
    BatchStartServersRequestBody, ServerId,
    BatchStopServersRequest, BatchStopServersOption,
    BatchStopServersRequestBody,
)
from huaweicloudsdkvpc.v2 import (
    VpcClient, ListPortsRequest,
    UpdatePortRequest, UpdatePortRequestBody, UpdatePortOption,
)

# ========== 配置 ==========
def _load_dotenv():
    """若同目录存在 .env，把其中 KEY=VALUE / export KEY=VALUE 载入环境（不覆盖已存在的）。

    仅做最小解析，避免引入 python-dotenv 依赖；真实环境变量优先于 .env。
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

# 华为云凭据从环境变量读取，不硬编码进代码。运行前需设置（见 .env.example），
# 或在同目录放一份 .env（已被 .gitignore 排除，不会进仓库）：
#   export HW_ECS_AK=...  HW_ECS_SK=...  HW_ECS_PROJECT_ID=...
AK = os.environ.get("HW_ECS_AK")
SK = os.environ.get("HW_ECS_SK")
PROJECT_ID = os.environ.get("HW_ECS_PROJECT_ID")
if not all([AK, SK, PROJECT_ID]):
    raise SystemExit(
        "缺少华为云凭据环境变量：请先设置 HW_ECS_AK / HW_ECS_SK / HW_ECS_PROJECT_ID（参考 .env.example）"
    )
ENDPOINT_ECS = os.environ.get("HW_ECS_ENDPOINT", "https://ecs.cn-north-4.myhuaweicloud.com")
ENDPOINT_VPC = os.environ.get("HW_VPC_ENDPOINT", "https://vpc.cn-north-4.myhuaweicloud.com")

# ECS 实例
SERVERS = {
    "mdw":  "41b83973-b91e-4369-b41a-e757ef055900",  # synxdb-0001 192.168.195.236
    "std":  "5f450779-b9d0-4c72-9ccf-5056b9dd2483",  # synxdb-0002 192.168.199.179
    "sdw1": "1bc1ded3-f5da-4100-baaf-02fdf335e40a",  # synxdb-0003 192.168.194.60
    "sdw2": "2a06599b-83f5-4719-bc94-dbf973cf1e61",  # synxdb-0004 192.168.193.23
}

# 安全组 (ID)
SECURITY_GROUPS = {
    "sg-chenqiang":     "c4ad736b-4359-42eb-97dc-6373e469355d",
    "sg-deny-all-test": "89bd06de-5a93-47f3-a9b6-38e55fbb838f",
}

# ========== 初始化客户端 ==========
credentials = BasicCredentials(AK, SK, PROJECT_ID)
ecs_client = EcsClient.new_builder() \
    .with_credentials(credentials) \
    .with_endpoint(ENDPOINT_ECS) \
    .build()
vpc_client = VpcClient.new_builder() \
    .with_credentials(credentials) \
    .with_endpoint(ENDPOINT_VPC) \
    .build()


def resolve_server_ids(names):
    """将服务器名称列表解析为 ID 列表，支持 'all' 表示全部"""
    if names == ["all"]:
        return list(SERVERS.values())
    ids = []
    for name in names:
        if name in SERVERS:
            ids.append(SERVERS[name])
        elif name in SERVERS.values():
            ids.append(name)
        else:
            print(f"错误: 未知服务器 '{name}'，可选: {', '.join(SERVERS.keys())}")
            sys.exit(1)
    return ids


def _get_port_id(server_id):
    """通过 VPC API 查询 ECS 实例的网卡 port_id"""
    req = ListPortsRequest()
    req.device_id = server_id
    resp = vpc_client.list_ports(req)
    if not resp.ports:
        raise RuntimeError(f"未找到 server {server_id} 的网卡端口")
    return resp.ports[0].id


def show_status(server_ids):
    """查看 ECS 状态"""
    for sid in server_ids:
        name = next((k for k, v in SERVERS.items() if v == sid), sid)
        req = ShowServerRequest()
        req.server_id = sid
        resp = ecs_client.show_server(req)
        s = resp.server
        ips = []
        if s.addresses:
            for net, addrs in s.addresses.items():
                for a in addrs:
                    ips.append(a.addr)
        sgs = [sg.name for sg in s.security_groups] if s.security_groups else []
        print(f"  {name:5s}  状态: {s.status:10s}  IP: {', '.join(ips):18s}  安全组: {', '.join(sgs)}")


def show_security_groups(server_ids):
    """查看 ECS 绑定的安全组"""
    for sid in server_ids:
        name = next((k for k, v in SERVERS.items() if v == sid), sid)
        req = ShowServerRequest()
        req.server_id = sid
        resp = ecs_client.show_server(req)
        s = resp.server
        sgs = [sg.name for sg in s.security_groups] if s.security_groups else []
        print(f"  {name:5s}  安全组: {', '.join(sgs) if sgs else '(无)'}")


def stop_servers(server_ids):
    """关机"""
    req = BatchStopServersRequest()
    req.body = BatchStopServersRequestBody(
        os_stop=BatchStopServersOption(
            servers=[ServerId(id=sid) for sid in server_ids],
            type="SOFT"
        )
    )
    resp = ecs_client.batch_stop_servers(req)
    print(f"关机请求已发送 (job_id: {resp.job_id})")
    return resp.job_id


def start_servers(server_ids):
    """开机"""
    req = BatchStartServersRequest()
    req.body = BatchStartServersRequestBody(
        os_start=BatchStartServersOption(
            servers=[ServerId(id=sid) for sid in server_ids]
        )
    )
    resp = ecs_client.batch_start_servers(req)
    print(f"开机请求已发送 (job_id: {resp.job_id})")
    return resp.job_id


def change_security_group(server_ids, old_sg_name, new_sg_name):
    """通过 VPC update_port 切换安全组（直接设置目标安全组列表）"""
    new_sg_id = SECURITY_GROUPS[new_sg_name]
    for sid in server_ids:
        name = next((k for k, v in SERVERS.items() if v == sid), sid)
        port_id = _get_port_id(sid)
        req = UpdatePortRequest()
        req.port_id = port_id
        req.body = UpdatePortRequestBody(
            port=UpdatePortOption(security_groups=[new_sg_id])
        )
        vpc_client.update_port(req)
        print(f"  {name}: 安全组已切换为 {new_sg_name}")


def wait_for_status(server_ids, target_status, timeout=120):
    """等待 ECS 达到目标状态"""
    print(f"等待状态变为 {target_status}...", end="", flush=True)
    start = time.time()
    while time.time() - start < timeout:
        all_ready = True
        for sid in server_ids:
            req = ShowServerRequest()
            req.server_id = sid
            resp = ecs_client.show_server(req)
            if resp.server.status != target_status:
                all_ready = False
                break
        if all_ready:
            print(" 完成")
            return True
        print(".", end="", flush=True)
        time.sleep(5)
    print(" 超时")
    return False


def usage():
    print("""用法: python3 hw_ecs_manage.py <命令> <服务器...>

命令:
  status  <服务器...>              查看状态
  show-sg <服务器...>              查看安全组
  stop    <服务器...>              关机
  start   <服务器...>              开机
  switch-sg <旧安全组> <新安全组> <服务器...>  切换安全组

服务器: mdw, std, sdw1, sdw2, all (全部)

示例:
  python3 hw_ecs_manage.py status all
  python3 hw_ecs_manage.py show-sg mdw              # 查看单台安全组
  python3 hw_ecs_manage.py show-sg all              # 查看全部4台安全组
  python3 hw_ecs_manage.py stop mdw std sdw1 sdw2
  python3 hw_ecs_manage.py start all
  python3 hw_ecs_manage.py switch-sg sg-chenqiang sg-deny-all-test all
  python3 hw_ecs_manage.py switch-sg sg-deny-all-test sg-chenqiang mdw std
""")
    sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        usage()

    cmd = sys.argv[1]

    if cmd == "status":
        server_ids = resolve_server_ids(sys.argv[2:])
        show_status(server_ids)

    elif cmd == "show-sg":
        server_ids = resolve_server_ids(sys.argv[2:])
        show_security_groups(server_ids)

    elif cmd == "stop":
        server_ids = resolve_server_ids(sys.argv[2:])
        print("即将关机:")
        show_status(server_ids)
        stop_servers(server_ids)
        wait_for_status(server_ids, "SHUTOFF")
        show_status(server_ids)

    elif cmd == "start":
        server_ids = resolve_server_ids(sys.argv[2:])
        print("即将开机:")
        show_status(server_ids)
        start_servers(server_ids)
        wait_for_status(server_ids, "ACTIVE")
        show_status(server_ids)

    elif cmd == "switch-sg":
        if len(sys.argv) < 5:
            print("错误: switch-sg 需要指定 <旧安全组> <新安全组> <服务器...>")
            usage()
        old_sg = sys.argv[2]
        new_sg = sys.argv[3]
        if old_sg not in SECURITY_GROUPS or new_sg not in SECURITY_GROUPS:
            print(f"错误: 安全组名称不对，可选: {', '.join(SECURITY_GROUPS.keys())}")
            sys.exit(1)
        server_ids = resolve_server_ids(sys.argv[4:])
        print(f"切换安全组: {old_sg} -> {new_sg}")
        change_security_group(server_ids, old_sg, new_sg)
        print("\n切换后状态:")
        show_status(server_ids)

    else:
        print(f"未知命令: {cmd}")
        usage()
