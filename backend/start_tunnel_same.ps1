$action = New-ScheduledTaskAction -Execute 'C:\Users\acer\OneDrive\Desktop\SVNIT New\backend\run_tunnel_same.bat'
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 12) -StartWhenAvailable
Register-ScheduledTask -TaskName 'VoxTunnelSame' -Action $action -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName 'VoxTunnelSame'
Write-Output 'SAME_URL_TUNNEL_STARTED'
