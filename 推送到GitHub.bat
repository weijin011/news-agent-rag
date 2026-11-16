@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo   推送 news-agent-rag 到 GitHub
echo ============================================
echo.
echo 目标: https://github.com/weijin011/news-agent-rag
echo 分支: main
echo.
echo 第一次会弹出 GitHub 登录窗口，用浏览器 OAuth 登录即可，
echo 不需要手动生成 Token。登录成功后会自动记住，以后双击直接推。
echo.

pause

git push -u origin main

echo.
if errorlevel 1 (
    echo [失败] 可能原因：
    echo   1. 网络连不上 github.com
    echo   2. GitHub 上仓库还没建（去 https://github.com/new 建 news-agent-rag，
    echo      不要勾 README / LICENSE / .gitignore）
    echo   3. 登录窗口被挡住，或账号没仓库权限
) else (
    echo [成功] 打开 https://github.com/weijin011/news-agent-rag 查看
)

echo.
pause
