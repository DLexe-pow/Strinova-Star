@echo off
rem Strinova Star UI launcher. ASCII only on purpose: a batch file containing
rem Chinese text breaks depending on the console code page, so the messages here
rem stay English. The UI itself is in Chinese.
setlocal
cd /d "%~dp0"
title Strinova Star

set "PY="
set "PY_WEAK="

rem Try in order: STRINOVA_PYTHON -> bundled python -> local Python -> .venv -> PATH python
for %%P in ("%STRINOVA_PYTHON%" "%~dp0python\python.exe" "D:\software\Python\python.exe" "%~dp0.venv\Scripts\python.exe") do call :probe "%%~P"
for /f "delims=" %%P in ('where python 2^>nul') do call :probe "%%P"

if not defined PY if not defined PY_WEAK goto :no_py
if not defined PY (
  set "PY=%PY_WEAK%"
  echo [warn] This Python has no rapidocr-onnxruntime: UI opens, OCR features fail.
)

echo Using Python: %PY%

if not "%~1"=="" goto :launch
rem No arguments: if 8765 is already listening, just open the browser.
netstat -ano | findstr ":8765 " | findstr LISTENING >nul 2>nul
if not errorlevel 1 (
  echo UI is already running on http://127.0.0.1:8765 - opening it.
  start "" "http://127.0.0.1:8765"
  exit /b 0
)

:launch
echo Starting UI ... first start loads the templates, a few seconds is normal.
"%PY%" -B "%~dp0src\app.py" %*
set "CODE=%ERRORLEVEL%"
if "%CODE%"=="0" goto :done
echo.
echo [error] UI exited with code %CODE%
pause

:done
endlocal
exit /b 0

:no_py
echo.
echo [error] No usable Python found. It needs opencv + numpy,
echo         plus rapidocr-onnxruntime for the OCR features.
echo   - pip install:  python -m pip install -r requirements.txt
echo   - or point us at one:  set STRINOVA_PYTHON=D:\path\to\python.exe
echo.
pause
exit /b 1

:probe
if "%~1"=="" goto :eof
if defined PY goto :eof
"%~1" -c "import cv2, numpy, rapidocr_onnxruntime" >nul 2>nul
if not errorlevel 1 (
  set "PY=%~1"
  goto :eof
)
"%~1" -c "import cv2, numpy" >nul 2>nul
if not errorlevel 1 if not defined PY_WEAK set "PY_WEAK=%~1"
goto :eof
