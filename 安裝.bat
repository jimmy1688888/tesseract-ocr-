@echo off
chcp 950 >nul
cd /d "%~dp0"
title LinaOCR 安裝與環境檢查
echo ============================================
echo   LinaOCR 安裝與環境檢查
echo ============================================
echo.

rem ── 1. Python ──────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo [X] 找不到 Python。請先安裝 Python 3.10 以上,
    echo     安裝時務必勾選 "Add python.exe to PATH"。
    echo     下載: https://www.python.org/downloads/
    goto :fail
)
for /f "tokens=2" %%v in ('python --version') do echo [V] Python %%v

rem ── 2. Tesseract ───────────────────────────
set "TESS=C:\Program Files\Tesseract-OCR\tesseract.exe"
if not exist "%TESS%" (
    echo [X] 找不到 Tesseract^(預設路徑 C:\Program Files\Tesseract-OCR\^)。
    echo     下載: https://github.com/UB-Mannheim/tesseract/wiki
    echo     安裝時勾選語言包: Indonesian ^(ind^)、English ^(eng^)
    goto :fail
)
echo [V] Tesseract 已安裝

"%TESS%" --list-langs 2>nul | findstr /x "ind" >nul
if errorlevel 1 (
    echo [X] Tesseract 缺少印尼語言包 ind,請重新安裝並勾選 Indonesian。
    goto :fail
)
"%TESS%" --list-langs 2>nul | findstr /x "eng" >nul
if errorlevel 1 (
    echo [X] Tesseract 缺少英文語言包 eng,請重新安裝並勾選 English。
    goto :fail
)
echo [V] 語言包 ind、eng 齊全

rem ── 3. Python 套件 ─────────────────────────
echo.
echo 安裝 Python 套件中...
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo [X] 套件安裝失敗,請把上方錯誤訊息截圖回報管理者。
    goto :fail
)
echo [V] Python 套件安裝完成

rem ── 4. 本機檔案 ────────────────────────────
echo.
if not exist "docs" mkdir docs
if exist "service_account.json" (echo [V] service_account.json) else (echo [!] 缺 service_account.json — 請向管理者索取,放在本資料夾)
if exist "data\agency_roster.json" (echo [V] data\agency_roster.json) else (echo [!] 缺 data\agency_roster.json — 仲介名冊,缺少時機構欄會留空)
if exist "data\AllData.json" (echo [V] data\AllData.json) else (echo [!] 缺 data\AllData.json — 門牌地址庫,缺少時標準地址欄會留空)

echo.
echo ============================================
echo   檢查完成。上方 [!] 項目補齊後即可使用:
echo   把 .docx 放進 docs 資料夾,雙擊「執行.bat」
echo ============================================
goto :end

:fail
echo.
echo 安裝未完成,請先解決上面 [X] 的問題,再重新雙擊本檔。

:end
echo.
pause
