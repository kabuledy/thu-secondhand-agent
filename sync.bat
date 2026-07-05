@echo off
chcp 65001 >nul
echo ==================================================
echo   清小闲 — 同步到阿里云 ECS
echo   用法：直接双击运行
echo ==================================================
echo.

python -c "import paramiko, os, time; ssh=paramiko.SSHClient(); ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy()); ssh.connect('39.106.1.145', username='root', password='Thudy87ubantu...', timeout=10); sftp=ssh.open_sftp(); local=r'C:\Users\dengyi\Desktop\thu-secondhand-agent'; remote='/opt/thu-secondhand'; files=['backend/api/agent.py','backend/api/image_utils.py','backend/api/list_item.py','backend/api/search_item.py','backend/api/tag_utils.py','backend/api/storage_backend.py','backend/api/database.py','backend/api/conversation_store.py','backend/main.py','backend/requirements.txt','prompt/system_prompt.md']; dirs=set(); [dirs.add(os.path.dirname(os.path.join(remote,rel))) for rel in files if os.path.exists(os.path.join(local,rel))]; [ssh.exec_command(f'mkdir -p {d}') for d in dirs]; time.sleep(0.5); ok=0; [exec('with open(os.path.join(local,rel),\"rb\") as f: data=f.read()\nwith sftp.open(os.path.join(remote,rel),\"wb\") as f: f.write(data)\nprint(f\"OK: {rel}\"); ok+=1') for rel in files if os.path.exists(os.path.join(local,rel))]; sftp.close(); print(f'Uploaded: {ok} files')"
if %ERRORLEVEL% NEQ 0 (
    echo 上传失败，请检查网络连接和密码
    pause
    exit /b 1
)

echo.
echo 重启后端服务...
python -c "import paramiko, time; ssh=paramiko.SSHClient(); ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy()); ssh.connect('39.106.1.145', username='root', password='Thudy87ubantu...'); ssh.exec_command('systemctl restart thu-secondhand.service'); time.sleep(3); stdin,stdout,stderr=ssh.exec_command('curl -s http://localhost:5000/'); r=stdout.read().decode().strip(); print('服务运行正常' if 'status.*ok' in r else f'异常: {r[:80]}'); ssh.close()"

echo.
echo ✅ 同步完成！
echo    后端地址: http://39.106.1.145/
pause
