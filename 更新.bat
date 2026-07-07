@echo off
chcp 950 >nul
cd /d "%~dp0"
title LinaOCR 更新
if not exist ".git" (
    echo [X] 此資料夾不是 git 版本^(可能是 ZIP 解壓版^),無法一鍵更新。
    echo     請向管理者索取新版,或改用 git clone 方式安裝。
    goto :end
)
where git >nul 2>&1
if errorlevel 1 (
    echo [X] 找不到 Git。請先安裝 Git for Windows^(一路下一步即可^):
    echo     https://git-scm.com/download/win
    goto :end
)
echo 從 GitHub 取得最新版...
git pull --ff-only
if errorlevel 1 (
    echo.
    echo [X] 更新失敗,請截圖回報管理者。
    goto :end
)
echo.
echo 同步 Python 套件...
python -m pip install -r requirements.txt -q
echo.
echo [V] 更新完成,目前版本:
git log -1 --oneline
:end
echo.
pause
