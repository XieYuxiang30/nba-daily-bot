# NBA每日赛程播报小工具：零基础完整实操指南

## 阶段一：环境搭建与项目初始化

### 1.1 准备工作
确保你已经安装了上个回答中提到的软件：**Python**、**VS Code**、**Git**、**GitHub Desktop**。

### 1.2 创建项目文件夹
在你的电脑上（比如D盘或桌面）新建一个文件夹，命名为 `NBA-Daily-Bot`。
打开 **VS Code**，点击左上角的 `File -> Open Folder`，选中你刚建的 `NBA-Daily-Bot` 文件夹。

### 1.3 创建虚拟环境（非常重要）
在 VS Code 顶部菜单栏选择 `Terminal -> New Terminal`，在下方弹出的终端里输入：

```bash
python -m venv venv
```

*等待几秒，你会发现左侧目录多了一个 `venv` 文件夹。*
接下来激活它：

- **Windows 用户**：输入 `venv\Scripts\activate` (如果报错，尝试 `..\venv\Scripts\activate`)
- **Mac 用户**：输入 `source venv/bin/activate`

*成功标志：终端最前面会出现 `(venv)` 字样。*

### 1.4 安装依赖库
在终端输入：

```bash
pip install nba_api rich requests python-dotenv
```

安装完成后，输入 `pip freeze > requirements.txt`，这会生成一个依赖清单文件，方便别人运行你的项目。

---

## 阶段二：编写核心代码

在项目根目录下，新建三个文件：`main.py`、`.env`、`.gitignore`。再新建一个文件夹 `src`，在 `src` 里面新建 `fetcher.py`、`formatter.py`、`notifier.py`。

### 2.1 配置环境变量 (`.env`)
打开 `.env` 文件，写入你的微信推送Key（稍后去获取，现在先占位）：

```text
SERVERCHAN_KEY=SCT123456你的Key
```

打开 `.gitignore` 文件，写入以下内容（**这极其重要，防止你的隐私泄露**）：

```text
venv/
__pycache__/
.env
```

### 2.2 抓取数据 (`src/fetcher.py`)
打开 `fetcher.py`，写入以下代码（我加了详细注释）：

```python
from nba_api.stats.endpoints import scoreboardv2
from datetime import datetime

def get_today_games():
    # 获取今天的NBA比赛数据
    today = datetime.now().strftime('%Y-%m-%d')
    
    try:
        # 调用NBA API
        board = scoreboardv2.ScoreboardV2(game_date=today)
        # 获取比赛数据
        games = board.game_header.get_dict()['data']
        # 获取球队和比分数据
        line_scores = board.line_score.get_dict()['data']
        
        return games, line_scores
    except Exception as e:
        print(f"抓取数据失败: {e}")
        return None, None
```

### 2.3 处理与格式化数据 (`src/formatter.py`)
新建 `src/formatter.py`。这里我们要解决**时区转换**和**数据组装**的问题：

```python
from datetime import datetime, timedelta

def format_games(games, line_scores):
    # 将原始数据转换为易读的格式
    if not games:
        return "今日无NBA比赛安排。"

    formatted_data = []
    
    # 建立球队ID到比分的映射
    score_dict = {}
    team_dict = {}
    for score in line_scores:
        team_id = score[3] # 球队ID
        pts = score[22]    # 得分
        score_dict[team_id] = pts
        team_dict[team_id] = score[4] # 球队缩写
        
    for game in games:
        game_status = game[3] # 比赛状态 (1=未开始, 2=进行中, 3=已结束)
        game_time_utc = game[4] # 比赛时间 (UTC)
        home_team_id = game[6]
        away_team_id = game[7]
        
        # 转换时间为北京时间 (UTC+8)
        if game_time_utc:
            utc_time = datetime.strptime(game_time_utc[:19], "%Y-%m-%dT%H:%M:%S")
            bj_time = utc_time + timedelta(hours=8)
            time_str = bj_time.strftime("%m-%d %H:%M")
        else:
            time_str = "时间待定"
            
        away_team = team_dict.get(away_team_id, "客队")
        home_team = team_dict.get(home_team_id, "主队")
        
        away_score = score_dict.get(away_team_id, 0)
        home_score = score_dict.get(home_team_id, 0)
        
        if game_status == 3:
            status_str = "已结束"
            score_str = f"{away_score} - {home_score}"
        elif game_status == 2:
            status_str = "进行中"
            score_str = f"{away_score} - {home_score}"
        else:
            status_str = "未开始"
            score_str = "VS"
            
        formatted_data.append({
            "status": status_str,
            "away": away_team,
            "score": score_str,
            "home": home_team,
            "time": time_str
        })
        
    return formatted_data
```

### 2.4 终端美化输出 (`main.py`)
打开 `main.py`：

```python
from src.fetcher import get_today_games
from src.formatter import format_games
from src.notifier import send_to_wechat
from rich.console import Console
from rich.table import Table

def print_to_terminal(formatted_games):
    # 在终端打印漂亮的表格
    if isinstance(formatted_games, str):
        print(formatted_games)
        return

    console = Console()
    table = Table(title="🏀 今日NBA赛程", show_header=True, header_style="bold magenta")
    
    table.add_column("状态", style="cyan", justify="center")
    table.add_column("客队", style="green")
    table.add_column("比分", style="yellow", justify="center")
    table.add_column("主队", style="green")
    table.add_column("时间 (北京)", style="blue", justify="center")

    for game in formatted_games:
        table.add_row(
            game["status"], game["away"], game["score"], 
            game["home"], game["time"]
        )
    
    console.print(table)

if __name__ == "__main__":
    games, line_scores = get_today_games()
    formatted_games = format_games(games, line_scores)
    
    # 终端输出
    print_to_terminal(formatted_games)
    
    # 微信推送
    send_to_wechat(formatted_games)
```

