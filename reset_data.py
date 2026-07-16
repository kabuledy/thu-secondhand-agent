"""
清小闲 — 一键清空所有数据（初始化智能体）

清理内容：
1. ✓ 所有商品记录（items 表）
2. ✓ 所有标签统计（tag_stats 表）
3. ✓ 所有上传的图片文件（uploads/ 目录）
4. 本地 + 云服务器同步清理

用法：
  python reset_data.py

⚠️ 此操作不可逆！清空前会要求确认。
"""
import os
import sys
import shutil
from dotenv import load_dotenv

LOCAL = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(LOCAL, "backend", "data", "items.db")
UPLOADS_DIR = os.path.join(LOCAL, "backend", "data", "uploads")

# 从 backend/.env 加载服务器密码
dotenv_path = os.path.join(LOCAL, "backend", ".env")
load_dotenv(dotenv_path)

# ── 服务器配置（与 sync.py 一致） ──
HOST = os.environ.get("SERVER_HOST", "")
USER = "root"
PASS = os.environ.get("SERVER_PASS", "")
if not HOST or not PASS:
    print("❌ 请在 backend/.env 中设置 SERVER_HOST 和 SERVER_PASS")
    sys.exit(1)
REMOTE_DB = "/opt/thu-secondhand/backend/data/items.db"
REMOTE_UPLOADS = "/opt/thu-secondhand/backend/data/uploads"


def confirm() -> bool:
    """要求用户输入确认"""
    print("\n" + "=" * 60)
    print("   ⚠️  警告：即将清空所有数据！")
    print("=" * 60)
    print("  将删除：")
    print("    • 所有商品信息（数据库 items 表）")
    print("    • 所有标签统计（数据库 tag_stats 表）")
    print("    • 所有议价记录（数据库 bargain_data 表）")
    print("    • 所有上传的图片（uploads/ 目录）")
    print("  范围：本地开发环境 + 阿里云服务器")
    print("=" * 60)
    ans = input("\n确认清空？输入 YES 继续: ").strip()
    return ans == "YES"


def clear_local():
    """清空本地数据"""
    print("\n📦 清空本地数据...")

    # 1. 清空数据库
    ok = 0
    try:
        sys.path.insert(0, os.path.join(LOCAL, "backend"))
        from api.database import clear_all_items, get_connection
        clear_all_items()
        # 额外 VACUUM 回收磁盘空间
        conn = get_connection()
        conn.execute("VACUUM")
        conn.close()
        print("  ✅ 数据库已清空")
        ok += 1
    except Exception as e:
        print(f"  ❌ 数据库清空失败: {e}")

    # 2. 清空 uploads 目录（保留目录本身）
    if os.path.exists(UPLOADS_DIR):
        count = 0
        for f in os.listdir(UPLOADS_DIR):
            fp = os.path.join(UPLOADS_DIR, f)
            try:
                if os.path.isfile(fp):
                    os.remove(fp)
                    count += 1
            except Exception as e:
                print(f"  ⚠️ 删除文件失败 {f}: {e}")
        print(f"  ✅ 已删除 {count} 个本地图片文件")
        ok += 1
    else:
        print("  ℹ️  本地 uploads 目录不存在，跳过")
        ok += 1

    return ok


def clear_remote():
    """通过 SSH 清空服务器数据"""
    print("\n☁️  连接阿里云 ECS...")
    try:
        import paramiko
    except ImportError:
        print("  ❌ 请先安装 paramiko: pip install paramiko")
        return 0

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(HOST, username=USER, password=PASS, timeout=10)
    except Exception as e:
        print(f"  ❌ SSH 连接失败: {e}")
        return 0
    print("  ✅ 已连接")

    ok = 0

    # 1. 通过 Python 脚本清空数据库（比 SQLite CLI 更可靠）
    print("\n  📦 清空服务器数据库...")
    stdin, stdout, stderr = ssh.exec_command(
        "cd /opt/thu-secondhand/backend && python3 -c \""
        "from api.database import clear_all_items, get_connection; "
        "clear_all_items(); "
        "conn = get_connection(); conn.execute('VACUUM'); conn.close(); "
        "print('OK')"
        "\""
    )
    result = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    if result == "OK":
        print("  ✅ 数据库已清空")
        ok += 1
    else:
        print(f"  ❌ 清空失败: {result} {err[:100]}")

    # 2. 清空服务器 uploads 目录
    print("\n  📁 清空服务器图片文件...")
    stdin, stdout, stderr = ssh.exec_command(
        f"rm -rf {REMOTE_UPLOADS}/* && echo 'OK' || echo 'FAIL'"
    )
    result = stdout.read().decode().strip()
    if result == "OK":
        print("  ✅ 服务器图片已清空")
        ok += 1
    else:
        print(f"  ❌ 清空失败: {result}")

    # 3. 重启服务
    print("\n  🔄 重启后端服务...")
    ssh.exec_command("systemctl restart thu-secondhand.service")
    import time
    time.sleep(3)
    ok += 1

    # 4. 验证
    stdin, stdout, stderr = ssh.exec_command("curl -s http://localhost:5000/")
    result = stdout.read().decode().strip()
    if '"status": "ok"' in result:
        print("  ✅ 服务重启正常")
        ok += 1
    else:
        print(f"  ❌ 服务异常: {result[:100]}")

    ssh.close()
    return ok


def main():
    print("=" * 60)
    print("   清小闲 — 一键数据重置工具")
    print("=" * 60)

    if not confirm():
        print("\n❌ 已取消")
        return

    local_ok = clear_local()
    print(f"\n  本地清理: {local_ok}/2 项完成")

    remote_ok = clear_remote()
    print(f"  远程清理: {remote_ok}/4 项完成")

    total = local_ok + remote_ok
    max_total = 2 + 4  # local(2) + remote(4)

    print("\n" + "=" * 60)
    if total == max_total:
        print("  ✅ 全部清理完成！智能体已初始化。")
    else:
        print(f"  ⚠️ 部分完成（{total}/{max_total}），请检查上面的错误信息。")
    print("=" * 60)


if __name__ == "__main__":
    main()
