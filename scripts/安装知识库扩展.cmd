@echo off
setlocal
if not exist "%~dp0runtime\python.exe" (
  echo Runtime folder is missing. Extract the complete release ZIP first.
  pause
  exit /b 1
)
if not defined DESK_COMPANION_DATA_DIR set "DESK_COMPANION_DATA_DIR=%LOCALAPPDATA%\DeskCompanion"
echo Close tebb before installing. Dependencies are retained in your user data folder.
echo A failed installation keeps the previous compatible extension generation.
"%~dp0runtime\python.exe" -c "from desk_companion.runtime_extensions import main; main()" install
if errorlevel 1 goto failed
echo Installation complete. Restart tebb, then download models in the Knowledge page.
pause
exit /b 0
:failed
echo Installation failed. Check the messages above and your internet connection, then retry.
pause
exit /b 1
