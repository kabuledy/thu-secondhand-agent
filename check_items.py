"""检查数据库中现有商品和图片"""
import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect("39.106.1.145", username="root", password="Thudy87ubantu...", timeout=10, look_for_keys=False, allow_agent=False)

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
