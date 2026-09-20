# Biostatistics Job Watcher MVP

这是一个面向生物统计求职的可运行项目。公司池有 94 家（14 家跨国药企、5 家跨国 CRO、55 家国内药企、20 家国内 CRO）。其中 7 家配置了尝试自动抓取，31 家记录了招聘入口，另外 56 家尚未补充招聘入口；真实抓取成功数量以每次报告为准。报告展示成都、中国正式岗位、中国实习、海外实习、历史实习截止日期，以及猎聘、智联招聘、LinkedIn、实习僧、松鼠实习的人工定向检索入口。岗位、实习、公司来源表显示公司性质（跨国药企、跨国CRO、国内药企、国内CRO、其他）。SQLite 历史库区分首次发现与已见过的岗位；GitHub Pages 可以托管报告，WxPusher 每天发送汇总和报告链接。自动运行时间取决于你在 GitHub 仓库默认分支上实际使用的工作流。

## 公司池

- 14 家跨国药企：AstraZeneca、Amgen、Roche、Gilead、MSD、BMS、Pfizer、Lilly、Novartis、Sanofi、AbbVie、J&J、Takeda、Boehringer Ingelheim
- 5 家跨国 CRO：IQVIA、ICON、Parexel、Fortrea、Thermo Fisher / PPD
- 11 家国内药企：恒瑞、百济神州、信达、君实、再鼎、康方、和黄、中国生物制药/正大天晴、翰森、科伦、康弘

