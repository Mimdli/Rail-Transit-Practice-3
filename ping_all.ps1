# Rail Transit System - Network Connectivity Check
Clear-Host
Write-Host "============================================"
Write-Host "  Rail Transit System - Ping All Interfaces"
Write-Host "  Press any key to start..."
Write-Host "============================================"
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")

$targets = @(
    @{IP="192.168.200.102"; Name="Main-Control (192.168.200.102)"}
    @{IP="192.168.200.110"; Name="Vehicle-Sim (192.168.200.110)"}
    @{IP="192.168.200.106"; Name="Signal-Dev1 (192.168.200.106)"}
    @{IP="9.38.1.2";       Name="Signal-Dev2 (9.38.1.2)"}
    @{IP="9.38.1.242";     Name="Signal-Gateway2 (9.38.1.242)"}
    @{IP="9.38.1.3";       Name="Signal-Dev3 (9.38.1.3)"}
    @{IP="9.38.1.243";     Name="Signal-Gateway3 (9.38.1.243)"}
    @{IP="192.168.100.123"; Name="PLC-Device (192.168.100.123)"}
    @{IP="192.168.100.121"; Name="Network-Screen (192.168.100.121)"}
    @{IP="192.168.100.122"; Name="Signal-Screen (192.168.100.122)"}
    @{IP="192.168.100.124"; Name="Vision-System (192.168.100.124)"}
)

$total = $targets.Count
$ok = 0
$ng = 0
$i = 0

foreach ($t in $targets) {
    $i++
    Write-Host ""
    Write-Host "[$i] $($t.Name)" -ForegroundColor Cyan
    $result = Test-Connection -ComputerName $t.IP -Count 2 -Quiet -ErrorAction SilentlyContinue
    if ($result) {
        Write-Host "  OK" -ForegroundColor Green
        $ok++
    } else {
        Write-Host "  FAIL" -ForegroundColor Red
        $ng++
    }
}

Write-Host ""
Write-Host "============================================"
Write-Host "  Done: $total total, $ok OK, $ng FAIL"
Write-Host "============================================"
Write-Host ""
Write-Host "Note: FAIL may due to:"
Write-Host "  - Device not powered on or not connected"
Write-Host "  - 9.38.x.x requires dedicated network"
Write-Host "  - Firewall blocking ICMP (ping)"
Write-Host ""
Write-Host "Press any key to exit..."
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
