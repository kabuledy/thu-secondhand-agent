"""
清小闲 — 一键同步本地代码到阿里云 ECS

用法：
  python sync.py

将本地修改上传到服务器，自动重启服务。
依赖：pip install paramiko（已安装）
"""

import os, sys, time
from dotenv import load_dotenv

# 从 backend/.env 加载服务器配置
dotenv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend", ".env")
load_dotenv(dotenv_path)

HOST = os.environ.get("SERVER_HOST", "")
USER = "root"
PASS = os.environ.get("SERVER_PASS", "")
if not HOST or not PASS:
    print("❌ 请在 backend/.env 中设置 SERVER_HOST 和 SERVER_PASS")
    sys.exit(1)
PUBLIC_BASE = f"http://{HOST}/uploads"
LOCAL = os.path.dirname(os.path.abspath(__file__))
REMOTE = "/opt/thu-secondhand"

# 要同步的文件（排除 backend/data/ 下的交易数据）
FILES = [
    "backend/api/__init__.py",
    "backend/api/agent.py",
    "backend/api/analyze_image.py",
    "backend/api/bargain_data.py",
    "backend/api/conversation_store.py",
    "backend/api/database.py",
    "backend/api/embedding.py",
    "backend/api/image_utils.py",
    "backend/api/list_item.py",
    "backend/api/price_learning.py",
    "backend/api/search_item.py",
    "backend/api/storage_backend.py",
    "backend/api/tag_utils.py",
    "backend/api/web_search.py",
    "backend/main.py",
    "backend/requirements.txt",
    "prompt/system_prompt.md",
    "chat.html",
    "query_data.py",
    "generate_demo_data.py",
    "poster_multi_run.py",
    "poster_charts.py",
    "plot_charts.py",
    "plot_multi_charts.py",
    "reset_data.py",
]


def main():
    import paramiko

    print("=" * 50)
    print("  清小闲 — 同步到阿里云 ECS")
    print("=" * 50)

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print("\n连接服务器...", end=" ")
    ssh.connect(HOST, username=USER, password=PASS, timeout=10)
    print("OK")

    # 使用 SFTP 传输文件（比 echo base64 可靠）
    sftp = ssh.open_sftp()

    print("\n上传文件...")
    ok, fail = 0, 0
    for rel in FILES:
        local = os.path.join(LOCAL, rel)
        remote = f"{REMOTE}/{rel}"  # 远程路径必须用 /，不能用 os.path.join
        if not os.path.exists(local):
            continue

        # 确保远程目录存在
        remote_dir = f"{REMOTE}/{os.path.dirname(rel)}"

        # SFTP 上传
        try:
            sftp.put(local, remote)
            print(f"  ✅ {rel}")
            ok += 1
        except Exception as e:
            print(f"  ❌ {rel}: {str(e)[:60]}")
            fail += 1

    sftp.close()

    if ok == 0:
        print("\n❌ 没有文件上传成功")
        ssh.close()
        sys.exit(1)

    # 重启服务
    print("\n重启后端服务...")
    ssh.exec_command("systemctl restart thu-secondhand.service")
    time.sleep(3)

    # 验证
    print("验证运行状态...")
    stdin, stdout, stderr = ssh.exec_command("curl -s http://localhost:5000/")
    result = stdout.read().decode().strip()
    if '"status": "ok"' in result:
        print("  ✅ 服务运行正常")
    else:
        print(f"  ❌ {result[:100]}")

    # ── 设置服务器 PUBLIC_BASE ──
    # 确保服务器上的图片 URL 使用公网地址，而非 localhost
    print("\n配置服务器图片公网地址...")
    stdin, stdout, stderr = ssh.exec_command(
        "grep -q '^PUBLIC_BASE=' /opt/thu-secondhand/backend/.env && "
        f"sed -i 's|^PUBLIC_BASE=.*|{PUBLIC_BASE}|' "
        "/opt/thu-secondhand/backend/.env || "
        f"echo '{PUBLIC_BASE}' "
        ">> /opt/thu-secondhand/backend/.env"
    )
    print(f"  ✅ PUBLIC_BASE 已设为 {PUBLIC_BASE}")

    # 再次重启以加载新环境变量
    print("重新加载环境变量...")
    ssh.exec_command("systemctl restart thu-secondhand.service")
    time.sleep(2)

    ssh.close()
    print(f"\n✅ 同步完成！（{ok} 个文件）")
    print(f"   后端地址: http://{HOST}/")


if __name__ == "__main__":
    main()
