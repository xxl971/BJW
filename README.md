
## 公司池

- 14 家跨国药企：AstraZeneca、Amgen、Roche、Gilead、MSD、BMS、Pfizer、Lilly、Novartis、Sanofi、AbbVie、J&J、Takeda、Boehringer Ingelheim
- 5 家 CRO：IQVIA、ICON、Parexel、Fortrea、Thermo Fisher / PPD
- 11 家国内药企：恒瑞、百济神州、信达、君实、再鼎、康方、和黄、中国生物制药/正大天晴、翰森、科伦、康弘

`monitor_mode=html` 的站点会自动抓取并翻页；`monitor_mode=portal` 的站点在报告里提供官方入口，等待后续增加专用适配器。程序不会把仅有官网入口的公司误报成“自动抓取成功”。

## 运行环境

- Python 3.10 或更高版本
- 不需要安装第三方包

## 在 PyCharm 中运行

1. 解压项目。
2. 在 PyCharm 中打开 `job_watcher_mvp` 文件夹。
3. 直接运行 `main.py`。

也可以在 Terminal 中运行：

```bash
python main.py
```

结果写入 `output` 文件夹：

- `job_report_时间戳.html`：成都、中国正式岗位；当前开放实习；历史实习截止日期；30 家公司监控状态
- `latest_report.html`：最近一次报告的固定文件名
- `jobs_all_时间戳.csv`：全球抓到并通过初筛的岗位
- `jobs_china_时间戳.csv`：中国岗位
- `run_report_时间戳.json`：每家公司成功、警告或失败的具体原因

历史与待推送数据保存在 `data/job_history.sqlite3`。第一次运行会使用最近一次 CSV 初始化历史库，避免把已有岗位全部标成今日新增；之后首次出现的中国正式岗位和中国实习会进入待推送池。报告顶部显示今日新增数量，新岗位使用红色背景和 `NEW` 标记，旧岗位单独列出。

默认每个自动源最多抓取 10 页，并额外访问 China / Chengdu 定向入口。临时改变页数：

```bash
python main.py --max-pages 20
```

先验证不联网的完整流程：

```bash
python main.py --offline-demo
```

运行测试：

```bash
python -m unittest -v
```

## WxPusher 首次测试

个人推送推荐使用 WxPusher 的极简推送 SPT，不需要提供微信号或微信密码：

1. 打开 WxPusher 官方 SPT 文档并扫码取得以 `SPT_` 开头的令牌：<https://wxpusher.zjiecode.com/docs/spt.html>
2. 将 `.env.local.example` 复制为 `.env.local`。
3. 把 `.env.local` 中的示例值替换为自己的 SPT。真实 SPT 不要上传、截图或提交到仓库。
4. 发送一次预览消息：

```bash
python main.py --test-push
```

测试推送会发送当前排名靠前的中国岗位和中国实习，不会修改正式推送队列。手动生成报告并发送当天日报：

```bash
python main.py --push
```

`--push` 每次都会发送消息。当天没有新增岗位时会发送“今日新增 0 个”，并附完整报告链接（如环境变量 `REPORT_URL` 已配置）。

## 每天北京时间 17:07 自动运行（Windows）

在 PowerShell 中进入项目目录后执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_windows_task.ps1
```

脚本会创建名为 `BiostatisticsJobWatcher` 的每日任务。电脑在 17:07 关机或休眠时无法准点抓取；`StartWhenAvailable` 会让系统在下次可用时补跑。如果需要电脑关闭时仍能准时发送，应使用下面的 GitHub Actions 云端方案。

## 电脑关机时自动运行（推荐）

项目内置 GitHub Actions 工作流：

```text
.github/workflows/daily-job-push.yml
```

它会在每天北京时间 17:07 运行，使用 GitHub Secret 读取 `WXPUSHER_SPT`，并通过缓存保存 SQLite 历史库和推送队列。每次运行会把 `latest_report.html` 部署到 GitHub Pages，然后无论新增数量是否为 0 都发送一次 WxPusher 日报；消息中的“点击查看今日完整岗位报告”指向最新网页。完整配置步骤见 [`GITHUB_ACTIONS_SETUP.md`](GITHUB_ACTIONS_SETUP.md)。

## 如何理解第一轮结果

- `ok`：页面成功返回，而且解析到了符合条件的岗位。
- `warning`：页面成功返回，但未解析到岗位。常见原因是网站改版、依赖 JavaScript，或当前没有匹配岗位。
- `failed`：网络、TLS、超时、403/429 或其他 HTTP 问题。
- `portal`：已纳入 30 家公司池并提供官方招聘链接，但未使用通用解析器自动抓取。

第一版故意只读取搜索结果页，不高频访问每个职位详情页，以减少对招聘网站的请求。确认哪些来源稳定后，再为各网站增加专用解析器、详情页字段和 SQLite 去重。

## 已知限制

- 通用解析器主要识别 URL 中带 `/job/` 或 `/jobs/` 的链接；网站改版后可能需要调整。
- `title` 暂时保留招聘页链接中的完整文本，地点和发布日期尚未可靠拆分。
- 地点识别目前根据搜索结果文字和岗位 URL 完成；少数网站若不在搜索页显示地点，需要在下一版读取 JD。
- 当前实习清单采用“经核实的策展数据 + 自动源”的务实方式；部分海外岗位有工作许可限制。
- 中国实习同时使用公司官网和招聘平台近期页面。报告会区分“官网开放”“官网长期招聘”“近期发布·投递前复核”，不会将第三方页面一律视为官网确认。
- GitHub Pages 报告属于公开网页，即使源仓库为 Private 也不应在报告中放个人信息或密钥。本项目只发布公开岗位信息，不会把 `WXPUSHER_SPT` 写入网页。