**测试一下**：在终端运行 `python main.py`，看看是否成功输出了漂亮的表格！如果报错，检查网络或看报错信息（常见问题：NBA API偶尔抽风，多重试几次）。

---

## 阶段三：接入微信推送

### 3.1 获取 Server酱 Key
1. 浏览器打开 [sct.ftqq.com](https://sct.ftqq.com/)。
2. 微信扫码登录。
3. 复制你的 `SendKey`，填到之前创建的 `.env` 文件里。

### 3.2 编写推送逻辑 (`src/notifier.py`)

```python
import os
import requests
from dotenv import load_dotenv

# 加载 .env 文件
load_dotenv()

def send_to_wechat(formatted_games):
    # 通过 Server酱 推送到微信
    key = os.getenv("SERVERCHAN_KEY")
    if not key:
        print("未配置 SERVERCHAN_KEY，跳过微信推送。")
        return

    if isinstance(formatted_games, str):
        content = formatted_games
    else:
        content = "🏀 **今日NBA赛程**\n\n"
        for game in formatted_games:
            content += f"**{game['status']}** | {game['away']} {game['score']} {game['home']} | {game['time']}\n\n"

    url = f"https://sctapi.ftqq.com/{key}.send"
    data = {
        "title": "今日NBA赛程播报",
        "desp": content
    }
    
    try:
        response = requests.post(url, data=data)
        if response.status_code == 200:
            print("微信推送成功！")
        else:
            print(f"微信推送失败，状态码: {response.status_code}")
    except Exception as e:
        print(f"推送发生异常: {e}")
```

### 3.3 整合到 `main.py`
修改 `main.py` 的 `if __name__ == "__main__":` 部分，将 `notifier` 的导入和调用整合进去（参考上方 2.4 的完整 `main.py` 代码）。

再运行一次 `python main.py`，看看手机微信有没有收到通知！

---

## 阶段四：把项目上传到 GitHub

### 4.1 使用 GitHub Desktop
1. 打开 GitHub Desktop，点击 `File -> Add local repository`。
2. 选择你的 `NBA-Daily-Bot` 文件夹。
3. 它会提示你这不是一个Git仓库，点击 `create a repository`。
4. 在左侧填写项目简介，点击 `Create repository`。

### 4.2 提交与推送
1. 在 GitHub Desktop 左侧，你会看到所有改动的文件。
2. **注意**：确保 `.env` 和 `venv` 没有被勾选（这就是 `.gitignore` 的作用）。
3. 在左下角 Summary 填入 `feat: 初始化NBA赛程播报项目`，点击 `Commit to main`。
4. 点击右上角的 `Publish repository`，取消勾选 `Keep this code private`（如果你想让别人看到），点击发布。

### 4.3 完善 README.md
在 VS Code 里新建 `README.md`，这是你项目的门面。一定要包含：

- 项目名称和一句话简介
- 终端输出截图和微信推送截图（可以直接拖拽图片到GitHub网页的README编辑框里）
- 如何安装和运行（`pip install -r requirements.txt`，配置 `.env`，`python main.py`）
- MIT 开源协议声明

---

## 阶段五：终极进阶 —— 让 GitHub 帮你免费自动运行

你的电脑不可能24小时开机，但 GitHub 的服务器可以！我们可以用 **GitHub Actions** 让它每天早上8点自动跑一次并推送微信。

1. 在项目根目录新建文件夹 `.github/workflows`。
2. 在里面新建文件 `daily.yml`，写入：

```yaml
name: Daily NBA Schedule

on:
  schedule:
    # 每天北京时间早上8点运行 (UTC时间 0点)
    - cron: '0 0 * * *'
  workflow_dispatch: # 允许手动触发

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout code
        uses: actions/checkout@v3

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.10'

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Run script
        env:
          SERVERCHAN_KEY: ${{ secrets.SERVERCHAN_KEY }}
        run: python main.py
```

3. 去你的 GitHub 仓库网页，点击 `Settings -> Secrets and variables -> Actions`。
4. 点击 `New repository secret`，Name 填 `SERVERCHAN_KEY`，Value 填你的真实Key。
5. 把代码 Push 上去。GitHub 就会每天定时帮你运行了！

---

## 新手常见报错及解决思路

1. **`ModuleNotFoundError`**：忘了激活虚拟环境，或者忘了 `pip install`。
2. **`requests.exceptions.ConnectionError`**：网络问题，国内访问NBA API可能不稳定，可以尝试挂代理，或者换用国内的体育数据API。
3. **微信收不到推送**：检查 `.env` 里的Key有没有多余空格，或者去 Server酱 后台看看是否触发了频率限制。
4. **GitHub Actions 运行失败**：去仓库的 `Actions` 标签页，点进去看具体哪一步报错，通常是环境变量没配好。

---

这是一个非常完整的全栈小项目，涵盖了**API请求、数据处理、CLI美化、微信推送、Git版本控制、CI/CD自动化**。当你把它完整跑通并挂在GitHub上时，你的编程能力和简历含金量都会有一个质的飞跃。
