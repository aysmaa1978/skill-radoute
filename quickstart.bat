@echo off
rem ============================================================
rem  skill-radoute quickstart (v1.7.0)
rem  Double-click to: check Python -> cd scripts
rem  -> run `python acquire.py run --query tavily --auto`
rem  NOTE: ASCII-only on purpose; cmd.exe batch parsing of
rem  multibyte chars inside if-blocks is unreliable. Chinese
rem  guidance is printed by the Python scripts themselves.
rem ============================================================
title skill-radoute quickstart

echo.
echo ============================================
echo   skill-radoute quickstart script
echo ============================================
echo.

rem ---------- 1. check Python ----------
echo [1/3] Checking Python...
where python >nul 2>nul
if errorlevel 1 (
    where py >nul 2>nul
    if errorlevel 1 (
        echo.
        echo [!] Python not found. Please install Python 3.10+:
        echo     https://www.python.org/downloads/
        echo     Check "Add Python to PATH" during install,
        echo     then double-click this script again.
        echo.
        pause
        exit /b 1
    )
    set "PY=py"
) else (
    set "PY=python"
)

"%PY%" --version >nul 2>nul
if errorlevel 1 (
    echo [!] Failed to start Python. Please check the install.
    pause
    exit /b 1
)
"%PY%" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 (
    echo [!] Python too old: 3.10+ required. Please upgrade.
    pause
    exit /b 1
)
echo [ok] Python environment OK

rem ---------- 2. enter scripts dir ----------
echo.
echo [2/3] Entering scripts directory...
cd /d "%~dp0scripts"
if errorlevel 1 (
    echo [!] scripts directory not found: %~dp0scripts
    pause
    exit /b 1
)
echo [ok] Now in %CD%

rem ---------- 3. run acquire ----------
echo.
echo [3/3] Fetching skill package (tavily)...
"%PY%" acquire.py run --query tavily --auto
set "RC=%errorlevel%"

echo.
if "%RC%"=="0" (
    echo ============================================
    echo   [OK] Acquisition completed successfully!
    echo ============================================
    echo.
    echo Next steps:
    echo   1. Run registry.py scan in WorkBuddy to refresh the index
    echo   2. route a task to tavily, e.g.:
    echo      router.py route "search the latest AI news"
    echo.
    echo Useful commands:
    echo   python scripts/router.py status      show router status
    echo   python scripts/acquire.py status     show acquire status
) else (
    echo ============================================
    echo   [!] Acquisition did not succeed (exit code %RC%)
    echo ============================================
    echo.
    echo Common causes and fixes:
    echo   - Network issue: set GITHUB_PROXY, then re-run
    echo   - Interrupted session: run acquire.py resume to continue
    echo   - Skill not in trusted table: run acquire.py status
    echo   - See README.md FAQ section for details
)
echo.
pause
