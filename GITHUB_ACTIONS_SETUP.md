# 电脑关机时继续推送：GitHub Actions 配置

本项目已经包含 `.github/workflows/daily-job-push.yml`。它会在每天北京时间 12:07 自动抓取岗位，并仅在发现新中国正式岗位或新中国实习时通过 WxPusher 推送。电脑可以处于关机状态。

## 1. 创建私有仓库

1. 登录 GitHub，点击 **New repository**。
2. 仓库可命名为 `biostatistics-job-watcher`。
3. 选择 **Private**，不要把 SPT 放进仓库。
4. 创建仓库后，把解压得到的 `job_watcher_mvp` 文件夹内全部内容上传到仓库根目录。上传后仓库根目录应直接看到 `main.py`、`companies.json` 和 `.github`。

## 2. 创建新的 WxPusher SPT

此前用于聊天测试的 SPT 已经出现在对话中，不应继续作为长期密钥。请在 WxPusher 中重置并获取新的 SPT。

## 3. 把 SPT 保存为 GitHub Secret

1. 打开仓库的 **Settings**。
2. 进入 **Secrets and variables → Actions**。
3. 点击 **New repository secret**。
4. Name 填写 `WXPUSHER_SPT`。
5. Secret 填写新的完整 `SPT_...`，保存。

工作流只通过 `${{ secrets.WXPUSHER_SPT }}` 环境变量读取令牌，不会把它写入报告、缓存或日志。

## 4. 手动测试云端运行

1. 打开仓库的 **Actions**。
2. 选择 **Daily biostatistics job push**。
3. 点击 **Run workflow**。
4. 保持“发送预览”为启用状态并运行。
5. 等工作流完成后检查 WxPusher App。预览不会把正式待推送队列标记为已发送。

## 5. 查看报告和故障信息

每次运行结束后，工作流页面的 **Artifacts** 区域会出现 `job-report-运行编号`，保存 30 天，包含：

- `latest_report.html`
- 最新中国岗位 CSV
- 最新机器运行报告 JSON

SQLite 历史库和待推送队列通过 GitHub Actions Cache 在不同运行之间保存。缓存每天都会被访问，因此正常每日运行时不会触发“超过 7 天未访问”的清理规则。

## 时间说明

工作流使用：

```yaml
cron: "7 12 * * *"
timezone: "Asia/Shanghai"
```

也就是每天北京时间 12:07。GitHub 定时任务不是硬实时系统，高负载时仍可能延迟数分钟。选择 12:07 是为了避开整点高负载。
