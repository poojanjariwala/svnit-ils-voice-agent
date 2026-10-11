$action = New-ScheduledTaskAction -Execute 'C:\Users\acer\OneDrive\Desktop\SVNIT New\backend\run_call.bat'
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 1) -StartWhenAvailable
Register-ScheduledTask -TaskName 'VoxCall' -Action $action -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName 'VoxCall'
Write-Output 'TASK_STARTED'
