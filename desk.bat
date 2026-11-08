@echo off
rem The Makefile, for cmd.exe. Same targets, no GNU make required.
rem   desk.bat test | shadow | paper | stocks | review | review-claude | kill
setlocal
cd /d "%~dp0"

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo [!] No virtualenv at .venv
    echo     python -m venv .venv
    echo     .venv\Scripts\python.exe -m pip install -r requirements.txt
    exit /b 1
)

if "%~1"=="" goto usage
if /i "%~1"=="test"          goto run_test
if /i "%~1"=="shadow"        goto run_shadow
if /i "%~1"=="paper"         goto run_paper
if /i "%~1"=="stocks"        goto run_stocks
if /i "%~1"=="review"        goto run_review
if /i "%~1"=="review-claude" goto run_review_claude
if /i "%~1"=="kill"          goto run_kill
if /i "%~1"=="ui"            goto run_ui

echo [!] Unknown command: %~1
goto usage

:run_test
rem The offline suite. No keys, no network, no positions.
%PY% -m unittest discover -s tests -v
exit /b %ERRORLEVEL%

:run_shadow
rem One full cycle: scan, check, score, alert. Never opens a position.
%PY% run_crypto.py --shadow --once
exit /b %ERRORLEVEL%

:run_paper
rem The loop. Ctrl-C to stop; "desk.bat kill" stops it at the next cycle boundary.
%PY% run_crypto.py
exit /b %ERRORLEVEL%

:run_stocks
%PY% run_stocks.py
exit /b %ERRORLEVEL%

:run_review
rem Extra args pass through: desk.bat review --days 30
%PY% review.py %2 %3
exit /b %ERRORLEVEL%

:run_review_claude
%PY% review.py --claude %2 %3
exit /b %ERRORLEVEL%

:run_kill
rem "touch KILL" for Windows. The desk closes every paper position on its next
rem cycle and stops. Delete the file to start it again.
echo. > KILL
echo Wrote KILL. The desk stops on its next cycle.
exit /b 0

:run_ui
rem Read-only browser view. Ctrl-C to stop. Extra args pass through: desk.bat ui --port 9000
%PY% run_ui.py --open %2 %3 %4
exit /b %ERRORLEVEL%

:usage
echo.
echo   desk.bat ^<command^>
echo.
echo   test            offline test suite, needs no keys
echo   shadow          one full cycle: scan, check, score, alert. No position.
echo   paper           paper trading, every 60 seconds. Ctrl-C to stop.
echo   stocks          Alpaca paper account, every 15 min while the market is open
echo   review          weekly stats from journal.jsonl
echo   review-claude   weekly stats plus a blunt written review ^(needs a key^)
echo   kill            stop the desk on its next cycle
echo   ui              read-only browser view of the desk
echo.
echo   From PowerShell: .\desk.bat test
exit /b 1