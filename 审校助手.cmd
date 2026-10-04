@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Python environment not found: .venv\Scripts\python.exe
  echo Please follow the local setup instructions in README.zh-CN.md.
  echo.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" scripts\review_workflow.py
set "REVIEW_RESULT=%ERRORLEVEL%"

echo.
if "%REVIEW_RESULT%"=="0" (
  echo Review workflow completed.
) else if "%REVIEW_RESULT%"=="2" (
  echo No formal changes were made.
) else (
  echo Review workflow did not complete. See the message above.
)
pause
exit /b %REVIEW_RESULT%
