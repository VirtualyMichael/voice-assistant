; Inno Setup script. Build: ISCC.exe /DAppVersion=0.1.0 packaging\installer.iss  (after PyInstaller has produced dist\VoiceAssistant)
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{6F3C2E5A-8B1D-4C7E-9A52-3D1E7B94C0A1}
AppName=Voice Assistant
AppVersion={#AppVersion}
AppPublisher=Voice Assistant
DefaultDirName={autopf}\Voice Assistant
DefaultGroupName=Voice Assistant
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist\installer
OutputBaseFilename=VoiceAssistant-Setup-{#AppVersion}
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\VoiceAssistant.exe
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
MinVersion=10.0.17763

[Messages]
WelcomeLabel2=This installs Voice Assistant, a hands-free voice assistant for Windows.%n%nNothing else is required. The first time you start it, a setup guide checks your PC and, only with your permission, downloads the speech models (about 430 MB, or 900 MB with the optional accurate recognizer) and helps you connect an AI model.%n%nYour conversations and settings stay on this PC.

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; Flags: unchecked
Name: "autostart"; Description: "Start Voice Assistant when I sign in to Windows"; Flags: unchecked

[Files]
Source: "..\dist\VoiceAssistant\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion createallsubdirs
Source: "..\extension\*"; DestDir: "{app}\extension"; Excludes: "_key.txt"; Flags: recursesubdirs ignoreversion createallsubdirs

[Icons]
Name: "{group}\Voice Assistant"; Filename: "{app}\VoiceAssistant.exe"
Name: "{group}\Voice Assistant setup guide"; Filename: "{app}\VoiceAssistant.exe"; Parameters: "--setup"
Name: "{group}\Uninstall Voice Assistant"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Voice Assistant"; Filename: "{app}\VoiceAssistant.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "VoiceAssistant"; ValueData: """{app}\VoiceAssistant.exe"""; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\VoiceAssistant.exe"; Description: "Start Voice Assistant now"; Flags: nowait postinstall skipifsilent
; The in-app updater runs this installer silently with /RELAUNCH=1 and expects the app to come back.
Filename: "{app}\VoiceAssistant.exe"; Flags: nowait; Check: Relaunch

[Code]
function Relaunch: Boolean;
begin
  Result := ExpandConstant('{param:RELAUNCH|0}') = '1';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{userappdata}\VoiceAssistant');
    if DirExists(DataDir) then
      if MsgBox('Also delete your settings, saved conversation and downloaded voice models?' + #13#10 + DataDir,
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;
