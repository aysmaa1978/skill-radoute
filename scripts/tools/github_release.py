import subprocess
import json
import os
import urllib.request
import sys

TAG = 'v2.1.0'
ZIP = r'E:/MyProject/skill-radoute/skill-radoute-v2.1.0.skill.zip'

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
    'name': 'v2.1.0 — 国内镜像源适配 + 路由反馈学习',
    'body': (
        '## 🚀 v2.1.0 国内镜像源适配 + 路由反馈学习\n'
        'P0 解决国内网络拉取 GitHub Release 超时问题；P1 让路由「越用越准」。\n\n'
        '### P0 国内镜像源适配\n'
        '1. **多源自动切换**：`finder.py` 内置镜像列表 `MIRRORS`（github.com / hub.fastgit.xyz / gitclone.com），下载失败自动切换并提示「⚠️ GitHub 连接超时，切换至国内镜像源...」；`SKILL_RADOUTE_MIRROR` 环境变量可覆盖默认列表\n'
        '2. **代理配置**：`acquire.py` 自动启用 `HTTP_PROXY`/`HTTPS_PROXY`/`GITHUB_PROXY`，无代理时给出设置指引\n'
        '3. **quickstart.bat**：新增可选镜像配置步骤，选 y 即写入 `SKILL_RADOUTE_MIRROR` 用户环境变量\n\n'
        '### P1 路由反馈学习\n'
        '1. **反馈数据模型**：新增 `learning.py`，反馈只存本机 `~/.workbuddy/feedback.json`（`SKILL_RADOUTE_FEEDBACK` 可改路径），绝不上传\n'
        '2. **打分加权**：任务与反馈相似度 > 0.8 时，chosen 技能 +1.5×weight、excluded 技能 −2.0×weight（weight 1.0 全量 / 0.5 减半）\n'
        '3. **管理命令**：`router.py feedback list|clear|stats`；记录/清空后路由缓存自动失效\n'
        '4. **隐私承诺**：SKILL.md 新增说明，数据仅本地使用、可随时一键清空\n\n'
        '### 兼容与质量\n'
        '- 公开接口不变（向后兼容），纯标准库零新依赖\n'
        '- 测试：新增 test_learning.py 38 项断言（反馈记录/查询/加权/镜像切换），原 4 套测试全绿'
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
