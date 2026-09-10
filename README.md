# 🏀 NBA-Daily-Bot

每天自动抓取 NBA 赛程/比分，在终端打印成漂亮的表格，并推送到微信（Server 酱）。
配合 GitHub Actions，可实现**每天早上 8 点免费自动播报**。

## 功能特性

- 抓取指定日期的全部 NBA 比赛（赛程、实时比分、终场比分）
- 主客场、比赛状态（未开始 / 进行中 / 已结束）识别
- 自动把美东时间换算成**北京时间**
- 终端 `rich` 表格美化输出
- 一键推送到微信（Server 酱）
- 双数据源：NBA 官方接口优先，失败自动回退 ESPN 公开接口
- GitHub Actions 定时运行，零成本托管

## 项目结构

```
nba-daily-bot/
├── main.py                     # 程序入口（终端表格 + 推送）
├── src/
│   ├── fetcher.py              # 数据抓取（nba_api / ESPN），并做标准化
│   ├── formatter.py            # 时区转换、数据组装、Markdown 生成
│   └── notifier.py             # Server 酱微信推送
├── tests/test_offline.py       # 离线单元测试（校验解析逻辑，无需联网）
├── .github/workflows/daily.yml # GitHub Actions 定时任务
├── .env.example                # 环境变量示例
└── requirements.txt
```

## 快速开始

### 1. 准备环境

```bash
python -m venv venv
# Windows
venv\Scripts\activate
# macOS / Linux
source venv/bin/activate

pip install -r requirements.txt
```

### 2. 配置微信推送（可选）

1. 打开 [sct.ftqq.com](https://sct.ftqq.com/)，微信扫码登录，复制 SendKey。
2. 复制 `.env.example` 为 `.env`，填入 Key：

```text
SERVERCHAN_KEY=SCT你的Key
```

> 未配置 Key 时程序只会跳过推送，不影响赛程播报。`.env` 已被 `.gitignore` 忽略，不会泄露。

### 3. 运行

```bash
# 播报今天（北京时间）的赛程
python main.py

# 播报指定日期（美东日期）；休赛期想看效果可以用这个
python main.py --date 2026-10-28

# 指定数据源：auto（默认）/ nba_api / espn
python main.py --source espn

# 只打印不推送
python main.py --no-push
```

### 4. 运行测试

```bash
python -m unittest discover -s tests -v
```

## 终端输出示例

```
  🏀 2026-01-15 NBA 赛程（数据来源：ESPN）
┌────────┬──────┬───────────┬──────┬─────────────┐
│  状态  │ 客队 │   比分    │ 主队 │ 时间 (北京) │
├────────┼──────┼───────────┼──────┼─────────────┤
│ 已结束 │ MEM  │ 111 - 118 │ ORL  │ 01-16 03:00 │
│ 已结束 │ PHX  │ 105 - 108 │ DET  │ 01-16 08:00 │
│ 已结束 │ BOS  │ 119 - 114 │ MIA  │ 01-16 08:30 │
│ 已结束 │ OKC  │ 111 - 91  │ HOU  │ 01-16 08:30 │
│ 已结束 │ MIL  │ 101 - 119 │ SA   │ 01-16 09:00 │
└────────┴──────┴───────────┴──────┴─────────────┘
```

微信推送效果（Markdown）：

```
🏀 2026-01-15 NBA 赛程

已结束 | MEM 111 - 118 ORL | 01-16 03:00
已结束 | PHX 105 - 108 DET | 01-16 08:00
...
```

## GitHub Actions 自动播报

`.github/workflows/daily.yml` 已配置好：每天 UTC 0 点（**北京时间早上 8 点**）自动运行。
推送到 GitHub 后只需再配一个密钥：

1. 仓库页面 `Settings -> Secrets and variables -> Actions`
2. `New repository secret`，Name 填 `SERVERCHAN_KEY`，Value 填你的真实 SendKey
3. 在 `Actions` 标签页可手动 `Run workflow` 立即验证

> 提示：GitHub 的定时任务高峰期可能延迟几分钟，属于正常现象。

## 数据源说明

| 数据源 | 说明 | 适用场景 |
| --- | --- | --- |
| `nba_api`（ScoreboardV3 / V2） | NBA 官方 stats 接口，数据最权威 | GitHub Actions（海外服务器）、可直连的网络 |
| ESPN | 公开 JSON 接口，无需 Key | 官方接口被网络拦截时自动兜底 |

程序默认 `auto`：先试官方接口，失败或返回空时自动切换到 ESPN，并在表格标题显示实际数据来源。

## 常见问题

1. **`ModuleNotFoundError`**：没有激活虚拟环境，或没执行 `pip install -r requirements.txt`。
2. **抓取失败 / `JSONDecodeError`**：官方接口被网络拦截或限流，程序会自动回退 ESPN；也可以设置代理环境变量 `HTTPS_PROXY` 后重试。
3. **微信收不到推送**：检查 `.env` 里的 Key 是否有多余空格、是否触发了 Server 酱频率限制。
4. **GitHub Actions 运行失败**：到仓库 `Actions` 标签页查看具体步骤日志，通常是 `SERVERCHAN_KEY` 没配置。

## 开源协议

[MIT](LICENSE)
