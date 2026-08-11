import subprocess
import json
import os
import urllib.request
import sys

TAG = 'v1.6.0'
ZIP = r'C:/Users/86155/.workbuddy/skills/skill-radoute/skill-radoute-v1.6.0.skill.zip'

if not os.path.exists(ZIP):
    print('❌ ZIP 文件不存在')
    sys.exit(1)

token = None
p = subprocess.Popen(['git', 'credential', 'fill'],
                     stdin=subprocess.PIPE,
                     stdout=subprocess.PIPE,
                     text=True)
out, _ = p.communicate('protocol=https\nhost=github.com\n\n')
for line in out.splitlines():
    if line.startswith('password='):
        token = line.split('=', 1)[1]
        break

if not token:
    print('❌ 未获取到 GitHub Token')
    sys.exit(1)

print('✅ Token 获取成功')
url = 'https://api.github.com/repos/aysmaa1978/skill-radoute/releases'
data = json.dumps({
    'tag_name': TAG,
    'name': 'v1.6.0 — Bug 修复 + 非交互防护 + 失败退出码',
    'body': (
        '## v1.6.0 Bug 修复\n'
        '1. **resume 崩溃**：acquire.py resume 参数回退到会话 context，消除 AttributeError 崩溃\n'
        '2. **P0 自动放行**：删除 P0 + --auto + 预置哈希自动放行分支，P0 恒需人工确认\n'
        '3. **current_skill 清空**：call close 栈空时保留 current_skill，switch --keep-open 后不再丢失\n'
        '4. **EOFError 防护**：非交互环境 input() 视为拒绝，不再崩溃\n'
        '5. **失败退出码**：acquire 失败链路返回非零退出码\n\n'
        '## 其他\n'
        '- --exclude 大小写不敏感 / 删除 finder 死常量 / registry stat OSError / sentinel kws 判定\n'
        '- 测试：4 套全绿（acquire 16 passed，call_chain 17 项）'
    ),
    'draft': False,
    'prerelease': False
})
req = urllib.request.Request(url, data=data.encode('utf-8'), headers={
    'Authorization': f'Bearer {token}',
    'Accept': 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28'
})
resp = urllib.request.urlopen(req)
release = json.loads(resp.read().decode())
upload_url = release['upload_url'].replace('{?name,label}', f'?name=skill-radoute-{TAG}.skill.zip')
with open(ZIP, 'rb') as f:
    zip_data = f.read()
req = urllib.request.Request(upload_url, data=zip_data, headers={
    'Authorization': f'Bearer {token}',
    'Accept': 'application/vnd.github+json',
    'Content-Type': 'application/octet-stream'
})
urllib.request.urlopen(req)
print(f'✅ Release {TAG} 创建完成，附件已上传')
