# 电脑关机时继续推送：GitHub Actions 配置

本项目已经包含 `.github/workflows/daily-job-push.yml`。它会在每天北京时间 17:07 自动抓取岗位、发布完整 HTML 报告，并通过 WxPusher 发送一次日报。即使当天新增为 0，也会发送消息和完整报告链接。电脑可以处于关机状态。

## 1. 确认仓库与 GitHub Pages 权限

GitHub Free 只允许从 Public 仓库使用 GitHub Pages；Private 仓库需要 GitHub Pro、Team 或 Enterprise。你可以选择：

- 使用 GitHub Free：把当前仓库改为 **Public**。报告和代码会公开，但 SPT 仍安全地保存在 Secret 中。
- 保持 **Private**：账户需具备 GitHub Pro 或相应团队计划。

GitHub Pages 网站本身是公开访问的。项目只把公开岗位报告部署到网站，不会写入 SPT。

## 2. 创建新的 WxPusher SPT

此前用于聊天测试的 SPT 已经出现在对话中，不应继续作为长期密钥。请在 WxPusher 中重置并获取新的 SPT。

## 3. 把 SPT 保存为 GitHub Secret

1. 打开仓库的 **Settings**。
2. 进入 **Secrets and variables → Actions**。
3. 点击 **New repository secret**。
4. Name 填写 `WXPUSHER_SPT`。
5. Secret 填写新的完整 `SPT_...`，保存。

工作流只通过 `${{ secrets.WXPUSHER_SPT }}` 环境变量读取令牌，不会把它写入报告、缓存或日志。

## 4. 启用完整报告网页

1. 打开仓库的 **Settings**。
2. 左侧进入 **Pages**。
3. 在 **Build and deployment → Source** 中选择 **GitHub Actions**。

网页地址通常为：

```text
https://你的GitHub用户名.github.io/仓库名/
```

工作流会自动读取实际部署地址，不需要手工把网址写进代码。

## 5. 手动测试云端运行

1. 打开仓库的 **Actions**。
2. 选择 **Daily biostatistics job push**。
3. 点击 **Run workflow**。
4. 确认分支为 `main` 后运行。
5. 等工作流完成后检查 WxPusher App；消息应包含当日新增数量和“点击查看今日完整岗位报告”。手动运行会作为一次正式日报，并把本轮新增项目标记为已发送。

## 6. 查看报告和故障信息

每次运行结束后，工作流页面的 **Artifacts** 区域会出现 `job-report-运行编号`，保存 30 天，包含：

- `latest_report.html`
- 最新中国岗位 CSV
- 最新机器运行报告 JSON

SQLite 历史库和待推送队列通过 GitHub Actions Cache 在不同运行之间保存。缓存每天都会被访问，因此正常每日运行时不会触发“超过 7 天未访问”的清理规则。

报告网页每次运行都会覆盖更新为最新版；WxPusher 中的链接保持不变。Actions 页面中的 Artifact 是额外的 30 天备份，不是微信消息所使用的网址。

## 时间说明

工作流使用：

```yaml
cron: "7 17 * * *"
timezone: "Asia/Shanghai"
```

也就是每天北京时间 17:07。GitHub 定时任务不是硬实时系统，高负载时仍可能延迟数分钟。
