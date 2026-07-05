"""
清小闲 — 同步数据到阿里云 ECS

将本地数据库和图片上传到服务器，使云端与本地一致。
注意：会覆盖服务器上的数据！

用法：
  python sync_data.py

使用场景：
  - 本地做了大量测试/修改后，想让服务器数据与本地一致
  - 新上架了一些商品，需要同步到服务器
"""
import os
import sys
import time
from dotenv import load_dotenv

LOCAL = os.path.dirname(os.path.abspath(__file__))

# 从 backend/.env 加载服务器密码
dotenv_path = os.path.join(LOCAL, "backend", ".env")
load_dotenv(dotenv_path)

# ── 服务器配置 ──
HOST = "39.106.1.145"
USER = "root"
PASS = os.environ.get("SERVER_PASS", "")
if not PASS:
    print("❌ 未找到 SERVER_PASS，请在 backend/.env 中设置")
    sys.exit(1)
REMOTE_BASE = "/opt/thu-secondhand"

LOCAL_DB = os.path.join(LOCAL, "backend", "data", "items.db")
LOCAL_UPLOADS = os.path.join(LOCAL, "backend", "data", "uploads")
REMOTE_DB = f"{REMOTE_BASE}/backend/data/items.db"
REMOTE_UPLOADS = f"{REMOTE_BASE}/backend/data/uploads"


def confirm() -> bool:
    print("=" * 60)
    print("  清小闲 — 数据同步到阿里云 ECS")
    print("=" * 60)

    # 统计要同步的内容
    db_size = os.path.getsize(LOCAL_DB) if os.path.exists(LOCAL_DB) else 0
    upload_files = []
    if os.path.exists(LOCAL_UPLOADS):
        upload_files = [f for f in os.listdir(LOCAL_UPLOADS) if os.path.isfile(os.path.join(LOCAL_UPLOADS, f))]

    print(f"\n  将要同步到服务器：")
    print(f"    📦 数据库: {db_size/1024:.1f} KB ({os.path.basename(LOCAL_DB)})")
    print(f"    🖼️  图片:  {len(upload_files)} 个文件")
    print(f"\n  目标服务器: {USER}@{HOST}")
    print(f"  ⚠️  服务器上现有数据将被覆盖！")

    ans = input("\n确认同步？输入 YES 继续: ").strip()
    return ans == "YES"


def sync_to_server():
    """将本地数据推送到服务器"""
    import paramiko

    print("\n🔌 连接服务器...", end=" ")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username=USER, password=PASS, timeout=10, look_for_keys=False, allow_agent=False)
    print("OK")

    sftp = ssh.open_sftp()
    ok, fail = 0, 0

    # ── 1. 同步数据库 ──
    print("\n📦 同步数据库...")
    try:
        # 确保远程目录存在
        ssh.exec_command(f"mkdir -p {REMOTE_BASE}/backend/data")
        sftp.put(LOCAL_DB, REMOTE_DB)
        print("  ✅ items.db 上传成功")
        ok += 1
    except Exception as e:
        print(f"  ❌ 数据库上传失败: {str(e)[:80]}")
        fail += 1

    # ── 2. 同步图片 ──
    print("\n🖼️  同步图片文件...")
    if os.path.exists(LOCAL_UPLOADS):
        # 先清空服务器上的旧图片
        ssh.exec_command(f"rm -rf {REMOTE_UPLOADS}/*")
        ssh.exec_command(f"mkdir -p {REMOTE_UPLOADS}")

        files = [f for f in os.listdir(LOCAL_UPLOADS) if os.path.isfile(os.path.join(LOCAL_UPLOADS, f))]
        for fname in files:
            local_path = os.path.join(LOCAL_UPLOADS, fname)
            remote_path = f"{REMOTE_UPLOADS}/{fname}"
            try:
                sftp.put(local_path, remote_path)
                ok += 1
            except Exception as e:
                print(f"  ❌ {fname}: {str(e)[:60]}")
                fail += 1
        print(f"  ✅ 已上传 {len(files)} 个图片文件")
    else:
        print("  ℹ️  本地 uploads 目录不存在，跳过")

    sftp.close()

    # ── 3. 重启服务 ──
    print("\n🔄 重启后端服务...")
    ssh.exec_command("systemctl restart thu-secondhand.service")
    time.sleep(3)

    # ── 4. 验证 ──
    print("验证运行状态...", end=" ")
    stdin, stdout, stderr = ssh.exec_command("curl -s http://localhost:5000/")
    result = stdout.read().decode().strip()
    if '"status": "ok"' in result:
        print("✅ 服务运行正常")
        ok += 1
    else:
        print(f"❌ {result[:80]}")
        fail += 1

    ssh.close()

    total = ok + fail
    print(f"\n{'='*60}")
    if fail == 0:
        print(f"  ✅ 同步完成！服务器数据已与本地一致。")
        print(f"    同步了 {ok} 项内容。")
    else:
        print(f"  ⚠️  同步完成，但有 {fail}/{total} 项失败。")
    print(f"{'='*60}")


if __name__ == "__main__":
    if not os.path.exists(LOCAL_DB):
        print("❌ 本地数据库不存在，请先运行后端产生数据。")
        sys.exit(1)

    if confirm():
        sync_to_server()
    else:
        print("\n❌ 已取消")
