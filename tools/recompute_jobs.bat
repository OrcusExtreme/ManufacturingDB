@echo off
setlocal
rem Bring existing data up to the current rules. Run it once after upgrading from an older version.
rem   1. Workingsteps: split again from the NC by feature, operation, cutting conditions and tool
rem      (ISO 14649, backend\resync_workingsteps.py)
rem   2. Job summaries: recompute with TDMS + NC (backend\recompute_job_summary.py)
rem Each part shows a preview first and changes the database only after you confirm.
rem The values before the change are saved to backups\*.json.
cd /d "%~dp0.."

echo ====================================================
echo   Update existing data to the current rules
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

echo [1/2] Workingsteps from the NC - preview, the database is not changed.
%PY% -X utf8 backend\resync_workingsteps.py
if errorlevel 1 goto failed
echo.
choice /C YN /M "Apply the new workingsteps to the database"
if errorlevel 2 (
    echo Skipped. The workingsteps were not changed.
    goto summaries
)
%PY% -X utf8 backend\resync_workingsteps.py --apply
if errorlevel 1 goto failed

:summaries
echo.
echo [2/2] Job summaries with TDMS + NC - preview, the database is not changed.
%PY% -X utf8 backend\recompute_job_summary.py --all
if errorlevel 1 goto failed

echo.
choice /C YN /M "Apply the new Job summaries to the database"
if errorlevel 2 goto cancelled

echo.
echo Applying...
%PY% -X utf8 backend\recompute_job_summary.py --all --apply
if errorlevel 1 goto failed
echo.
echo Done. The values before the change were saved to the backups folder.
goto end

:cancelled
echo Skipped. The Job summaries were not changed.
goto end

:failed
echo.
echo [ERROR] Failed. See the messages above.

:end
pause
