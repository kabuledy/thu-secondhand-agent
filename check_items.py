"""
检查数据库中现有商品和图片
用法：python check_items.py
"""
import os
import paramiko
from dotenv import load_dotenv

dotenv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend", ".env")
load_dotenv(dotenv_path)

HOST = "39.106.1.145"
USER = "root"
PASS = os.environ.get("SERVER_PASS", "")
if not PASS:
    print("❌ 未找到 SERVER_PASS，请在 backend/.env 中设置")
    exit(1)

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(HOST, username=USER, password=PASS, timeout=10, look_for_keys=False, allow_agent=False)

print("=== 所有在售商品 ===")
_, o, _ = ssh.exec_command(
    'cd /opt/thu-secondhand/backend && python3 -c "'
    'import sqlite3, json; '
    'conn = sqlite3.connect(\"data/items.db\"); '
    'rows = conn.execute(\"SELECT item_id, name, price, substr(image_url,1,80), status FROM items ORDER BY created_at DESC LIMIT 10\").fetchall(); '
    'for r in rows: print(r[0], r[1], r[2], r[3][:60], r[4])'
    '"'
)
print(o.read().decode().strip())

print("\n=== 上传目录文件 ===")
_, o, _ = ssh.exec_command("ls -lh /opt/thu-secondhand/uploads/ 2>/dev/null | head -10")
print(o.read().decode().strip() or "(empty)")

ssh.close()
