@echo off
chcp 950 >nul
cd /d "%~dp0"
title LinaOCR 執行
echo 開始處理 docs 資料夾內的 .docx ...
echo.
python pipeline.py %*
echo.
echo ============================================
echo   處理結束。請到 Google Sheets 查看結果;
echo   若上方有錯誤訊息,請截圖回報管理者。
echo ============================================
pause
