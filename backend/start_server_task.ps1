param([string]$TaskName = 'VoxServer', [string]$BatPath = 'C:\Users\acer\OneDrive\Desktop\SVNIT New\backend\run_server.bat')
$action = New-ScheduledTaskAction -Execute $BatPath
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 12) -StartWhenAvailable
Register-ScheduledTask -TaskName $TaskName -Action $action -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Write-Output "TASK_STARTED: $TaskName"
