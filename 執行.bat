@echo off
chcp 950 >nul
cd /d "%~dp0"
title LinaOCR 執行
echo 開始處理 docs 資料夾內的 .docx ...
echo.
python pipeline.py %*
rem 結束碼見 CONTEXT「一批的結果」:0 跑完、2 被擋下、其他任何值 = 中途出錯(含 Python 沒裝)
set RESULT=%errorlevel%
echo.
echo ============================================
if "%RESULT%"=="0" goto finished
if "%RESULT%"=="2" goto blocked
echo   處理中途發生錯誤，這一批沒有寫入 Google Sheets。
echo   請將上方訊息截圖回報給管理者。
goto done
:finished
echo   處理完成。請到 Google Sheets 查看結果。
goto done
:blocked
echo   沒有處理任何檔案 —— 開跑前被擋下，Google Sheets 沒有變動。
echo   請依上方訊息排除後重跑；看不懂請截圖回報給管理者。
:done
echo ============================================
pause
