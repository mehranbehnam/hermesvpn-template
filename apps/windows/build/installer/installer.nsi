Unicode true
!include "MUI2.nsh"
!ifndef VERSION
  !define VERSION "1.0.1"
!endif
!define APPNAME "HermesVPN Connect"
!define EXENAME "HermesVPN-Connect.exe"
!define UNKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\HermesVPN"

Name "${APPNAME}"
OutFile "HermesVPN-Connect-Setup-${VERSION}.exe"
InstallDir "$PROGRAMFILES64\HermesVPN Connect"
InstallDirRegKey HKLM "${UNKEY}" "InstallLocation"
RequestExecutionLevel admin
SetCompressor /SOLID lzma
BrandingText "HermesVPN"
VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName" "${APPNAME}"
VIAddVersionKey "FileDescription" "${APPNAME} Setup"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "ProductVersion" "${VERSION}"
VIAddVersionKey "CompanyName" "HermesVPN"
VIAddVersionKey "LegalCopyright" "HermesVPN"

!define MUI_ICON "icon.ico"
!define MUI_UNICON "icon.ico"
!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_RUN "$INSTDIR\${EXENAME}"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "Farsi"
!insertmacro MUI_LANGUAGE "English"

!macro StopApp
  nsExec::Exec 'taskkill /F /IM ${EXENAME}'
  ; only HermesVPN's own xray (in %LOCALAPPDATA%\HermesVPN\core), never another app's
  nsExec::Exec `powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-Process xray -ErrorAction SilentlyContinue | Where-Object { $$_.Path -like '*\HermesVPN\core\*' } | Stop-Process -Force"`
  Sleep 1000
!macroend

Function .onInit
  StrCpy $LANGUAGE ${LANG_FARSI}
  ; Older app versions (1.0.16-1.0.18) updated by copying this installer over the app's own
  ; exe and running it as "HermesVPN-Connect.exe" - then StopApp would kill the installer
  ; itself mid-way. If we are running under that name, relaunch from a temp copy and exit.
  StrCmp $EXEFILE "${EXENAME}" 0 +4
    CopyFiles /SILENT "$EXEPATH" "$TEMP\HermesVPN-Setup-run.exe"
    Exec '"$TEMP\HermesVPN-Setup-run.exe" /S'
    Quit
FunctionEnd

Section "Install"
  SetShellVarContext all
  !insertmacro StopApp
  SetOutPath "$INSTDIR"
  File "..\bin\${EXENAME}"
  Delete "$INSTDIR\icon.ico"
  File "/oname=hermesvpn-${VERSION}.ico" "icon.ico"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  Delete "$DESKTOP\${APPNAME}.lnk"
  RMDir /r "$SMPROGRAMS\${APPNAME}"
  CreateShortcut "$DESKTOP\${APPNAME}.lnk" "$INSTDIR\${EXENAME}" "" "$INSTDIR\hermesvpn-${VERSION}.ico"
  CreateDirectory "$SMPROGRAMS\${APPNAME}"
  CreateShortcut "$SMPROGRAMS\${APPNAME}\${APPNAME}.lnk" "$INSTDIR\${EXENAME}" "" "$INSTDIR\hermesvpn-${VERSION}.ico"
  CreateShortcut "$SMPROGRAMS\${APPNAME}\Uninstall.lnk" "$INSTDIR\Uninstall.exe"
  WriteRegStr HKLM "${UNKEY}" "DisplayName" "${APPNAME}"
  WriteRegStr HKLM "${UNKEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKLM "${UNKEY}" "Publisher" "HermesVPN"
  WriteRegStr HKLM "${UNKEY}" "DisplayIcon" "$INSTDIR\hermesvpn-${VERSION}.ico"
  WriteRegStr HKLM "${UNKEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKLM "${UNKEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegDWORD HKLM "${UNKEY}" "NoModify" 1
  WriteRegDWORD HKLM "${UNKEY}" "NoRepair" 1
  ; make Explorer drop the cached old icon
  nsExec::Exec 'ie4uinit.exe -show'
  System::Call 'shell32::SHChangeNotify(i 0x08000000, i 0, p 0, p 0)'
  ; silent update started from inside the app (/S): open the new version when done
  IfSilent 0 +2
    Exec '"$INSTDIR\${EXENAME}"'
SectionEnd

Section "Uninstall"
  SetShellVarContext all
  !insertmacro StopApp
  nsExec::Exec 'schtasks /Delete /TN "${APPNAME}" /F'
  nsExec::Exec 'schtasks /Delete /TN "${APPNAME} Launcher" /F'
  Delete "$INSTDIR\${EXENAME}"
  Delete "$INSTDIR\${EXENAME}.old"
  Delete "$INSTDIR\icon.ico"
  Delete "$INSTDIR\hermesvpn-*.ico"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  Delete "$DESKTOP\${APPNAME}.lnk"
  RMDir /r "$SMPROGRAMS\${APPNAME}"
  DeleteRegKey HKLM "${UNKEY}"
  SetShellVarContext current
  DeleteRegKey HKCU "Software\Classes\hermesvpn"
  RMDir /r "$LOCALAPPDATA\HermesVPN\core"
SectionEnd
