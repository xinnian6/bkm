#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地自动化抓取 + 推送一体脚本 (Windows 计划任务用)
跑 fetch_code.py 拿码 → 只在拿到码时 commit + push(失败不覆盖远程成功态)。

用法(计划任务调):
  python gh_actions_fetcher\auto_push.py
退出码: 0=成功推送/无变化; 2=没拿到码(不推,保留远程旧码)
"""
import io
import os
import sys
import subprocess
import time

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
FETCHER = os.path.join(HERE, "fetch_code.py")
JSON_OUT = os.path.join(REPO_ROOT, "current_code.json")
MD_OUT = os.path.join(REPO_ROOT, "latest.md")


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def main():
    os.chdir(REPO_ROOT)
    log(f"工作目录: {REPO_ROOT}")

    # 1. 跑抓取器
    log("=== 1/3 跑 fetch_code.py ===")
    r = subprocess.run([sys.executable, FETCHER], cwd=REPO_ROOT)
    if r.returncode != 0:
        log(f"抓取器退出码 {r.returncode},没拿到码,不推送(保留远程旧码)")
        return 2

    # 2. 读结果确认 code 非空
    import json
    if not os.path.exists(JSON_OUT):
        log("current_code.json 不存在,放弃")
        return 2
    with open(JSON_OUT, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not data.get("code"):
        log(f"code 为空(error={data.get('error')}),不推送")
        return 2
    log(f"拿到码: {data['code']},准备推送")

    # 3. git add + commit + push
    log("=== 2/3 git 提交 ===")
    def run(cmd):
        log(f"  $ {cmd}")
        return subprocess.run(cmd, shell=True, cwd=REPO_ROOT,
                              capture_output=True, text=True)

    run('git add current_code.json latest.md')
    diff = run('git diff --cached --quiet')
    if diff.returncode == 0:
        log("无变化(码跟上次一样),跳过 push")
        return 0

    run('git config user.name "xinnian6"')
    run('git config user.email "xinnian6@users.noreply.github.com"')
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    c = run(f'git commit -m "chore: update pokemon airport code ({ts})"')
    if c.returncode != 0:
        log(f"commit 失败: {c.stderr}")
        return 2

    log("=== 3/3 git push ===")
    # 走 Hiddify 代理 push(用户代理 12334)
    env = os.environ.copy()
    p = run('git push')
    if p.returncode != 0:
        log(f"直连 push 失败,试代理...")
        env["HTTPS_PROXY"] = "http://127.0.0.1:12334"
        env["HTTP_PROXY"] = "http://127.0.0.1:12334"
        p2 = subprocess.run('git push', shell=True, cwd=REPO_ROOT,
                            capture_output=True, text=True, env=env)
        log(f"  {p2.stdout} {p2.stderr}")
        if p2.returncode != 0:
            log("push 失败")
            return 2
    log("=== 推送完成 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
