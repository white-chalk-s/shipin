param(
    [string]$VendorDirectory = "",
    [string]$BuildRoot = ""
)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $projectRoot
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $python)) { throw '找不到 .venv，请先按 README 创建开发环境。' }
if (-not $BuildRoot) { $BuildRoot = Join-Path $projectRoot ('artifacts\dev-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0,6)) }
if (Test-Path -LiteralPath $BuildRoot) { throw "构建目录已存在，未覆盖：$BuildRoot" }
New-Item -ItemType Directory -Path $BuildRoot | Out-Null
$BuildRoot = (Resolve-Path -LiteralPath $BuildRoot).Path
$distPath = Join-Path $BuildRoot 'dist'
$workPath = Join-Path $BuildRoot 'work'
$specPath = Join-Path $BuildRoot 'spec'
New-Item -ItemType Directory -Path $specPath | Out-Null
$argsList = @('-m','PyInstaller','--noconfirm','--clean','--windowed','--onedir', '--name','VideoCaptureWorkbench', '--distpath',$distPath,'--workpath',$workPath,'--specpath',$specPath, '--collect-submodules','pynput')
if ($VendorDirectory) {
    $vendor = (Resolve-Path -LiteralPath $VendorDirectory).Path
    $argsList += @('--add-data', "$vendor;vendor/ffmpeg")
}
$argsList += (Join-Path $projectRoot 'region_recorder.py')
& $python @argsList
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 构建失败，详情见上方日志；构建目录：$BuildRoot" }
$exe = Join-Path $distPath 'VideoCaptureWorkbench\VideoCaptureWorkbench.exe'
if (!(Test-Path -LiteralPath $exe)) { throw "主程序不存在：$exe" }
# PyInstaller may collect a version-suffixed ICU DLL from the Python environment.
# This Qt build imports the unversioned Windows ICU API; the collected DLL shadows
# System32 and causes error 127. Keep the excluded file in the build work folder.
$collectedIcu = Join-Path $distPath 'VideoCaptureWorkbench\_internal\icuuc.dll'
if (Test-Path -LiteralPath $collectedIcu) {
    $systemIcu = Join-Path $env:SystemRoot 'System32\icuuc.dll'
    if (!(Test-Path -LiteralPath $systemIcu -PathType Leaf)) { throw '缺少 Windows 系统 ICU，无法验证 Qt 启动。' }
    Move-Item -LiteralPath $collectedIcu -Destination (Join-Path $workPath 'excluded-icuuc.dll')
}
$result = Join-Path $BuildRoot 'smoke-result.json'
$process = Start-Process -FilePath $exe -ArgumentList @('--build-smoke-test', ('"' + $result + '"')) -WindowStyle Hidden -PassThru
if (-not $process.WaitForExit(45000)) {
    Stop-Process -Id $process.Id -Force
    throw "启动检查超时；构建目录：$BuildRoot"
}
if ($process.ExitCode -ne 0 -or !(Test-Path -LiteralPath $result)) { throw "启动检查失败（退出码 $($process.ExitCode)）；结果：$result" }
$report = Get-Content -LiteralPath $result -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not $report.ok -or -not $report.screen) { throw "启动检查失败：$result" }
Write-Host "开发构建和隔离启动检查通过：$exe"
Write-Host "本构建仅供开发验证，不能作为正式安装包发布。"
