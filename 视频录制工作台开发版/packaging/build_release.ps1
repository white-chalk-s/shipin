param(
    [string]$Iscc = '',
    [string]$VendorDirectory = '',
    [string]$Version = '1.0.0'
)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $projectRoot
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $python)) { throw '缺少构建虚拟环境。' }
if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw '版本号必须为数字形式，例如 1.0.0。' }
if (-not $Iscc) {
    $cmd = Get-Command 'ISCC.exe' -ErrorAction SilentlyContinue
    if ($cmd) { $Iscc = $cmd.Source }
    else {
        $candidate = Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'
        if (Test-Path -LiteralPath $candidate) { $Iscc = $candidate }
    }
}
if (-not $Iscc -or !(Test-Path -LiteralPath $Iscc -PathType Leaf)) { throw '缺少 Inno Setup 6 的 ISCC.exe；无法构建安装包。' }
if (-not $VendorDirectory) { $VendorDirectory = Join-Path $projectRoot 'vendor\ffmpeg' }
if (!(Test-Path -LiteralPath $VendorDirectory -PathType Container)) { throw '缺少已审核的编码器目录 vendor\ffmpeg。' }
$vendor = (Resolve-Path -LiteralPath $VendorDirectory).Path
$provenancePath = Join-Path $vendor 'PROVENANCE.json'
$noticesPath = Join-Path $vendor 'THIRD_PARTY_NOTICES.md'
if (!(Test-Path -LiteralPath $provenancePath -PathType Leaf) -or !(Test-Path -LiteralPath $noticesPath -PathType Leaf)) { throw '缺少编码器来源、许可及审核记录，拒绝构建安装包。' }
$provenance = Get-Content -LiteralPath $provenancePath -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not $provenance.source_url -or -not $provenance.license -or -not $provenance.reviewed_by -or -not $provenance.reviewed_at) { throw '编码器审核记录不完整。' }
if ((Get-Item -LiteralPath $noticesPath).Length -eq 0) { throw '第三方许可说明为空。' }
foreach ($name in @('ffmpeg.exe', 'ffprobe.exe')) {
    $binary = Join-Path $vendor $name
    if (!(Test-Path -LiteralPath $binary -PathType Leaf)) { throw "编码器文件缺失：$name" }
    $expected = $provenance.sha256.$name
    if (-not $expected -or (Get-FileHash -LiteralPath $binary -Algorithm SHA256).Hash -ne $expected) { throw "编码器校验不通过：$name" }
}
$encoders = @(& (Join-Path $vendor 'ffmpeg.exe') -hide_banner -encoders 2>&1)
if ($LASTEXITCODE -ne 0 -or -not ($encoders -match '\blibx264\b')) { throw '已审核的 FFmpeg 不具备 libx264 编码能力。' }
& (Join-Path $vendor 'ffprobe.exe') -version | Out-Null
if ($LASTEXITCODE -ne 0) { throw '已审核的 ffprobe 无法运行。' }
$expectedPackages = @(Get-Content -LiteralPath (Join-Path $projectRoot 'requirements.lock.txt') -Encoding UTF8 | Where-Object { $_ -and !$_.StartsWith('#') } | ForEach-Object { $_.Trim().ToLowerInvariant() } | Sort-Object)
$actualPackages = @(& $python -m pip freeze | ForEach-Object { $_.Trim().ToLowerInvariant() } | Sort-Object)
if ($LASTEXITCODE -ne 0 -or (Compare-Object $expectedPackages $actualPackages)) { throw '构建环境与依赖锁定版本不一致。' }
& $python -m pip check
if ($LASTEXITCODE -ne 0) { throw '构建环境依赖检查失败。' }
$previousPath = $env:PATH
try {
    $env:PATH = "$vendor;$previousPath"
    & $python -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { throw '自动化测试失败。' }
} finally {
    $env:PATH = $previousPath
}
$buildRoot = Join-Path $projectRoot ('artifacts\release-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0,6))
& (Join-Path $PSScriptRoot 'build_dev.ps1') -VendorDirectory $vendor -BuildRoot $buildRoot
$sourceDir = Join-Path $buildRoot 'dist\VideoCaptureWorkbench'
$outputDir = Join-Path $buildRoot 'installer'
New-Item -ItemType Directory -Path $outputDir | Out-Null
& $Iscc "/DSourceDir=$sourceDir" "/DOutputDir=$outputDir" "/DAppVersion=$Version" (Join-Path $PSScriptRoot 'VideoCaptureWorkbench.iss')
if ($LASTEXITCODE -ne 0) { throw 'Inno Setup 编译失败。' }
$installer = Join-Path $outputDir "视频录制工作台_Setup_$Version.exe"
if (!(Test-Path -LiteralPath $installer)) { throw '未找到安装包。' }
Get-FileHash -LiteralPath $installer -Algorithm SHA256 | Format-List
Write-Host "已生成候选安装包：$installer；必须完成干净 Windows 机器上的安装、升级、卸载及离线验收。"
