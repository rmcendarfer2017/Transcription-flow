; Inno Setup 6 script: a per-user installer around the PyInstaller onedir.
;
;   ISCC.exe /DAppVersion=0.1.0 /DAppNumVersion=0.1.0.0 /DSourceDir=..\dist\TranscriptionFlow
;            /DOutputDir=..\dist /DIconFile=..\build\transcription_flow\TranscriptionFlow.ico
;            packaging\installer.iss
;
; No admin rights: installs to %LOCALAPPDATA%\Programs\Transcription Flow.
; Silent install:   TranscriptionFlow-Setup-<ver>.exe /VERYSILENT /CURRENTUSER
; Silent uninstall: unins000.exe /VERYSILENT [/DELETEUSERDATA]
; (a silent uninstall keeps settings and models unless /DELETEUSERDATA is given).
; tests/test_packaging.py checks the names below against the app's own.

#ifndef AppVersion
  #define AppVersion "0.0.0.dev0"
#endif
#ifndef AppNumVersion
  #define AppNumVersion "0.0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\TranscriptionFlow"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist"
#endif

#define AppName "Transcription Flow"
#define ExeName "TranscriptionFlow.exe"
#define RunValueName "TranscriptionFlow"
#define DataFolder "TranscriptionFlow"

[Setup]
AppId={{C41FBC91-05D3-4BBB-ADAB-FBC5C12CBECD}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Transcription Flow contributors
AppPublisherURL=https://github.com/rmcendarfer2017/Transcription-flow
AppSupportURL=https://github.com/rmcendarfer2017/Transcription-flow/issues
AppUpdatesURL=https://github.com/rmcendarfer2017/Transcription-flow/releases
VersionInfoVersion={#AppNumVersion}
VersionInfoProductName={#AppName}
VersionInfoDescription={#AppName} installer
PrivilegesRequired=lowest
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
; The single-instance mutex in src/transcription_flow/single_instance.py:
; setup and uninstall ask the user to quit a running copy first.
AppMutex=Local\TranscriptionFlow.SingleInstance
CloseApplications=yes
UninstallDisplayIcon={app}\{#ExeName}
UninstallDisplayName={#AppName}
OutputDir={#OutputDir}
OutputBaseFilename=TranscriptionFlow-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
#ifdef IconFile
SetupIconFile={#IconFile}
#endif

[Tasks]
; On an upgrade the box appears only if autostart is still on, so a choice
; made later in Settings (or Task Manager) survives (review-m4 F4).
Name: "autostart"; Description: "Start {#AppName} when I sign in to Windows"; GroupDescription: "Startup:"; Check: ShouldWriteRunValue

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#ExeName}"; Comment: "Hold a key, speak, release: the text is typed"

[Registry]
; The same value the app's "Start with Windows" setting writes
; (src/transcription_flow/autostart.py), so there is one source of truth.
; Written on a fresh install, or on an upgrade only if it is already there.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#RunValueName}"; ValueData: """{app}\{#ExeName}"" --tray"; Flags: uninsdeletevalue; Tasks: autostart; Check: ShouldWriteRunValue

[Run]
Filename: "{app}\{#ExeName}"; Description: "Start {#AppName} now"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Anything the app or Python wrote next to its files.
Type: filesandordirs; Name: "{app}\_internal"

[Code]
var
  DeleteUserData: Boolean;
  IsUpgrade: Boolean;
  HadRunValue: Boolean;

function InitializeSetup(): Boolean;
begin
  { Read before anything is written: an earlier install of this AppId, and
    whether autostart is on now. }
  IsUpgrade := RegKeyExists(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{C41FBC91-05D3-4BBB-ADAB-FBC5C12CBECD}_is1');
  HadRunValue := RegValueExists(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', '{#RunValueName}');
  Result := True;
end;

function ShouldWriteRunValue(): Boolean;
begin
  Result := (not IsUpgrade) or HadRunValue;
end;

function HasParam(const Name: String): Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), Name) = 0 then
    begin
      Result := True;
      Exit;
    end;
end;

function InitializeUninstall(): Boolean;
begin
  Result := True;
  if UninstallSilent then
    DeleteUserData := HasParam('/DELETEUSERDATA')
  else
    DeleteUserData := MsgBox('Also delete your {#AppName} settings and downloaded speech models?' + #13#10#13#10 +
      'The models can take several hundred MB. Choose No to keep them for a later reinstall.',
      mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;  { No is the default: a stray Enter keeps them }
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    { The app's own setting may have written these even without the
      installer's task; Task Manager writes the StartupApproved one. }
    RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', '{#RunValueName}');
    RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run', '{#RunValueName}');
  end;
  if (CurUninstallStep = usPostUninstall) and DeleteUserData then
  begin
    { Only these two folders (paths.py). DelTree deletes a junction inside
      them without following it; CI checks that with a junction and with
      folders next to these (packaging/ci_smoke.py). }
    DelTree(ExpandConstant('{localappdata}\{#DataFolder}'), True, True, True);
    DelTree(ExpandConstant('{userappdata}\{#DataFolder}'), True, True, True);
  end;
end;
