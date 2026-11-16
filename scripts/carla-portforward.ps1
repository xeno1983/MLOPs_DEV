# 管理者不要: 127.0.0.1:2000/2001 → WSL の CARLA へ転送
#   .\scripts\carla-portforward.ps1
param(
    [string]$WslDistro = "Ubuntu",
    [int[]]$Ports = @(2000, 2001)
)

$ErrorActionPreference = "Stop"
$wslIp = ((wsl -d $WslDistro -- hostname -I).Trim() -split '\s+')[0]
if (-not $wslIp) { throw "WSL IP を取得できません" }
Write-Host "Forward 127.0.0.1 -> ${wslIp} ports $($Ports -join ',')"

$script = {
    param($ListenPort, $TargetHost, $TargetPort)
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $ListenPort)
    $listener.Start()
    while ($true) {
        try {
            $client = $listener.AcceptTcpClient()
            $remote = New-Object System.Net.Sockets.TcpClient
            $remote.Connect($TargetHost, $TargetPort)
            $cStream = $client.GetStream()
            $rStream = $remote.GetStream()
            $buf1 = New-Object byte[] 8192
            $buf2 = New-Object byte[] 8192
            while ($client.Connected -and $remote.Connected) {
                if ($cStream.DataAvailable) {
                    $n = $cStream.Read($buf1, 0, $buf1.Length)
                    if ($n -le 0) { break }
                    $rStream.Write($buf1, 0, $n)
                }
                if ($rStream.DataAvailable) {
                    $n = $rStream.Read($buf2, 0, $buf2.Length)
                    if ($n -le 0) { break }
                    $cStream.Write($buf2, 0, $n)
                }
                Start-Sleep -Milliseconds 1
            }
            $client.Close(); $remote.Close()
        } catch {
            Start-Sleep -Milliseconds 200
        }
    }
}

Get-Job -Name "carla-fwd-*" -ErrorAction SilentlyContinue | Stop-Job -PassThru | Remove-Job -Force -ErrorAction SilentlyContinue
foreach ($p in $Ports) {
    Start-Job -Name "carla-fwd-$p" -ScriptBlock $script -ArgumentList $p, $wslIp, $p | Out-Null
    Write-Host "started job carla-fwd-$p"
}
Write-Host "Keep this window open, or jobs die when the session ends."
Write-Host "Test: Test-NetConnection 127.0.0.1 -Port 2000"
Receive-Job -Name "carla-fwd-*" -Wait
