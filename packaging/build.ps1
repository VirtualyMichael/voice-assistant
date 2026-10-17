# Builds dist\installer\VoiceAssistant-Setup-<version>.exe
#   powershell -File packaging\build.ps1 -Version 0.1.0
# Needs: the project's .venv with the app dependencies plus `pip install pyinstaller`, and Inno Setup 6.
param([string]$Version = "0.1.0")
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot)
$py = ".\.venv\Scripts\python.exe"

& $py packaging\make_icon.py packaging
& $py -m PyInstaller packaging\voice_assistant.spec --noconfirm
if ($LASTEXITCODE) { throw "PyInstaller failed" }

# Gate: the packaged program must contain everything it needs before we wrap it in an installer.
$result = Join-Path $env:TEMP "va_selftest.json"
Remove-Item $result -ErrorAction SilentlyContinue
$p = Start-Process dist\VoiceAssistant\VoiceAssistant.exe -ArgumentList "--selftest", $result -Wait -PassThru
if ($p.ExitCode -ne 0) { Get-Content $result; throw "Packaged self-test failed" }

$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 not found (https://jrsoftware.org/isinfo.php)" }
& $iscc /Q "/DAppVersion=$Version" packaging\installer.iss
if ($LASTEXITCODE) { throw "Inno Setup failed" }
Get-ChildItem dist\installer | Select-Object Name, @{n = "MB"; e = { [math]::Round($_.Length / 1MB) } }
