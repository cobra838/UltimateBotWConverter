@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0.."
if "%~1"=="" (
echo No BNP file was provided.
echo Drag and drop one or more .bnp files onto this script,
echo or run:
echo ubotw_converter\convert_single_thread.bat "path\to\your\bnp"
echo Enter the path to a mod. Use quotes if the path contains spaces:
set /P MANUAL_PATH="> "
echo.
if "!MANUAL_PATH!"=="" (
echo No path was provided.
pause
exit /b 1
)
echo +++++++++++++++++++++++++++
echo + Ultimate BotW Converter +
echo +++++++++++++++++++++++++++
echo.
echo Attempting to convert manually entered path, please wait...
echo.
python -m ubotw_converter.converter -s !MANUAL_PATH!
echo.
pause
exit /b 0
)
set /A TOTAL=0
set /A COUNTER=0
FOR %%A IN (%*) DO (
set /A TOTAL+=1
)
echo +++++++++++++++++++++++++++
echo + Ultimate BotW Converter +
echo +++++++++++++++++++++++++++
FOR %%A IN (%*) DO (
echo.
set /A COUNTER+=1
echo Attempting to convert !COUNTER! of %TOTAL% mods, please wait...
echo.
python -m ubotw_converter.converter -s "%%~A"
)
echo.
echo Processed %COUNTER% mods.
echo.
pause
