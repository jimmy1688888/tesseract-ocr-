@echo off
chcp 65001 >nul

REM ============================================================
REM  此腳本已停用。
REM
REM  舊做法是在登入時就指定模擬（--impersonate-service-account），但 ADC 是
REM  機器全域的單一檔案、只能記一個模擬目標，多專案各用不同服務帳號時會互相
REM  覆蓋。模擬已改為在程式內進行，見 gcp-identity/README.md 與
REM  foreign-worker-query/docs/adr/0002。
REM
REM  若執行舊做法，會產生「雙層模擬」——程式拿一個已經是模擬的憑據再模擬一次
REM  而失敗，且原生錯誤訊息看起來像權限問題，完全看不出根因。
REM  本檔刻意保留為導引，而不是直接刪除，避免有人憑記憶去找它卻找不到。
REM ============================================================

echo.
echo ============================================================
echo   此腳本已停用，請勿使用。
echo ============================================================
echo.
echo   認證方式已改變：模擬服務帳號改在程式內進行，登入時不再指定模擬目標。
echo.
echo   請改執行共用設定腳本（每台機器一次，供所有專案共用）：
echo.
echo       gcp-identity\setup-google-adc.bat
echo.
echo   本專案的模擬目標寫在 pipeline.py 的 SA_EMAIL，不需要在此設定。
echo.
echo   若你已經執行過舊腳本，請直接執行上面那支共用腳本覆蓋設定；
echo   python pipeline.py --preflight-only 會確認狀態是否正確。
echo.
echo   說明：gcp-identity\README.md
echo         foreign-worker-query\docs\adr\0002
echo.
pause
