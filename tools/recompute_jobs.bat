@echo off
setlocal
rem Recompute the machining summary of existing Jobs with the current rules (TDMS + NC).
rem Run it once after upgrading from an older version (backend\recompute_job_summary.py).
rem It shows a preview first and changes the database only after you confirm.
rem The values before the change are saved to backups\recompute_job_summary_*.json.
cd /d "%~dp0.."

echo ====================================================
echo   Recompute Job summaries (TDMS + NC)
echo ====================================================
echo Stop the services first: Controller, [Stop All].
echo.

rem Same Python as the controller: the bundled runtime if it exists.
set "PY="
if exist "runtime\python.exe" set "PY=runtime\python.exe"
if not defined PY (
    where py.exe >nul 2>&1 && set "PY=py -3"
)
if not defined PY set "PY=python"

echo [1/2] Preview - the database is not changed.
%PY% -X utf8 backend\recompute_job_summary.py --all
if errorlevel 1 goto failed

echo.
choice /C YN /M "Apply the new values to the database"
if errorlevel 2 goto cancelled

echo.
echo [2/2] Applying...
%PY% -X utf8 backend\recompute_job_summary.py --all --apply
if errorlevel 1 goto failed
echo.
echo Done. The values before the change were saved to the backups folder.
goto end

:cancelled
echo Cancelled. Nothing was changed.
goto end

:failed
echo.
echo [ERROR] Recompute failed. See the messages above.

:end
pause
