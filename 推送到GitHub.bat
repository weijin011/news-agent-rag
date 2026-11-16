@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo   推送 news-agent-rag 到 GitHub
echo ============================================
echo.
echo 目标仓库: https://github.com/weijin011/news-agent-rag.git
echo.
echo 第一次运行会弹出 GitHub 登录窗口：
echo   用户名: weijin011
echo   密码: 不是GitHub登录密码，要用 Personal Access Token
echo   （GitHub - Settings - Developer settings - Personal access tokens - Tokens(classic)
echo     勾选 repo 权限，生成后复制粘贴过来）
echo.

pause

git push -u origin main
if errorlevel 1 (
    echo.
    echo [失败] 推送未成功。常见原因：
    echo   1. 网络访问不了 github.com（国内常见，需要代理）
    echo   2. 仓库还没在 GitHub 上创建
    echo   3. Token 没有 repo 权限
    echo.
    echo 如果远程仓库还不存在，先去 https://github.com/new 创建
    echo 仓库名填 news-agent-rag，不要勾选 README/LICENSE（本地已有）
) else (
    echo.
    echo [成功] 已推送！打开 https://github.com/weijin011/news-agent-rag 查看
)

echo.
pause
