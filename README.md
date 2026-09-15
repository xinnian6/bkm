# 宝可梦机场优惠码抓取器

GitHub Actions 定时从 [linux.do](https://linux.do) 福利羊毛板块抓取宝可梦机场当月优惠码,结果存仓库供 Cloudflare Worker 等下游拉取。

## 工作原理

1. DrissionPage 起 headless Chrome,过 linux.do 的 Cloudflare 挑战
2. 从标签页 `https://linux.do/tag/193-tag/193.json` 找标题含「宝可梦 + 兑换码/优惠码」的最新帖(每月自动匹配,不写死 topic id)
3. 双策略取码:
   - **S1 显式码**:扫首帖+回复里「优惠码/兑换码:XXX」,频次 ≥2 优先
   - **S2 名频次**:`pokemon_names.txt` 全量宝可梦名(简+繁 1482 个)扫回复,确认词加权,取 top
4. 从首帖抠官网/备用地址
5. 写 `current_code.json` + `latest.md`,失败不覆盖旧结果

## 输出

- `current_code.json` — 稳定 JSON,Worker 可从 raw 拉:
  ```
  https://raw.githubusercontent.com/xinnian16/baokemeng/main/current_code.json
  ```
  字段:`code` / `confidence` / `sites` / `candidates` / `scanned_at` / `topic_id` / `topic_title` / `error`(失败时)

## 触发

- 每周一 UTC 03:17(北京周一 11:17)cron
- 手动 `workflow_dispatch`

## 本地自测

```bash
pip install -r gh_actions_fetcher/requirements.txt
python gh_actions_fetcher/fetch_code.py
```