新增公司及出处见 [`companies.json`](companies.json)：以中国医药工业信息中心发布的 [2025 年主营业务收入前 100 位](https://www.cnpp.cn/focus/3560969.html) 为母榜，按原榜顺序选择 50 家中国主体的制药、药品研发和医药制造集团，排除外资子公司、医疗器械和纯服务企业；保留原池中 5 家不在该筛选名单内的国内药企，因此国内药企共 55 家。`rank` 记录**原百强榜名次**，不是一份官方另行公布的“国内药企前 50”榜单。国内 CRO 的 20 家按[药智网 2025 年中国医药 CRO 企业 20 强](https://top.yaozh.com/Ranking/index/tag/15/year/2025.html)录入；该榜按研发实力评定，包含临床前及其他研发服务公司，不代表营收或生物统计职位数量。两张榜单的名次不可比较。

`monitor_mode=html` 的站点会自动抓取并翻页；`monitor_mode=portal` 的站点在报告里提供招聘入口，等待后续增加专用适配器；`monitor_mode=pending` 的新公司仅加入候选名单，`url` 留空，报告标记“待补充入口”，**不会被抓取，也不会产生该公司的新岗位推送**。收到由用户提供的招聘网址再改成 `portal`；配好解析器并核实翻页后才改成 `html`。公司可配置 `channels`，分别保存社招、校招、实习以及其他地区等多条链接；报告会一并展示。例如罗氏中国实习、数字化与研发及 Genentech 美国实习；齐鲁、复星、正大天晴、上海医药、三生制药的社招、校招及实习入口；其他已提交入口均保存在公司池中。恒瑞的松鼠实习公司页标明为第三方，修正药业的 Bing 检索链接放入 `discovery_urls`，保留 `pending` 状态。入口已经记录不等于已核实岗位仍在开放，也不等于完成自动采集。

罗氏官网入口使用中国大陆招聘页 `https://careers.roche.com/cn/zh/mainland-china-jobs`；该页目前通过浏览器脚本加载结果，仍属于 `portal`，不会自动入库。已有的其余入口和新公司的招聘链接仍需逐家核对详情字段、翻页方式和访问条件。

城市识别优先使用职位明确给出的 `data-location` / 招聘卡片地点 / JSON-LD `jobLocation` 字段；只有页面没有地点时才参考标题和职位链接。已覆盖成都、主要国内城市和多个四川城市；不能可靠识别但职位页写有中文地点的记录显示在“地点待核实”列表里，不计入中国新增推送。不会因为公司来自中国站点就自动认定为中国岗位。职位名称的中文检索词包括生物统计、医学统计、统计科学家、统计编程、临床统计和生物信息。

报告末尾的猎聘、智联招聘、LinkedIn、实习僧、松鼠实习入口是按照平台域名和职位关键词生成的定向搜索链接，属于**人工复核**，不是已验证的开放岗位或自动抓取数据。松鼠实习主页和恒瑞在该站的公司页提供直接入口，第三方聚合岗位仍要到招聘方页面核实。搜索引擎可能收录早已下线的职位；请先打开具体职位页核对城市、发布时间、投递状态及截止日期。当前不会把这些入口计入“今日新增”或 WxPusher 推送。要把平台职位自动入库，需要可稳定访问且允许使用的检索数据源，并单独实现职位状态校验。不同来源 URL 对应的同一岗位保留两条记录和两条原始链接，新增计数按来源记录统计；校招不自动视为实习。

## 在 GitHub 更新现有仓库

把更新包**解压**，将其中的 `main.py`、`companies.json`、`test_main.py`、`README.md` 和 `COMPANY_POOL.md` 五个文件上传到仓库根目录，选择覆盖同名文件并提交到默认分支。不要直接上传 zip 文件：GitHub 不会帮你自动解压。更新包没有 `.github/workflows/`、`internships.json`、`data/` 或 `output/`，不会替换你已经调好的定时工作流、实习记录或推送历史。上传后可在 Actions 里手动运行工作流，并打开 HTML 报告检查新入口；新增 `portal` 公司只会出现链接，尚不会自动贡献岗位数量。

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

- `job_report_时间戳.html`：成都、中国正式岗位；当前开放实习；历史实习截止日期；94 家公司监控与待补充状态
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

实际自动运行时间以你 GitHub 仓库默认分支中该工作流的 `schedule` 配置及 GitHub Actions 的实际调度为准。本次更新不包含工作流文件，因此不会更改目前已设定的运行时间。工作流使用 GitHub Secret 读取 `WXPUSHER_SPT`，并通过缓存保存 SQLite 历史库和推送队列；每次运行会把 `latest_report.html` 部署到 GitHub Pages，然后无论新增数量是否为 0 都发送一次 WxPusher 日报。消息中的“点击查看今日完整岗位报告”指向最新网页。配置步骤见 [`GITHUB_ACTIONS_SETUP.md`](GITHUB_ACTIONS_SETUP.md)，以仓库中现用工作流为准。

## 如何理解第一轮结果

- `ok`：页面成功返回，而且解析到了符合条件的岗位。
- `warning`：页面成功返回，但未解析到岗位。常见原因是网站改版、依赖 JavaScript，或当前没有匹配岗位。
- `failed`：网络、TLS、超时、403/429 或其他 HTTP 问题。
- `portal`：提供招聘入口，但未使用通用解析器自动抓取。
- `pending`：已列入候选池但官网招聘链接尚未核实，未自动抓取。

第一版故意只读取搜索结果页，不高频访问每个职位详情页，以减少对招聘网站的请求。确认哪些来源稳定后，再为各网站增加专用解析器、详情页字段和 SQLite 去重。

## 已知限制

- 通用解析器主要识别 URL 中带 `/job/` 或 `/jobs/` 的链接；网站改版后可能需要调整。
- `title` 暂时保留招聘页链接中的完整文本，地点和发布日期尚未可靠拆分。
- 地点识别目前根据搜索结果文字和岗位 URL 完成；少数网站若不在搜索页显示地点，需要在下一版读取 JD。
- 当前实习清单采用“经核实的策展数据 + 自动源”的务实方式；部分海外岗位有工作许可限制。
- 中国实习同时使用公司官网和招聘平台近期页面。报告会区分“官网开放”“官网长期招聘”“近期发布·投递前复核”，不会将第三方页面一律视为官网确认。
- GitHub Pages 报告属于公开网页，即使源仓库为 Private 也不应在报告中放个人信息或密钥。本项目只发布公开岗位信息，不会把 `WXPUSHER_SPT` 写入网页。
