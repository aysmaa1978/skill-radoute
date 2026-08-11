import subprocess
import json
import os
import urllib.request
import sys

TAG = 'v2.0.0'
ZIP = r'E:/MyProject/skill-radoute/skill-radoute-v2.0.0.skill.zip'

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
    'name': 'v2.0.0 — 从路由器升级为多技能编排引擎',
    'body': (
        '## 🚀 v2.0.0 多技能编排引擎\n'
        '从「路由器」升级为「多技能编排引擎」：不止选一个技能，而是定义、编排、执行多技能工作流。\n\n'
        '### 核心新能力\n'
        '1. **工作流编排**：`router.py workflow run <模板名>` 一条命令串行执行多步工作流（YAML/JSON 模板），步骤间自动传递上下文；任一步失败自动回滚该步写入，`workflow resume` 从断点续跑\n'
        '2. **并行执行引擎**：intent 按依赖图分层，无依赖子任务自动并行（`route --parallel` 强制），总耗时约等于最慢子任务\n'
        '3. **动态技能加载**：路由只加载候选 top3 元数据，执行时才按需加载完整内容，LRU 保留最近 5 个自动卸载（`registry.py cache stats/load/evict`）\n\n'
        '### 底座（v1.7 + v1.8）\n'
        '- v1.7：中文化报错、网络超时+进度、自动重试 3 次、README FAQ、quickstart.bat、口语化触发词\n'
        '- v1.8：注册表增量扫描+缓存、路由决策缓存、技能索引内存驻留+文件变更监听\n\n'
        '### 兼容与质量\n'
        '- `route` / `acquire` / `call` 公开接口不变（向后兼容），纯标准库零新依赖\n'
        '- 测试：新增 test_workflow.py 40 断言（模板/串行/回滚/resume/并行/动态加载），原 4 套测试全绿'
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
