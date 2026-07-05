"""排查根路径 404 问题"""
import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect("39.106.1.145", username="root", password="Thudy87ubantu...", timeout=10, look_for_keys=False, allow_agent=False)

print("=== 模拟浏览器请求 ===")
_, o, _ = ssh.exec_command("curl -sI http://thu-secondhand.top/ 2>&1 | head -10")
print(o.read().decode().strip())

print("\n=== Nginx 站点配置顺序 ===")
_, o, _ = ssh.exec_command("ls -la /etc/nginx/sites-enabled/")
print(o.read().decode().strip())

print("\n=== 测试两个 server block ===")
# Test with thu-agent's server_name
_, o, _ = ssh.exec_command('curl -sI -H "Host: thu-secondhand.top" http://localhost/ 2>&1 | head -5')
print(f"Host: thu-secondhand.top → {o.read().decode().strip()[:200]}")

# Test with raw IP
_, o, _ = ssh.exec_command('curl -sI http://localhost/ 2>&1 | head -5')
print(f"No Host header: {o.read().decode().strip()[:200]}")

print("\n=== 查看 thu-agent 完整配置 ===")
_, o, _ = ssh.exec_command("cat /etc/nginx/sites-enabled/thu-agent")
print(o.read().decode().strip()[:500])

# Fix: Switch the order so thu-secondhand takes priority
# Or better: ensure thu-agent doesn't steal the traffic
print("\n=== 检查冲突 ===")
_, o, _ = ssh.exec_command("grep server_name /etc/nginx/sites-enabled/* | grep -v bak")
print(o.read().decode().strip())

ssh.close()
