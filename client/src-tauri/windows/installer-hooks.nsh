; Rendered by bundle_installer.py using the runtime lease owner's filename.
; Keep the standard Tauri template; replace its optional data deletion with
; the product's constrained cleanup adapter before runtime files are removed.
!ifndef TEBB_DATA_ROOT
  !define TEBB_DATA_ROOT "$LOCALAPPDATA\DeskCompanion"
!endif

Var TebbInstallerMutex
!macro TEBB_REQUIRE_IDLE_INSTALLER
  ; Keep the handle until process exit, including all file/directory cleanup.
  ; This also protects an ordinary self-copied NSIS uninstaller from a second
  ; installer that starts after its original launcher has already returned.
  System::Call 'kernel32::CreateMutexW(p 0, i 0, w "${TEBB_INSTALLER_MUTEX_NAME}") p .r0 ?e'
  Pop $1
  ${If} $0 P= 0
  ${OrIf} $1 = 183
    ${If} $0 P<> 0
      System::Call 'kernel32::CloseHandle(p r0)'
    ${EndIf}
    SetErrorLevel 73
    ${IfNot} ${Silent}
      MessageBox MB_OK|MB_ICONSTOP "另一个 tebb 安装或卸载仍在进行，请等待其完成后重试。"
    ${EndIf}
    Abort
  ${EndIf}
  StrCpy $TebbInstallerMutex $0
!macroend

!macro TEBB_ABORT_RUNNING
  SetErrorLevel 73
  ${IfNot} ${Silent}
    MessageBox MB_OK|MB_ICONSTOP "请从托盘正常退出 tebb，并等待后台完成收尾后重试。安装器不会强行停止任务。"
  ${EndIf}
  Abort
!macroend

!macro TEBB_CHECK_LEASE root
  ${If} ${FileExists} "${root}\memory\locks\@RUNTIME_LOCK@"
    System::Call 'kernel32::CreateFileW(w "${root}\memory\locks\@RUNTIME_LOCK@", i 0xC0000000, i 3, p 0, i 3, i 0, p 0) p .r0'
    ${If} $0 P= -1
      !insertmacro TEBB_ABORT_RUNNING
    ${EndIf}
    System::Call 'kernel32::LockFile(p r0, i 0, i 0, i 1, i 0) i .r1'
    System::Call 'kernel32::CloseHandle(p r0)'
    ${If} $1 = 0
      !insertmacro TEBB_ABORT_RUNNING
    ${EndIf}
  ${EndIf}
!macroend

!macro TEBB_REQUIRE_STOPPED executableName
  nsis_tauri_utils::FindProcessCurrentUser "${executableName}"
  Pop $R0
  ${If} $R0 = 0
    !insertmacro TEBB_ABORT_RUNNING
  ${EndIf}
  !insertmacro TEBB_CHECK_LEASE "${TEBB_DATA_ROOT}"
  ReadEnvStr $R3 "DESK_COMPANION_DATA_DIR"
  ${If} $R3 != ""
    !insertmacro TEBB_CHECK_LEASE "$R3"
  ${EndIf}
!macroend

; The vendor's default check may kill a running process in silent mode.
; Replace that macro with the same non-destructive guard; do not copy its template.
!ifmacrondef CheckIfAppIsRunning
  !error "Tauri process-check contract changed; review the installer hooks."
!endif
!macroundef CheckIfAppIsRunning
!macro CheckIfAppIsRunning executableName productName
  !insertmacro TEBB_REQUIRE_STOPPED "${executableName}"
!macroend

!macro NSIS_HOOK_PREINSTALL
  ; Compiler-time CreateProcess, after Tauri defines MAINBINARYSRCPATH.
  !execute '${TEBB_CAPTURE_TOOL} "${MAINBINARYSRCPATH}" "${TEBB_CAPTURE_PATH}" "${TEBB_PORTABLE_SHA}"' = 0
  !insertmacro TEBB_REQUIRE_IDLE_INSTALLER
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  !insertmacro TEBB_REQUIRE_IDLE_INSTALLER
  !insertmacro TEBB_REQUIRE_STOPPED "${MAINBINARYNAME}.exe"
  !ifndef TEBB_VERIFY_INSTALLER
  ${If} $DeleteAppDataCheckboxState = 1
  ${AndIf} $UpdateMode <> 1
  ${AndIf} $PassiveMode <> 1
  ${AndIfNot} ${Silent}
    MessageBox MB_YESNO|MB_DEFBUTTON2|MB_ICONEXCLAMATION "确定清除默认应用数据吗？这会删除本用户的配置、历史、日程、素材、扩展，以及 tebb 内浏览器登录。旧便携版共享这些数据。外部项目、系统登录和 Docker 环境不会被清除。" IDNO tebb_keep_data
    System::Call 'kernel32::SetEnvironmentVariableW(w "TEBB_DISABLE_USER_EXTENSIONS", w "1")'
    System::Call 'kernel32::GetTickCount() i .r0'
    System::Call 'kernel32::GetCurrentProcessId() i .r1'
    StrCpy $R2 "$TEMP\tebb-uninstall-$0-$1.json"
    ExecWait '"$INSTDIR\runtime\python.exe" -m desk_companion.installation clear-default-data --confirm-default-data --report "$R2"' $R0
    ${If} $R0 <> 0
      MessageBox MB_OK|MB_ICONSTOP "数据清除未完成，卸载已停止。请核对报告：$R2"
      SetErrorLevel 73
      Abort
    ${EndIf}
  ${EndIf}
  tebb_keep_data:
  !endif
  ; The standard template otherwise recursively removes unvalidated app-data
  ; paths. Both product roots were handled by the adapter; all other cases keep.
  StrCpy $DeleteAppDataCheckboxState 0
!macroend
