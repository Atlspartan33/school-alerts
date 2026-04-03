$taskName = "SchoolEmailAlerts"
$pythonExe = "C:\Users\Terre\OneDrive\Documents\Family Management\school-alerts\.venv\Scripts\python.exe"
$scriptPath = "C:\Users\Terre\OneDrive\Documents\Family Management\school-alerts\main.py"
$workDir = "C:\Users\Terre\OneDrive\Documents\Family Management\school-alerts"

# Remove existing task if present
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue

# Use pythonw.exe so no console window pops up
$pythonW = $pythonExe -replace 'python\.exe$', 'pythonw.exe'
$action = New-ScheduledTaskAction -Execute $pythonW -Argument "`"$scriptPath`"" -WorkingDirectory $workDir

# Trigger: daily at 6:00 AM, repeating every 5 minutes for 18 hours (covers 6 AM - midnight)
# This resets every day, so sleep/wake cycles don't break the repetition.
$trigger = New-ScheduledTaskTrigger -Daily -At "6:00AM" -DaysInterval 1
$trigger.Repetition.Interval = "PT5M"
$trigger.Repetition.Duration = "PT18H"
$trigger.Repetition.StopAtDurationEnd = $false

# Settings: 2 min execution limit, run on battery, start if missed
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 2) `
    -MultipleInstances IgnoreNew

# Register the task
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Description "Check Gmail for school emails and send Telegram alerts every 5 minutes"

Write-Host "Scheduled task '$taskName' created successfully!"
Write-Host "Runs daily 6 AM - midnight, every 5 minutes."
