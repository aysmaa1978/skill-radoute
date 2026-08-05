#!/usr/bin/env python3
"""Security audit for a downloaded skill package, pre-install gate.

Part of skill-radoute v1.1 remote-acquisition chain.
Step 2 of: find -> audit -> confirm -> install -> register.

Scans SKILL.md + scripts/ + references/ + assets/ text for dangerous
patterns and emits a risk verdict. Rules are built-in (no config file);
they encode the gate defined in references/remote-acquisition.md:

    P0 (high)   -> reject by default, install only if user insists
    P1 (medium) -> list risks, require explicit confirmation
    P2 (low/none) -> installable

This is a pure module: it returns a structured report and writes nothing
to disk or trace. The acquire.py orchestrator owns persistence so this
stays replayable and unit-testable.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

SEV_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3}

# text files we actually read; everything else (images, fonts, zips) skipped
TEXT_EXT = {
    ".md", ".py", ".sh", ".js", ".ts", ".json", ".yaml", ".yml",
    ".txt", ".toml", ".ps1", ".bat", ".cfg", ".ini", ".skills",
}

# id, category, severity, regex, note
_RAW_RULES = [
    # ---- prompt injection ----
    ("pi.ignore", "prompt_injection", "high",
     r"ignore (all |any )?(previous|prior|above|preceding) (instructions|prompt|system)",
     "指示忽略既有指令，典型提示注入"),
    ("pi.silent", "prompt_injection", "high",
     r"(do not|don'?t|never) tell (the |your )?(user|human|client)",
     "要求对用户保密，提示注入信号"),
    ("pi.hide", "prompt_injection", "medium",
     r"(keep (this|it|a secret)|do not disclose|hide (this|it) from)",
     "疑似要求隐瞒信息"),

    # ---- shell execution ----
    ("sh.pipetoshell", "shell_exec", "high",
     r"(curl|wget)\b[^\n]*\|\s*(sh|bash|python3?|perl|ruby)",
     "下载后直接管道执行，供应链高危"),
    ("sh.obfuscate", "shell_exec", "high",
     r"base64\.b64decode|powershell\s+(-Enc|-EncodedCommand)|Invoke-Expression|iex\s+",
     "混淆或编码执行"),
    ("sh.evalexec", "shell_exec", "medium",
     r"\b(eval|exec)\s*\(",
     "动态执行代码"),
    ("sh.osystem", "shell_exec", "medium",
     r"os\.system\s*\(|subprocess\.(call|Popen|run)\s*\([^)]*shell\s*=\s*True",
     "通过 shell 执行命令"),

    # ---- network exfiltration ----
    ("net.upload", "network", "high",
     r"(requests\.(post|put)[^\n]*files=|curl\s+[^\n]*--upload-file|scp\s+[^\n]*\s+[^\n]*@)",
     "疑似上传本地文件到远程"),
    ("net.post", "network", "low",
     r"(requests\.(post|put)|curl\s+[^\n]*-X\s*(POST|PUT)|urllib\.request\.urlopen)\b",
     "向外发起请求（常见且通常无害，仅作提示）"),

    # ---- secret access ----
    ("sec.keyfiles", "secret", "high",
     r"(\.ssh|id_rsa|\.pem|\.keystore|keychain|Login Keychain|Cookies|cookies\.db|-----BEGIN [A-Z ]*PRIVATE KEY-----)",
     "触碰密钥/凭据/浏览器存储"),
    ("sec.envkey", "secret", "low",
     r"os\.environ\.\w+\s*\(?\s*[\"']?(API_KEY|SECRET|TOKEN|PASSWORD|PRIVATE_KEY)",
     "读取自身 API Key 环境变量（常见，仅作提示）"),
    ("sec.dotenv", "secret", "low",
     r"\b\.env\b",
     "读取 .env（需确认来源是否可信）"),

    # ---- dangerous file operations ----
    ("fo.recursive_del", "file_ops", "high",
     r"rm\s+-rf\s+(/|~|\*|\$HOME)|rmtree\s*\(\s*(os\.path\.expanduser\(['\"]~|Path\.home)",
     "递归删除用户/系统目录"),
    ("fo.home_write", "file_ops", "low",
     r"(os\.path\.expanduser\(['\"]~|Path\.home\(\)|expandvars\(['\"]~)",
     "引用用户主目录（常见，仅作提示）"),
    ("fo.sensitive_dir", "file_ops", "low",
     r"[\/\\](Desktop|Documents|Downloads|Pictures|AppData)",
     "引用用户敏感目录（常见，仅作提示）"),

    # ---- persistence ----
    ("per.cron", "persistence", "high",
     r"crontab|/etc/cron",
     "写入定时任务"),
    ("per.rc", "persistence", "high",
     r"(\.bashrc|\.zshrc|\.profile|/etc/profile|launchd|启动|Startup)",
     "写入 shell 启动项/开机自启"),
    ("per.other_skill", "persistence", "high",
     r"\.\./|\.\./\.\./",
     "写入上级目录，可能篡改其他技能"),
]


def _compile(rules):
    out = []
    for rid, cat, sev, pat, note in rules:
        out.append({
            "id": rid, "category": cat, "severity": sev,
            "re": re.compile(pat, re.IGNORECASE), "note": note,
        })
    return out


RULES = _compile(_RAW_RULES)


def _iter_text_files(root: Path):
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in TEXT_EXT and "__pycache__" not in p.parts:
            yield p


def _snippet(line: str, width: int = 200) -> str:
    s = line.strip()
    return s if len(s) <= width else s[:width] + "..."


def audit(skill_dir, rules: list = RULES, min_severity: str = "low") -> dict:
    root = Path(skill_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"not a directory: {skill_dir}")
    min_sev = SEV_ORDER.get(min_severity, 1)
    findings: list[dict] = []
    scanned: list[str] = []
    for fp in _iter_text_files(root):
        rel = str(fp.relative_to(root))
        scanned.append(rel)
        try:
            text = fp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for ln, line in enumerate(text.splitlines(), 1):
            for r in rules:
                if r["re"].search(line):
                    findings.append({
                        "rule": r["id"], "category": r["category"],
                        "severity": r["severity"], "file": rel,
                        "line": ln, "snippet": _snippet(line),
                        "note": r["note"],
                    })
    # de-dup identical (rule,file,line) keeping first
    seen = set()
    uniq = []
    for f in findings:
        key = (f["rule"], f["file"], f["line"])
        if key not in seen:
            seen.add(key)
            uniq.append(f)
    uniq.sort(key=lambda x: (-SEV_ORDER[x["severity"]], x["file"], x["line"]))
    risk = max((SEV_ORDER[f["severity"]] for f in uniq), default=0)
    risk_name = {v: k for k, v in SEV_ORDER.items()}[risk]
    verdict = {"high": "P0", "medium": "P1", "low": "P2", "none": "P2"}[risk_name]
    counts = {s: sum(1 for f in uniq if f["severity"] == s)
              for s in ("high", "medium", "low")}
    summary = (f"{risk_name.upper()} ({verdict}): "
               f"{counts['high']} high / {counts['medium']} medium / {counts['low']} low "
               f"across {len(scanned)} files")
    return {
        "risk": risk_name,
        "verdict": verdict,
        "skill_dir": str(root),
        "scanned_files": scanned,
        "findings": uniq,
        "counts": counts,
        "summary": summary,
    }


def _print(report: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    print(report["summary"])
    print(f"  verdict: {report['verdict']}   files scanned: {len(report['scanned_files'])}")
    if not report["findings"]:
        print("  no findings")
        return
    for f in report["findings"]:
        print(f"  [{f['severity']:6}] {f['rule']:18} {f['file']}:{f['line']}")
        print(f"           {f['note']}")
        print(f"           > {f['snippet'][:160]}")


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="security_check.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("audit", help="audit a skill directory")
    p.add_argument("dir")
    p.add_argument("--min-severity", default="low",
                   choices=["low", "medium", "high"])
    p.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if a.cmd == "audit":
        try:
            rep = audit(a.dir, min_severity=a.min_severity)
        except FileNotFoundError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        _print(rep, a.json)
        # exit code reflects risk so acquire can branch on it
        return {"none": 0, "low": 0, "medium": 1, "high": 2}[rep["risk"]]
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
