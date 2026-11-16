@echo off
cd /d "%~dp0"

set GIT_EXE=C:\Users\MECHREVO\.workbuddy\vendor\PortableGit\mingw64\bin\git.exe

echo ============================================
echo   Push news-agent-rag to GitHub
echo ============================================
echo.
echo Target: https://github.com/weijin011/news-agent-rag
echo Branch: main
echo.
echo A GitHub sign-in window will pop up on first run.
echo Sign in with your browser. No token needed.
echo.

"%GIT_EXE%" remote -v
echo.

"%GIT_EXE%" push -u origin main

echo.
if errorlevel 1 goto FAIL
echo [OK] Done. Open https://github.com/weijin011/news-agent-rag
echo.
pause
exit /b 0

:FAIL
echo [FAILED] Check: 1) network  2) repo exists on GitHub
echo.
pause
exit /b 1
