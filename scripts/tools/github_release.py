import subprocess
import json
import os
import urllib.request
import sys

TAG = 'v3.0.1'
ZIP = r'E:/MyProject/skill-radoute/skill-radoute-v3.0.1.skill.zip'

if not os.path.exists(ZIP):
    print('鉂?ZIP 鏂囦欢涓嶅瓨鍦?)
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
    print('鉂?鏈幏鍙栧埌 GitHub Token')
    sys.exit(1)

print('鉁?Token 鑾峰彇鎴愬姛')
url = 'https://api.github.com/repos/aysmaa1978/skill-radoute/releases'
data = json.dumps({
    'tag_name': TAG,
    'name': 'v3.0.0 鈥?鑷劧璇█缂栨帓 + 浜や簰寮忓伐浣滄祦',
    'body': (
        '## 馃殌 v3.0.0 鑷劧璇█瑙ｆ瀽寮曟搸 + 浜や簰寮忓伐浣滄祦鏋勫缓\n'
        '鍩轰簬 v2.1 璇勬祴鍙嶉锛堢患鍚?4.7锛夋墦纾紝鐩爣 4.9锛氳涓€鍙ヨ瘽灏辫兘璺戝鎶€鑳藉伐浣滄祦銆俓n\n'
        '### 鑷劧璇█瑙ｆ瀽寮曟搸锛圢LP锛塡n'
        '1. **鍔ㄤ綔璇嶈〃 20 绫?*锛歚intent.py` 鍦?10 绫诲熀纭€涓婃柊澧?瑙勫垝/澶磋剳椋庢毚/瀹￠槄/鎻愬彇/杞崲/闂瓟/娴嬭瘯/璋冭瘯/涓嬭浇/瀹夎锛屽惈渚濊禆鍥句笌寤鸿鎶€鑳絓n'
        '2. **`workflow.parse_workflow(text)`**锛氫竴鍙ヨ瘽瑙ｆ瀽涓哄彲杩愯妯℃澘锛屼緷璧栭摼锛堣皟鐮斺啋鏁寸悊鈫掑啓浣滐級鑷姩涓叉帴 input/output\n\n'
        '### 浜や簰寮忓伐浣滄祦鏋勫缓\n'
        '1. **`workflow from-text "<浠诲姟>"`**锛氳嚜鐒惰瑷€涓€閿敓鎴愭ā鏉匡紱`--save <鍚?` 钀界洏 YAML\n'
        '2. **`workflow build`**锛氶€愭闂瓟浜や簰鏋勫缓锛屽彲閫夊瓧娈电洿鎺ュ洖杞﹁烦杩囷紱`--save` 淇濆瓨\n'
        '3. 淇濆瓨浣嶇疆锛歚SKILL_ROUTER_WORKFLOW_DIR` > `./workflows/` > `~/.workbuddy/workflows/`\n\n'
        '### 鏂囨。涓庤鑼冿紙C 4.4 鐭澘淇锛塡n'
        '1. README 鏇存柊鏃ュ織绮剧畝鑷虫渶杩?3 鐗堬紝瀹屾暣鍘嗗彶绉昏嚦 `CHANGELOG.md`锛堝惈鐗堟湰閫熻琛級\n'
        '2. FAQ 浠?5 鏉℃墿灞曡嚦 **22 鏉?*锛堥暅鍍?浠ｇ悊/鍙嶉瀛︿範/宸ヤ綔娴?骞惰/璁よ瘉绛夛級\n'
        '3. 鏂板銆屽揩閫熷満鏅€嶇珷鑺傦紙5 涓父瑙佸満鏅洿鎺ユ妱鍛戒护锛塡n\n'
        '### 浣撻獙鎵撶（锛圧 4.7 鐭澘淇锛塡n'
        '1. 寮傚父鎻愮ず缁熶竴銆屽師鍥?+ 瑙ｅ喅寤鸿銆嶄袱姝ュ紡锛堟ā鏉挎壘涓嶅埌/鏃犲姩浣滆В鏋愮瓑锛塡n'
        '2. 鏈湴鍙嶉鏁版嵁鎺ュ叆宸ヤ綔娴佹妧鑳芥帹鑽愶細琚惁鍐崇殑鎶€鑳借烦杩囥€佽閫変腑鐨勪紭鍏圽n\n'
        '### 鍏煎涓庤川閲廫n'
        '- 鍏紑鎺ュ彛涓嶅彉锛堝悜鍚庡吋瀹癸級锛岀函鏍囧噯搴撻浂鏂颁緷璧朶n'
        '- 娴嬭瘯锛氭柊澧?test_workflow_nlp.py锛?0 鍔ㄤ綔璇嗗埆 + 24 鏉¤嚜鐒惰瑷€鐢ㄤ緥鍑嗙‘鐜?100% + build/from-text/--save/鍙嶉鎺ㄨ崘锛夛紝鍘?6 濂楁祴璇曞叏缁?
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
print(f'鉁?Release {TAG} 鍒涘缓瀹屾垚锛岄檮浠跺凡涓婁紶')

