$action = New-ScheduledTaskAction -Execute 'C:\Users\acer\OneDrive\Desktop\SVNIT New\backend\run_tunnel.bat'
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 12) -StartWhenAvailable
Register-ScheduledTask -TaskName 'VoxTunnel' -Action $action -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName 'VoxTunnel'
Write-Output 'TUNNEL_TASK_STARTED'
