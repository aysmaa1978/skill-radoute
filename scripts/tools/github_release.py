import subprocess
import json
import os
import urllib.request
import sys

ZIP = r'C:/Users/86155/.workbuddy/skills/skill-radoute/skill-radoute-v1.5.0.skill.zip'

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
    'tag_name': 'v1.5.0',
    'name': 'v1.5.0 — 云鼎安全修复 + 弱匹配守卫',
    'body': '## 云鼎安全修复\n1. 可信下载源\n2. SHA256 校验\n3. 版本锁定+人工确认\n\n## 路由增强\n- 弱匹配守卫\n- route --explain\n- 语义同义词提升',
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
upload_url = release['upload_url'].replace('{?name,label}', '?name=skill-radoute-v1.5.0.skill.zip')
with open(ZIP, 'rb') as f:
    zip_data = f.read()
req = urllib.request.Request(upload_url, data=zip_data, headers={
    'Authorization': f'Bearer {token}',
    'Accept': 'application/vnd.github+json',
    'Content-Type': 'application/octet-stream'
})
urllib.request.urlopen(req)
print('✅ Release v1.5.0 创建完成，附件已上传')
