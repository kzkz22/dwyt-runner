; dwyt runner – Windows installer (round 7c; plan 15b, decisions 18.15, 18.20). Built by packaging/build.py:
;   iscc /DAppVersion=0.11.0 /DSrc=<folder with python\ (Skipwise.exe in it), bin\, dwyt-logo.ico> dwyt-runner.iss
; Per-user, no admin rights: %LOCALAPPDATA%\Programs\Skipwise (before 0.17: Programs\dwyt-runner, kept). Settings (%APPDATA%\dwyt-runner), keys (Credential
; Manager) and models (%LOCALAPPDATA%\dwyt-runner\models) are the runner's own and stay on uninstall unless asked.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef Src
  #define Src "..\..\build\package\windows"
#endif

[Setup]
AppId={{6E0C3D6A-3B7F-4C1E-9A55-2D8B1F0E7C41}
AppName={cm:AppTitle}
AppVersion={#AppVersion}
AppVerName={cm:AppTitle} {#AppVersion}
AppPublisher=Skipwise
AppPublisherURL=https://github.com/kzkz22/skipwise-runner
; new installs: Programs\Skipwise; an installed runner keeps its folder (UsePreviousAppDir, same AppId)
DefaultDirName={localappdata}\Programs\Skipwise
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputBaseFilename=skipwise-runner-setup-{#AppVersion}
SetupIconFile={#Src}\dwyt-logo.ico
UninstallDisplayIcon={app}\dwyt-logo.ico
; tells Explorer to refresh its icons (the old "d" icon stayed in the cache)
ChangesAssociations=yes
UninstallDisplayName={cm:AppTitle}
#ifdef SignUninstaller
; signed releases (decision 19.27): the first compile leaves the uninstaller in UninstDir as an .e32 and stops; it is
; signed outside (SignPath), and this compile embeds its signature (packaging/build.py windows-tosign)
SignedUninstaller=yes
SignedUninstallerDir={#UninstDir}
#endif
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; the language follows Windows' display language (round 9); any other language gets the first entry, English
ShowLanguageDialog=no
LanguageDetectionMethod=uilanguage
; an update runs this silently while the app is open: close it, the [Run] entry below starts it again
CloseApplications=force
RestartApplications=no

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "hu"; MessagesFile: "compiler:Languages\Hungarian.isl"
Name: "de"; MessagesFile: "compiler:Languages\German.isl"
Name: "it"; MessagesFile: "compiler:Languages\Italian.isl"
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "fr"; MessagesFile: "compiler:Languages\French.isl"

[CustomMessages]
en.AppTitle=Skipwise runner
hu.AppTitle=Skipwise futtató
de.AppTitle=Skipwise-Runner
it.AppTitle=Runner Skipwise
es.AppTitle=Runner de Skipwise
fr.AppTitle=Runner Skipwise
en.DesktopIcon=Icon on the desktop
hu.DesktopIcon=Ikon az asztalon
de.DesktopIcon=Symbol auf dem Desktop
it.DesktopIcon=Icona sul desktop
es.DesktopIcon=Icono en el escritorio
fr.DesktopIcon=Icône sur le Bureau
en.StartNow=Start the Skipwise runner
hu.StartNow=A Skipwise futtató indítása
de.StartNow=Skipwise-Runner starten
it.StartNow=Avvia il runner Skipwise
es.StartNow=Iniciar el runner de Skipwise
fr.StartNow=Lancer le runner Skipwise
en.AskDelete=Delete the runner's settings, keys and downloaded models too?
hu.AskDelete=Töröljem a futtató beállításait, a kulcsait és a letöltött modelleket is?
de.AskDelete=Auch die Einstellungen, Schlüssel und heruntergeladenen Modelle des Runners löschen?
it.AskDelete=Elimino anche impostazioni, chiavi e modelli scaricati del runner?
es.AskDelete=¿Elimino también los ajustes, las claves y los modelos descargados del runner?
fr.AskDelete=Supprimer aussi les réglages, les clés et les modèles téléchargés du runner ?
en.AskDeleteMore=If you reinstall later, without them it has to be set up again and the models downloaded again. Revoke the runner in Skipwise on the Runners page.
hu.AskDeleteMore=Ha később újratelepíted, nélkülük újra be kell állítani, és a modelleket újra le kell tölteni. A Skipwise-ban a futtatót a Futtatók oldalon vond vissza.
de.AskDeleteMore=Wenn du später neu installierst, muss ohne sie alles neu eingerichtet und die Modelle neu heruntergeladen werden. Widerrufe den Runner in Skipwise auf der Runner-Seite.
it.AskDeleteMore=Se lo reinstalli più tardi, senza di essi andrà configurato di nuovo e i modelli andranno riscaricati. Revoca il runner in Skipwise nella pagina Runner.
es.AskDeleteMore=Si lo reinstalas más tarde, sin ellos habrá que configurarlo de nuevo y volver a descargar los modelos. Revoca el runner en Skipwise, en la página Runners.
fr.AskDeleteMore=Si vous le réinstallez plus tard, il faudra sans eux tout reconfigurer et retélécharger les modèles. Révoquez le runner dans Skipwise, sur la page Runners.

[Tasks]
Name: "desktopicon"; Description: "{cm:DesktopIcon}"; Flags: unchecked

[InstallDelete]
; a new version brings its whole Python: no packages left over from the previous one
Type: filesandordirs; Name: "{app}\python"
; the icon before 0.15.3 (the "d"); the logo comes under a new name so no cached copy is shown
Type: files; Name: "{app}\dwyt-runner.ico"
; the shortcuts before 0.16 were called "dwyt futtató" (the new name: Skipwise)
Type: files; Name: "{userprograms}\dwyt futtató.lnk"
Type: files; Name: "{userdesktop}\dwyt futtató.lnk"
; until 0.21 the shortcuts were always Hungarian; now they follow the installer's language (made again below)
Type: files; Name: "{userprograms}\Skipwise futtató.lnk"
Type: files; Name: "{userdesktop}\Skipwise futtató.lnk"

[Files]
Source: "{#Src}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{userprograms}\{cm:AppTitle}"; Filename: "{app}\python\Skipwise.exe"; Parameters: "-m dwyt_runner.app.main"; WorkingDir: "{app}"; IconFilename: "{app}\dwyt-logo.ico"; AppUserModelID: "kzkz22.dwyt-runner"
Name: "{userdesktop}\{cm:AppTitle}"; Filename: "{app}\python\Skipwise.exe"; Parameters: "-m dwyt_runner.app.main"; WorkingDir: "{app}"; IconFilename: "{app}\dwyt-logo.ico"; AppUserModelID: "kzkz22.dwyt-runner"; Tasks: desktopicon

[Run]
Filename: "{app}\python\Skipwise.exe"; Parameters: "-m dwyt_runner.app.main"; WorkingDir: "{app}"; Description: "{cm:StartNow}"; Flags: nowait postinstall skipifsilent
; after a silent update (from the app itself) the app comes back in the tray; /NOLAUNCH for tests
Filename: "{app}\python\Skipwise.exe"; Parameters: "-m dwyt_runner.app.main --hidden"; WorkingDir: "{app}"; Flags: nowait; Check: StartAfterSilentInstall

[UninstallDelete]
; Python writes __pycache__ while running: files the installer did not put there
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\bin"

[UninstallRun]
; the login start always goes; a running app is closed by the uninstaller
Filename: "{app}\python\python.exe"; Parameters: "-m dwyt_runner.cli autostart off"; Flags: runhidden; RunOnceId: "AutostartOff"

[Code]
function StartAfterSilentInstall: Boolean;
begin
  Result := WizardSilent and (ExpandConstant('{param:NOLAUNCH|0}') = '0');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Code: Integer;
begin
  if CurUninstallStep = usUninstall then
    { the running app (tray) holds its files: ask it to quit first }
    Exec(ExpandConstant('{app}\python\python.exe'), '-m dwyt_runner.cli app-quit', '', SW_HIDE,
         ewWaitUntilTerminated, Code);
  if (CurUninstallStep = usUninstall) and not UninstallSilent then
  begin
    { unticked by default: a reinstall should find everything where it was (decision 18.15) }
    if MsgBox(CustomMessage('AskDelete') + #13#10#13#10 + CustomMessage('AskDeleteMore'),
              mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      Exec(ExpandConstant('{app}\python\python.exe'), '-m dwyt_runner.cli reset --models --yes', '',
           SW_HIDE, ewWaitUntilTerminated, Code);
  end;
end;
