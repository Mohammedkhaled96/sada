; Inno Setup script for Sada.
;
; Build:  "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe" installer\Sada.iss
; Input:  dist\Sada\        (from: python -m PyInstaller Sada.spec --noconfirm)
; Output: installer\out\Sada-Setup-<version>.exe
;
; One installer, two ways to run it:
;
;   Sada-Setup-1.0.0.exe                                  interactive wizard
;   Sada-Setup-1.0.0.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART   unattended
;
; The silent form is what winget invokes, so it must complete with no window, no
; prompt and no reboot, and must still register a working uninstaller. Everything
; below that looks fussy is there to satisfy that.
;
; Installs per-user into %LocalAppData%, which needs no administrator rights.
; That matters for two reasons: winget can install it without elevation, and
; many of the people this is for cannot get a UAC prompt approved on a work
; machine - and a UAC dialog is one more thing to navigate without sight.
;
; No speech model is included. The smallest is 461 MB and the recommended one is
; 1.5 GB; bundling either would make this a multi-gigabyte download repeated on
; every update. The user picks and downloads one from Settings, and it is stored
; under %LocalAppData%\Sada so an upgrade or reinstall never re-downloads it.

#define AppName          "Sada"
#define AppVersion       "1.0.0"
#define AppPublisher     "Mohammed Khaled"
#define AppExe           "Sada.exe"
#define AppUrl           "https://hub.docker.com/r/mohammedkhaledmahmoud/sada"
#define AppId            "{8E2C1F4A-9D3B-4A77-B5E1-2C9F6A0D1B83}"

[Setup]
AppId={{#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppUrl}
AppSupportURL={#AppUrl}
AppUpdatesURL={#AppUrl}
; winget reads the installed version from the uninstall registry entry, which
; Inno fills from VersionInfoVersion. Without it, `winget upgrade` cannot tell
; that a newer release exists.
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}
VersionInfoCompany={#AppPublisher}
VersionInfoDescription={#AppName} Setup

DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
AllowNoIcons=yes

; Per-user, so no elevation and no UAC prompt.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

OutputDir=out
OutputBaseFilename=Sada-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; The wizard has to be usable by someone who cannot see it.
WizardSizePercent=120

UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExe}
; The icon shown on the setup executable itself and in Add/Remove Programs.
SetupIconFile=..\assets\sada.ico

MinVersion=10.0
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

; Close a running copy instead of demanding a reboot. A silent install that asks
; for a restart is rejected by winget and is miserable for the user.
CloseApplications=yes
CloseApplicationsFilter=*.exe
RestartApplications=no
RestartIfNeededByRun=no
; Refuse to run two installers at once rather than corrupting the installation.
SetupMutex=SadaSetupMutex,Global\SadaSetupMutex

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "arabic";  MessagesFile: "compiler:Languages\Arabic.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; \
    GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
; Checked by default, and therefore also applied on a silent install: a tray
; application that is not running does nothing, and pressing the shortcut to
; silence would be baffling - especially with no window to look at.
Name: "startup";     Description: "Start {#AppName} when I sign in to Windows"; \
    GroupDescription: "Startup"

[Files]
; recursesubdirs is required: torch alone puts hundreds of files in
; subdirectories, and missing them produces an application that installs
; cleanly and then fails the moment it is launched.
Source: "..\dist\Sada\*"; DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\README.md";     DestDir: "{app}"; Flags: ignoreversion
Source: "..\USER_GUIDE.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}";           Filename: "{app}\{#AppExe}"
Name: "{group}\{#AppName} User Guide"; Filename: "{app}\USER_GUIDE.md"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}";     Filename: "{app}\{#AppExe}"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}";     Filename: "{app}\{#AppExe}"; Tasks: startup

[Run]
; skipifsilent keeps a silent install from launching anything, which winget
; requires: the command must return once installation is done.
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\_internal\__pycache__"

[Code]
// Two traps in an Inno [Code] section, both of which abort the compile with a
// message pointing somewhere unhelpful:
//
//   1. No line may START with '#'. The preprocessor reads a leading '#' as a
//      directive, so a continuation line beginning with #13#10 fails with
//      "Unknown preprocessor directive". Keep #13#10 mid-line.
//   2. Use // comments, not braces. A brace comment containing a constant such
//      as the app directory ends at that constant's closing brace, and the rest
//      of the comment is then parsed as code.

function StopRunningCopy(): Integer;
var
  ResultCode: Integer;
begin
  // A running copy holds its own files open, so an upgrade must stop it first.
  // taskkill returns non-zero when nothing was running, which is fine.
  Exec('taskkill.exe', '/F /IM Sada.exe', '', SW_HIDE,
       ewWaitUntilTerminated, ResultCode);
  Result := ResultCode;
end;

function InitializeSetup(): Boolean;
begin
  StopRunningCopy();
  Result := True;
end;

function InitializeUninstall(): Boolean;
begin
  StopRunningCopy();
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
  Question: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    // Settings and the downloaded model live under the user profile, not in the
    // installation, so an upgrade keeps them. Ask before deleting a download
    // that can be 1.5 GB, and default to No so someone reinstalling does not
    // lose it by pressing Enter.
    //
    // A silent uninstall must never stop to ask, so the data is simply kept:
    // leaving a model behind is recoverable, deleting one silently is not.
    DataDir := ExpandConstant('{localappdata}\Sada');
    if DirExists(DataDir) and not UninstallSilent() then
    begin
      Question := 'Also remove your settings and the downloaded speech model?' + #13#10 + #13#10 +
                  'The model can be up to 1.5 GB and would have to be downloaded again next time.' + #13#10 +
                  'Choose No if you are reinstalling or upgrading.';
      if MsgBox(Question, mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
    end;
  end;
end;
