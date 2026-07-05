# 清小闲 — 一键同步到阿里云 ECS
# 用法：在 PowerShell 中运行  .\sync.ps1

$HOST = "39.106.1.145"
$USER = "root"
$PASS = "Thudy87ubantu..."
$LOCAL = "C:\Users\dengyi\Desktop\thu-secondhand-agent"
$REMOTE = "/opt/thu-secondhand"

$FILES = @(
    "backend/api/agent.py",
    "backend/api/image_utils.py",
    "backend/api/list_item.py",
    "backend/api/search_item.py",
    "backend/api/tag_utils.py",
    "backend/api/storage_backend.py",
    "backend/api/conversation_store.py",
    "backend/main.py",
    "backend/requirements.txt",
    "prompt/system_prompt.md"
)

Write-Host "=================================================="
Write-Host "  清小闲 — 同步到阿里云 ECS"
Write-Host "=================================================="

# 构建 Python 内联代码
$pyCode = @"
import paramiko, os, time

HOST = "$HOST"
USER = "$USER"
PASS = "$PASS"
LOCAL = r"$LOCAL"
REMOTE = "$REMOTE"

files = [$(($FILES | ForEach-Object { "r'$_'" }) -join ", ")]

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(HOST, username=USER, password=PASS, timeout=10)
sftp = ssh.open_sftp()

# mkdir
dirs = set()
for rel in files:
    local = os.path.join(LOCAL, rel)
    if os.path.exists(local):
        dirs.add(os.path.dirname(os.path.join(REMOTE, rel)))
for d in dirs:
    stdin, stdout, stderr = ssh.exec_command(f"mkdir -p {d}")
    stdout.read()
time.sleep(0.5)

# upload
ok = 0
for rel in files:
    local = os.path.join(LOCAL, rel)
    remote = os.path.join(REMOTE, rel)
    if not os.path.exists(local):
        continue
    try:
        with open(local, "rb") as f:
            data = f.read()
        with sftp.open(remote, "wb") as f:
            f.write(data)
        print(f"OK: {rel}")
        ok += 1
    except Exception as e:
        print(f"FAIL: {rel} -> {e}")

sftp.close()
ssh.close()
print(f"DONE: {ok} files uploaded")
"@

python -c $pyCode

# 重启服务
Write-Host "重启后端服务..."
python -c @"
import paramiko, time
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect("$HOST", username="$USER", password="$PASS")
ssh.exec_command("systemctl restart thu-secondhand.service")
time.sleep(3)
stdin, stdout, stderr = ssh.exec_command("curl -s http://localhost:5000/")
result = stdout.read().decode().strip()
if '"status": "ok"' in result:
    print("服务运行正常")
else:
    print("异常: " + result[:100])
ssh.close()
"@

Write-Host "`n✅ 同步完成！"
Write-Host "   后端地址: http://$HOST/"
