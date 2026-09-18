param(
    [string]$TaskName = "BiostatisticsJobWatcher",
    [string]$DailyTime = "17:07"
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$MainScript = Join-Path $ProjectDir "main.py"
$PythonCommand = Get-Command py -ErrorAction SilentlyContinue

if ($null -ne $PythonCommand) {
    $Executable = $PythonCommand.Source
    $Arguments = "-3 `"$MainScript`" --push"
} else {
    $PythonCommand = Get-Command python -ErrorAction Stop
    $Executable = $PythonCommand.Source
    $Arguments = "`"$MainScript`" --push"
}

$Action = New-ScheduledTaskAction `
    -Execute $Executable `
    -Argument $Arguments `
    -WorkingDirectory $ProjectDir
$Trigger = New-ScheduledTaskTrigger -Daily -At $DailyTime
$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "每天北京时间17点07分抓取并推送生物统计岗位" `
    -Force | Out-Null

Write-Host "已创建任务 $TaskName，每天 $DailyTime 运行。"
Write-Host "请确保本机时区为北京时间，并已在 .env.local 配置 WXPUSHER_SPT。"
