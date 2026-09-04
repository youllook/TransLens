# 在桌面建立「TransLens」快捷：以 pythonw 無主控台啟動 translens.py，使用 assets\translens.ico 圖示。
# 用法：powershell -ExecutionPolicy Bypass -File make_shortcut.ps1 [-Python <pythonw.exe 路徑>]
param(
    [string]$Python = ""
)
$ErrorActionPreference = "Stop"
$app = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not $Python) {
    # 優先用目前 PATH 上 python 對應的 pythonw（套件裝在同一個環境）
    $py = (Get-Command python -ErrorAction SilentlyContinue).Source
    if ($py) { $Python = Join-Path (Split-Path $py) "pythonw.exe" }
    if (-not $Python -or -not (Test-Path $Python)) { $Python = "pythonw.exe" }
}

$desktop = [Environment]::GetFolderPath("Desktop")
$lnk = Join-Path $desktop "TransLens.lnk"
$shell = New-Object -ComObject WScript.Shell
$sc = $shell.CreateShortcut($lnk)
$sc.TargetPath = $Python
$sc.Arguments = '"' + (Join-Path $app "translens.py") + '"'
$sc.WorkingDirectory = $app
$sc.IconLocation = (Join-Path $app "assets\translens.ico") + ",0"
$sc.Description = "TransLens 桌面透鏡翻譯框"
$sc.WindowStyle = 7   # 最小化啟動（pythonw 本來就沒有視窗）
$sc.Save()
Write-Host "已建立: $lnk"
Write-Host "目標: $Python"
