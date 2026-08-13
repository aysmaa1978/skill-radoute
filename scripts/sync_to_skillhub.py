#!/usr/bin/env python3
"""sync_to_skillhub.py - mirror the git repo into the SkillHub-installed copy.

Defaults assume this script lives at <repo>/scripts/. It syncs everything under
the repo root into ~/.workbuddy/skills/skill-radoute__skillhub/, skipping
.git/ and __pycache__/. Idempotent: files with matching sha256 are skipped.
Use --prune to also remove files in dst that no longer exist in src.

Usage:
    python3 scripts/sync_to_skillhub.py                # sync
    python3 scripts/sync_to_skillhub.py --dry-run      # show what would change
    python3 scripts/sync_to_skillhub.py --prune        # also delete extras in dst
"""

import argparse, hashlib, os, shutil, sys

EXCLUDE_DIRS = {".git", "__pycache__", ".workbuddy"}
EXCLUDE_FILES = {".DS_Store"}
EXCLUDE_SUFFIX = (".pyc", ".zip", ".skill")
# .bat：SkillHub 提交不允许该文件类型。quickstart.bat 保留在 git 仓库供开发者
# 使用，但不复制进 SkillHub 安装副本；已存在的旧 .bat 副本由 --prune 清理。
COPY_EXCLUDE_SUFFIX = (".bat",)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def walk(root):
    """Yield files under root, skipping excluded dirs/files."""
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in EXCLUDE_DIRS and not d.startswith(".")]
        for f in fn:
            if f in EXCLUDE_FILES or f.endswith(EXCLUDE_SUFFIX) \
                    or f.endswith(COPY_EXCLUDE_SUFFIX):
                continue
            yield os.path.join(dp, f)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--src", help="repo root (default: parent of scripts/)")
    ap.add_argument("--dst", help="target SkillHub copy "
                                  "(default: ~/.workbuddy/skills/skill-radoute__skillhub)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--prune", action="store_true",
                    help="delete files in dst that no longer exist in src")
    args = ap.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    src = os.path.abspath(args.src or os.path.dirname(here))
    dst = os.path.abspath(args.dst or os.path.expanduser(
        "~/.workbuddy/skills/skill-radoute__skillhub"))

    if os.path.realpath(src) == os.path.realpath(dst):
        sys.exit(f"src and dst resolve to the same path: {src}")
    if not os.path.isdir(src):
        sys.exit(f"src not found: {src}")

    copied = skipped = pruned = 0
    src_files = set()

    for sp in walk(src):
        rel = os.path.relpath(sp, src)
        src_files.add(rel)
        dp = os.path.join(dst, rel)
        if os.path.isfile(dp) and sha256(sp) == sha256(dp):
            skipped += 1
            continue
        if not args.dry_run:
            os.makedirs(os.path.dirname(dp), exist_ok=True)
            shutil.copy2(sp, dp)
        copied += 1
        print(f"  {'[dry-run] ' if args.dry_run else ''}copy {rel}")

    if args.prune:
        for dp, dn, fn in os.walk(dst):
            dn[:] = [d for d in dn if d not in EXCLUDE_DIRS]
            for f in fn:
                if f in EXCLUDE_FILES or f.endswith(EXCLUDE_SUFFIX):
                    continue
                rp = os.path.relpath(os.path.join(dp, f), dst)
                if rp not in src_files:
                    fp = os.path.join(dp, f)
                    if not args.dry_run:
                        os.remove(fp)
                    pruned += 1
                    print(f"  {'[dry-run] ' if args.dry_run else ''}prune {rp}")

    print(f"\nsrc={src}")
    print(f"dst={dst}")
    print(f"copied={copied} skipped={skipped} pruned={pruned}"
          + (" (dry-run)" if args.dry_run else ""))


if __name__ == "__main__":
    main()