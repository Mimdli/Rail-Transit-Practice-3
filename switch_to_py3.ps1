# 切换到 Python 3 — 以管理员身份运行此脚本
# 使用方法：右键 → "以管理员身份运行"

$sysPath = [Environment]::GetEnvironmentVariable("Path", "Machine")

# 移除旧的 Anaconda 路径
$sysPath = $sysPath -replace "C:\\Anaconda\\Scripts;", ""
$sysPath = $sysPath -replace "C:\\Anaconda;", ""

# 把 Python 3.8 放到 Anaconda 前面
$newPath = "F:\copy\Scripts;F:\copy;C:\Anaconda\Scripts;C:\Anaconda;" + $sysPath

# 写入系统 PATH
[Environment]::SetEnvironmentVariable("Path", $newPath, "Machine")

Write-Host "✅ PATH 修改成功！"
Write-Host ""
Write-Host "请关闭所有终端后重新打开，运行以下命令验证："
Write-Host "  python --version"
Write-Host ""
Write-Host "预期输出：Python 3.8.0"

# 同时更新当前会话的 PATH
$env:Path = "F:\copy\Scripts;F:\copy;" + $env:Path

Write-Host "当前 Python 版本："
python --version

pause
