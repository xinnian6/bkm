#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
宝可梦机场优惠码抓取器 (GitHub Actions 版)
从 pokemon_code_scanner.py 提炼,去掉 Windows/本地路径硬编码,跑在 Ubuntu runner 的 headless Chrome 上。

流程:
  1. DrissionPage 起 headless Chrome,过 linux.do 的 Cloudflare 挑战
  2. 从标签页 https://linux.do/tag/193-tag/193.json 找标题含「宝可梦+兑换码/优惠码」的最新帖
  3. 拉该帖全部回复,双策略取码:
       S1 显式码:首帖+回复里 「优惠码/兑换码/码：XXX」命中且该词频次≥2 → 优先
       S2 名频次:用 pokemon_names.txt 全量名表扫回复,频次最高(子串去重)兜底
  4. 从首帖抠官网/备用地址
  5. 写 current_code.json + latest.md;拿不到码时写 error 字段、不覆盖旧结果

输出结构 (current_code.json):
  { code, confidence, sites, candidates, scanned_at, topic_id, topic_title, [error] }
前 5 字段与本地 pokemon_code_scanner.py 的缓存格式一致,下游 pokemon_common.py 无感。
"""
import sys
import io
import os
import json
import re
import time
from collections import defaultdict, Counter

if sys.platform == "win32" and getattr(sys.stdout, "buffer", None) is not None:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from DrissionPage import Chromium, ChromiumOptions

BASE = "https://linux.do"
TAG_URL = f"{BASE}/tag/193-tag/193"

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
# 输出到仓库根,与本地 pokemon_code_scanner.py 的 current_code.json 同路径,下游无感
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", REPO_ROOT)
JSON_OUT = os.path.join(OUTPUT_DIR, "current_code.json")
MD_OUT = os.path.join(OUTPUT_DIR, "latest.md")
NAMES_FILE = os.path.join(HERE, "pokemon_names.txt")

# 标题关键词(找帖用)
TITLE_KEYWORDS = ["宝可梦", "pokemon"]
CODE_KEYWORDS = ["兑换码", "优惠码", "码", "免费", "流量", "兑换"]

# 确认词:回复里出现说明码对(已成功兑换)
CONFIRM_WORDS = [
    "已成功兑换", "成功兑换", "已兑换", "兑换成功", "已用",
    "感谢", "谢谢佬", "可以用", "已续费", "续费成功", "领到", "已领",
]
# 噪声词(名频次法排除:通用回复/确认/语气词,不是码)
NOISE_WORDS = {
    "楼主", "支持", "看看", "谢谢分享", "前排", "沙发", "感谢分享",
    "进来", "顶", "mark", "谢谢", "佬", "大佬", "宝可梦", "感谢",
    "感谢佬", "感谢大佬", "谢谢佬", "来了", "来了来了", "已成功兑换",
    "成功兑换", "已兑换", "兑换成功", "已用", "可以用", "已续费",
    "续费成功", "领到", "已领", "橙子", "辛苦", "辛苦了", "威武",
    "太棒", "继续", "加油", "不错", "厉害", "稳", "稳定", "好用",
    "公益", "佬友", "赛博", "鸡蛋", "领了", "续上", "续期", "打卡",
    "每月", "月初", "活动", "准时", "续了", "感谢分享", "准时打卡",
    "前来", "打卡", "领取", "答案", "的答案", "前排佬友", "感谢前排",
    "佬友", "前来",
}

# 显式码正则:S1 优先。「码/优惠码/兑换码」后跟 : 或 ：再跟 2-12 字(中/英/数字)
RE_EXPLICIT = re.compile(
    r"(?:优惠码|兑换码|码|是)[：:]\s*([^\s，。、,!！?？()（）]{2,12})"
)


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def load_pokemon_names():
    """读 pokemon_names.txt 全量名表。读不到就用最小内置集兜底。"""
    names = set()
    if os.path.exists(NAMES_FILE):
        with open(NAMES_FILE, "r", encoding="utf-8") as f:
            for line in f:
                w = line.strip()
                if len(w) >= 2:
                    names.add(w)
    if not names:
        names = {"吉利蛋", "皮卡丘", "妙蛙种子", "小火龙", "伊布",
                 "卡比兽", "梦幻", "超梦", "喷火龙", "胖丁"}
    return names


POKEMON_NAMES = load_pokemon_names()


def get_browser():
    """起 headless Chrome,过 Cloudflare。Linux/GitHub Actions 友好。"""
    options = ChromiumOptions()
    options.auto_port()
    options.set_timeouts(base=2)
    options.set_argument("--window-size=1280,900")
    # headless + CI 必需参数
    for arg in [
        "--headless=new",
        "--no-sandbox",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--lang=zh-CN",
        "--disable-features=IsolateOrigins,site-per-process",
    ]:
        options.set_argument(arg)
    options.set_user_agent(
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )
    # Linux 上若 runner 自带 chrome,DrissionPage 会自动探测;否则用自带的 chromium
    log("启动 headless 浏览器(过 Cloudflare)...")
    browser = Chromium(options)
    tab = browser.latest_tab
    tab.get(BASE)
    log("等 CF 挑战通过(最多 90 秒)...")
    for i in range(90):
        time.sleep(1)
        title = tab.title or ""
        tl = title.lower()
        if "linux.do" in tl or "linux do" in tl:
            log(f"CF 已通过({i+1}秒)")
            return browser, tab
        # 有些时候 CF 放行后 title 还没渲染,URL 不再是 challenge 也算过
        url = tab.url or ""
        if "linux.do" in url and "just a moment" not in tl and "请稍候" not in title and title:
            log(f"CF 已通过({i+1}秒,URL 检测)")
            return browser, tab
    log("[!] 90 秒未通过 CF 挑战(runner IP 可能被 CF 严拦)")
    return browser, tab


def _is_cf_challenge(html, title=""):
    """判断是不是 CF 挑战中转页。"""
    t = (title or "").lower()
    h = html or ""
    if "just a moment" in t or "请稍候" in t or "请稍候…" in t:
        return True
    if "<title>请稍候" in h or "just a moment" in h.lower():
        return True
    return False


def fetch_json(tab, url, timeout=90, retries=3):
    """拉 Discourse JSON。
    Discourse 把 JSON 包在 <pre> 里;但 .json 端点常被 CF 二次挑战拦。
    策略:CF 一直不放行就刷新重 tab.get(url) 重试,放行后等 <pre> 渲染。
    runner IP 段 CF 更严,给足时间。
    """
    for attempt in range(retries):
        if attempt > 0:
            log(f"  fetch_json 第 {attempt+1} 次重试: {url}")
        tab.get(url)
        start = time.time()
        # 阶段1:等 CF 放行(最多 timeout 秒),放行不了就刷新重来
        cf_deadline = start + timeout
        passed = False
        while time.time() < cf_deadline:
            time.sleep(0.7)
            html = tab.html or ""
            title = tab.title or ""
            if not _is_cf_challenge(html, title):
                passed = True
                break
        if not passed:
            continue  # 这次没过 CF,刷新重试
        # 阶段2:放行后等 <pre> 渲染(再给 20 秒)
        pre_deadline = time.time() + 20
        while time.time() < pre_deadline:
            time.sleep(0.5)
            html = tab.html or ""
            title = tab.title or ""
            if _is_cf_challenge(html, title):
                continue
            m = re.search(r"<pre>(.*?)</pre>", html, re.S)
            if m and len(m.group(1)) > 100:
                try:
                    return json.loads(m.group(1))
                except Exception:
                    break
            # 有的 Discourse 不包 pre,直接是裸 JSON 文本
            stripped = (html or "").strip()
            if stripped.startswith("{") and "topic_list" in stripped:
                try:
                    return json.loads(stripped)
                except Exception:
                    pass
    return None


def find_pokemon_topics(tab):
    """找标题含宝可梦+兑换码的帖子,返回 [(topic_id, title)],最新在前。
    主路:标签页 tag/193-tag/193.json;兜底:search.json 搜关键词。
    """
    matched = []
    # 主路:标签页
    data = fetch_json(tab, f"{TAG_URL}.json")
    if data:
        topics = data.get("topic_list", {}).get("topics", [])
        for t in topics:
            title = t.get("title", "")
            has_pokemon = any(k in title for k in TITLE_KEYWORDS) or \
                          any(k in title.lower() for k in TITLE_KEYWORDS)
            has_code = any(k in title for k in CODE_KEYWORDS)
            if has_pokemon and has_code:
                matched.append((t.get("id"), title))
        log(f"标签页找到 {len(matched)} 个匹配帖子")
    else:
        log("标签页 JSON 拉取失败(可能 CF 没过),转搜索兜底")

    # 去重用
    seen = {tid for tid, _ in matched}

    # 兜底:search.json
    if not matched:
        import urllib.parse as up
        q = up.quote("宝可梦 兑换码")
        sdata = fetch_json(tab, f"{BASE}/search.json?q={q}")
        topics2 = []
        if isinstance(sdata, dict):
            topics2 = sdata.get("topics", []) or []
        if topics2:
            log(f"搜索找到 {len(topics2)} 个候选")
            for t in topics2:
                title = t.get("title", "") or t.get("title_html", "") or ""
                tid = t.get("id")
                has_pokemon = any(k in title for k in TITLE_KEYWORDS) or \
                              any(k in title.lower() for k in TITLE_KEYWORDS)
                has_code = any(k in title for k in CODE_KEYWORDS)
                if has_pokemon and has_code and tid and tid not in seen:
                    seen.add(tid)
                    matched.append((tid, title))

    for tid, title in matched[:5]:
        log(f"  - {title} (id={tid})")
    return matched


def fetch_replies(tab, topic_id):
    """拉帖子全部回复(post_number>1) + 首帖正文。返回 (replies, main_html)。"""
    data = fetch_json(tab, f"{BASE}/t/{topic_id}.json")
    if not data:
        return [], ""
    posts = data.get("post_stream", {}).get("posts", [])
    main_post = next((p for p in posts if p.get("post_number") == 1), {})
    main_html = main_post.get("cooked", "") or main_post.get("raw", "")
    replies = [p for p in posts if p.get("post_number", 1) > 1]

    # 回复不够多时拉后续 post_ids(Discourse 分批)
    stream = data.get("post_stream", {}).get("stream", [])
    if len(replies) < 10 and len(stream) > len(posts):
        loaded_ids = {p.get("id") for p in posts}
        remaining = [pid for pid in stream if pid not in loaded_ids][:50]
        if remaining:
            params = "&".join(f"post_ids[]={pid}" for pid in remaining)
            more = fetch_json(tab, f"{BASE}/t/{topic_id}/posts.json?{params}")
            if more and isinstance(more, dict):
                for p in more.get("post_stream", {}).get("posts", []):
                    if p.get("post_number", 1) > 1:
                        replies.append(p)
    return replies, main_html


def extract_sites_from_main(main_html):
    """从首帖正文抠官网/备用地址(含 pokemon / p6m6 / waimaosass)。去尾斜杠去重。"""
    if not main_html:
        return []
    urls = re.findall(r"https?://[^\s\"'<>]+", main_html)
    sites = []
    seen = set()
    for u in urls:
        u = u.rstrip(".,;)")
        low = u.lower()
        if "pokemon" in low or "p6m6" in low or "waimaosass" in low:
            key = low.rstrip("/")
            if key in seen:
                continue
            seen.add(key)
            sites.append(u)
    return sites


def clean_text(html_text):
    """去 HTML 标签 + 压空白。"""
    t = re.sub(r"<[^>]+>", " ", html_text)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def extract_explicit_code(replies, main_html):
    """S1 显式码:扫「优惠码/兑换码/码：XXX」,统计候选频次,取频次≥2 的最高频。"""
    texts = [clean_text(main_html)]
    for r in replies:
        texts.append(clean_text(r.get("cooked", "") or r.get("raw", "")))
    blob = " ".join(texts)
    c = Counter()
    for m in RE_EXPLICIT.finditer(blob):
        w = m.group(1).strip(".,;:：")
        if w and w not in NOISE_WORDS:
            c[w] += 1
    if not c:
        return None
    top = c.most_common(3)
    for w, n in top:
        if n >= 2:
            log(f"S1 显式码命中: {w} (频次 {n})")
            return w, n
    # 频次都是 1,但只有唯一候选也认
    if len(c) == 1:
        w, n = top[0]
        log(f"S1 显式码(唯一候选): {w} (频次 {n})")
        return w, n
    return None


def extract_code_candidates(replies):
    """S2 名频次法:宝可梦名表 + 2-6 字中文词,确认词加权,子串去重,取 top5。"""
    word_scores = defaultdict(float)
    for reply in replies:
        text_clean = clean_text(reply.get("cooked", "") or reply.get("raw", ""))
        if not text_clean:
            continue
        candidates = set()
        # 1) 宝可梦名表直接命中
        for name in POKEMON_NAMES:
            if name in text_clean:
                candidates.add(name)
        # 2) 连续 2-6 字中文词(兜底,万一码不在表里)
        for m in re.finditer(r"[一-鿿]{2,6}", text_clean):
            w = m.group()
            if w not in NOISE_WORDS and len(w) >= 2:
                candidates.add(w)
        has_confirm = any(w in text_clean for w in CONFIRM_WORDS)
        for word in candidates:
            if word in NOISE_WORDS:
                continue
            score = 1.0
            if has_confirm:
                score += 3.0
            if word in POKEMON_NAMES:
                score += 2.0
            word_scores[word] += score
    ranked = sorted(word_scores.items(), key=lambda x: -x[1])
    # 子串去重:吉利是吉利蛋的子串,删短保长
    deduped = []
    for w, s in ranked:
        if not any(w != lw and w in lw for lw, _ in deduped):
            deduped.append((w, s))
    return deduped[:5]


def write_result(code, confidence, sites, candidates, topic_id, topic_title, error=None):
    """写 current_code.json + latest.md。"""
    data = {
        "code": code,
        "confidence": confidence,
        "sites": sites,
        "candidates": candidates,
        "scanned_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "topic_id": topic_id,
        "topic_title": topic_title,
    }
    if error:
        data["error"] = error
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(JSON_OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # latest.md 人读报告
    lines = [
        f"# 宝可梦机场优惠码 (抓取于 {data['scanned_at']})",
        "",
        f"- **来源帖子**: {topic_title or '(无)'}",
        f"- **topic_id**: {topic_id or '(无)'}",
        f"- **优惠码**: `{code or '(未取到)'}`",
        f"- **置信度**: {confidence}",
        f"- **官网/备用**: {sites or []}",
    ]
    if error:
        lines.append(f"- **错误**: {error}")
    if candidates:
        lines.append("")
        lines.append("## 候选码 (top5)")
        for c in candidates:
            w = c.get("word") if isinstance(c, dict) else c[0]
            s = c.get("score") if isinstance(c, dict) else c[1]
            lines.append(f"- {w}: {s}")
    lines.append("")
    with open(MD_OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main():
    log(f"工作目录: {REPO_ROOT}")
    log(f"名表: {NAMES_FILE} ({len(POKEMON_NAMES)} 个宝可梦名)")

    browser, tab = get_browser()
    code = None
    confidence = 0
    sites = []
    candidates = []
    topic_id = None
    topic_title = None
    error = None

    try:
        topics = find_pokemon_topics(tab)
        if not topics:
            error = "no_matching_topic"
            log("没找到宝可梦兑换码帖子")
            write_result(None, 0, [], [], None, None, error)
            return

        topic_id, topic_title = topics[0]
        log(f"分析帖子: {topic_title} (id={topic_id})")
        replies, main_html = fetch_replies(tab, topic_id)
        log(f"拉到 {len(replies)} 条回复")

        sites = extract_sites_from_main(main_html)
        log(f"官网地址: {sites}")

        # S1 显式码优先
        explicit = extract_explicit_code(replies, main_html)
        if explicit:
            code, n = explicit
            confidence = float(n) * 5  # 显式命中权重高

        # S2 名频次兜底
        ranked = extract_code_candidates(replies)
        candidates = [{"word": w, "score": s} for w, s in ranked]
        log("S2 候选码 (top5):")
        for w, s in ranked:
            log(f"  {w}: {s}")
        if not code and ranked:
            code, confidence = ranked[0]
        if not code:
            error = "no_code_guessed"
            log("没猜到码")
        else:
            log(f"=== 最终码: {code} (置信度 {confidence}) ===")
    except Exception as e:
        error = f"exception: {e}"
        log(f"异常: {e}")
    finally:
        try:
            browser.quit()
        except Exception:
            pass

    write_result(code, confidence, sites, candidates, topic_id, topic_title, error)
    # 失败退出码:GitHub Actions 步骤里可据 commit 步骤判断是否覆盖
    if code:
        print("\nRESULT_CODE:", code)
        print("RESULT_SITES:", sites)
        sys.exit(0)
    else:
        print("\n未取到码,error:", error)
        sys.exit(2)


if __name__ == "__main__":
    main()
