@echo off
setlocal
cd /d "%~dp0"
echo === AI Job Agent setup ===
echo.

rem JobSpy needs Python 3.12 on Windows (its numpy version has no 3.13+ builds).
py -3.12 --version >nul 2>&1
if not errorlevel 1 goto :have_python
echo Python 3.12 not found. Trying to install it with the Python install manager...
py install 3.12
py -3.12 --version >nul 2>&1
if not errorlevel 1 goto :have_python
echo.
echo Could not find or install Python 3.12.
echo Download the Python 3.12 Windows installer from https://www.python.org/downloads/windows/
echo During install keep "py launcher" ticked, then run setup.bat again.
goto :fail

:have_python
if exist .venv\Scripts\python.exe goto :have_venv
echo Creating the virtual environment in .venv ...
py -3.12 -m venv .venv || goto :fail

:have_venv
echo Installing packages - this takes a few minutes...
.venv\Scripts\python -m pip install --upgrade pip -q || goto :fail
.venv\Scripts\python -m pip install -r requirements.txt || goto :fail
echo Installing the Playwright browser...
.venv\Scripts\python -m playwright install chromium || goto :fail
if not exist .env copy .env.example .env >nul
if not exist profile.yaml copy profile.example.yaml profile.yaml >nul

echo.
echo Setup finished. Next:
echo   1. Put your CV, cover letter and target_job.md in the inputs folder
echo   2. Add your API key to .env and fill in profile.yaml and config.yaml
echo   3. Run:  .venv\Scripts\python main.py doctor
echo.
pause
exit /b 0

:fail
echo.
echo Setup did not finish - see the message above.
pause
exit /b 1
