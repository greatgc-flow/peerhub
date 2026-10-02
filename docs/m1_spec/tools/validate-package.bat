@echo off
setlocal
python "%~dp0validate_package.py"
exit /b %ERRORLEVEL%
