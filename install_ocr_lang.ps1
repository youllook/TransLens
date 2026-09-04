# 安裝 Windows 內建 OCR 的日文與英文語言包。沒有管理員權限時會自動跳 UAC 重新以管理員執行。
# 用法：install_ocr_lang.bat（或 powershell -ExecutionPolicy Bypass -File install_ocr_lang.ps1）
#       加 -DryRun 只顯示會做什麼，不安裝也不提權。
param(
    [switch]$DryRun,
    [string[]]$Languages = @("ja-JP", "en-US")
)
$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
# -File 模式下 "ja-JP,en-US" 會是單一字串，這裡統一拆成陣列
$Languages = @($Languages | ForEach-Object { $_ -split "," } | ForEach-Object { $_.Trim() } | Where-Object { $_ })

$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin -and -not $DryRun) {
    Write-Host "需要系統管理員權限，正在要求提權（UAC）..."
    $argList = @("-NoExit", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"",
                 "-Languages", ($Languages -join ","))
    try {
        Start-Process powershell -Verb RunAs -ArgumentList $argList
    } catch {
        Write-Host "提權被取消或失敗：$($_.Exception.Message)" -ForegroundColor Red
        exit 1
    }
    exit 0
}

Write-Host "=== TransLens：安裝 Windows OCR 語言包 ==="
Write-Host "透過 Windows Update 下載，每個語言約 1~3 分鐘，請勿關閉視窗。"
Write-Host ""
foreach ($lang in $Languages) {
    $cap = "Language.OCR~~~$lang~0.0.1.0"
    if ($DryRun) {
        Write-Host "[DryRun] 會執行：Add-WindowsCapability -Online -Name $cap"
        continue
    }
    Write-Host "安裝 $cap ..."
    try {
        $r = Add-WindowsCapability -Online -Name $cap
        Write-Host "  -> 完成（RestartNeeded=$($r.RestartNeeded)）" -ForegroundColor Green
    } catch {
        Write-Host "  -> 失敗：$($_.Exception.Message)" -ForegroundColor Red
        Write-Host "     常見原因：沒有網路、Windows Update 被停用或被公司政策管控。" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "目前已安裝的 OCR 語言："
if ($DryRun) {
    Write-Host "[DryRun] （略過查詢，需管理員）"
} else {
    Get-WindowsCapability -Online -Name "Language.OCR*" |
        Where-Object State -eq "Installed" |
        ForEach-Object { "  " + ($_.Name -replace "Language\.OCR~~~", "" -replace "~0\.0\.1\.0", "") }
}
Write-Host ""
Write-Host "完成。重新啟動 TransLens 後，⚙ → OCR 語言 會列出新語言；「自動」模式會自行挑最合適的。"
