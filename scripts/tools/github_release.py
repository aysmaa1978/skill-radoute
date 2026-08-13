import subprocess
import json
import os
import urllib.request
import sys

TAG = 'v3.0.0'
ZIP = r'E:/MyProject/skill-radoute/skill-radoute-v3.0.0.skill.zip'

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
    'name': 'v3.0.0 — 自然语言编排 + 交互式工作流',
    'body': (
        '## 🚀 v3.0.0 自然语言解析引擎 + 交互式工作流构建\n'
        '基于 v2.1 评测反馈（综合 4.7）打磨，目标 4.9：说一句话就能跑多技能工作流。\n\n'
        '### 自然语言解析引擎（NLP）\n'
        '1. **动作词表 20 类**：`intent.py` 在 10 类基础上新增 规划/头脑风暴/审阅/提取/转换/问答/测试/调试/下载/安装，含依赖图与建议技能\n'
        '2. **`workflow.parse_workflow(text)`**：一句话解析为可运行模板，依赖链（调研→整理→写作）自动串接 input/output\n\n'
        '### 交互式工作流构建\n'
        '1. **`workflow from-text "<任务>"`**：自然语言一键生成模板；`--save <名>` 落盘 YAML\n'
        '2. **`workflow build`**：逐步问答交互构建，可选字段直接回车跳过；`--save` 保存\n'
        '3. 保存位置：`SKILL_ROUTER_WORKFLOW_DIR` > `./workflows/` > `~/.workbuddy/workflows/`\n\n'
        '### 文档与规范（C 4.4 短板修复）\n'
        '1. README 更新日志精简至最近 3 版，完整历史移至 `CHANGELOG.md`（含版本速览表）\n'
        '2. FAQ 从 5 条扩展至 **22 条**（镜像/代理/反馈学习/工作流/并行/认证等）\n'
        '3. 新增「快速场景」章节（5 个常见场景直接抄命令）\n\n'
        '### 体验打磨（R 4.7 短板修复）\n'
        '1. 异常提示统一「原因 + 解决建议」两步式（模板找不到/无动作解析等）\n'
        '2. 本地反馈数据接入工作流技能推荐：被否决的技能跳过、被选中的优先\n\n'
        '### 兼容与质量\n'
        '- 公开接口不变（向后兼容），纯标准库零新依赖\n'
        '- 测试：新增 test_workflow_nlp.py（20 动作识别 + 24 条自然语言用例准确率 100% + build/from-text/--save/反馈推荐），原 6 套测试全绿'
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
