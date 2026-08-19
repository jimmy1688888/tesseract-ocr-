@echo off
chcp 65001 >nul
cd /d "%~dp0"
title LinaOCRproject - 重新登入 Google
set PYTHONIOENCODING=utf-8

REM ============================================================
REM  重新登入 Google（LinaOCRproject）
REM
REM  只做一件事：重新產生 ADC，也就是
REM      gcloud auth application-default login
REM  然後跑本專案的前置檢查確認結果。
REM
REM  這支「不是」首次設定。首次設定請執行 gcp-identity\setup-google-adc.bat，
REM  那支還會安裝共用套件 gcp-identity、清除殘留的金鑰檔設定。
REM
REM  ADC 是機器全域的單一檔案，所以本檔雖然放在本專案資料夾，跑一次會讓
REM  這台電腦上三個專案同時恢復。放進各專案只是為了讓人在原地就找得到。
REM
REM  與同資料夾的「使用者認證.bat」不同：那支是已停用的舊做法（登入時模擬），
REM  本檔才是現行做法。
REM ============================================================

echo.
echo ============================================================
echo   重新登入 Google - LinaOCRproject
echo ============================================================
echo.
echo   什麼時候需要跑這支：
echo     執行程式時「模擬服務帳號」那一項失敗，或錯誤訊息裡出現
echo     invalid_grant / reauth，代表你的 Google 登入已經過期。
echo.
echo   跑這一次，這台電腦上的三個專案
echo   （foreign-worker-query、LinaOCRproject、esther-ocr）會同時恢復，
echo   不需要每個專案各跑一次。
echo.

REM ── 1. gcloud ─────────────────────────────────────────────
echo [1/4] 檢查 Google Cloud CLI ...
where gcloud >nul 2>&1
if errorlevel 1 (
    echo   [失敗] 找不到 gcloud 指令。
    echo.
    echo   這台電腦可能還沒做過首次設定，或安裝後尚未重新開啟視窗。
    echo   請改執行：gcp-identity\setup-google-adc.bat
    goto :end
)
echo   [OK] 已安裝
echo.

REM ── 2. Python 與共用套件 ──────────────────────────────────
REM  少了 gcp-identity，重新登入救不了——程式在 import 就會失敗。
echo [2/4] 檢查共用套件 gcp-identity ...
where python >nul 2>&1
if errorlevel 1 (
    echo   [失敗] 找不到 python 指令。
    echo   請改執行：gcp-identity\setup-google-adc.bat
    goto :end
)
python -c "import gcp_identity" >nul 2>&1
if errorlevel 1 (
    echo   [失敗] 共用套件 gcp-identity 尚未安裝，重新登入解決不了這個問題。
    echo.
    echo   請改執行：gcp-identity\setup-google-adc.bat
    echo   那支會安裝套件並一併完成登入，跑完就不必再回來跑本檔。
    goto :end
)
echo   [OK] 已安裝
echo.

REM ── 殘留的金鑰檔設定 ──────────────────────────────────────
REM  google.auth.default() 會「優先」採用這個環境變數。它若還在，
REM  重新登入等於白做，而且表面上一切正常——最難察覺的壞法。
if defined GOOGLE_APPLICATION_CREDENTIALS (
    echo   [警告] 偵測到 GOOGLE_APPLICATION_CREDENTIALS=%GOOGLE_APPLICATION_CREDENTIALS%
    echo          這筆設定會蓋過你即將重新產生的登入，讓本次登入沒有效果。
    echo          請改執行 gcp-identity\setup-google-adc.bat，由它負責清除。
    echo.
    pause
)

REM ── 3. 登入 ───────────────────────────────────────────────
echo [3/4] 登入 Google ...
echo.
echo   ============================================================
echo     接下來會開啟瀏覽器，請「使用您自己的公司 Google 帳號」登入。
echo     請勿使用他人帳號 —— 稽核記錄將依此帳號歸屬操作責任。
echo   ============================================================
echo.
pause

REM  刻意「不加」--impersonate-service-account：模擬由程式負責，
REM  在登入時指定會變成雙層模擬而失敗
REM  （見 foreign-worker-query\docs\adr\0002）。
call gcloud auth application-default login
if errorlevel 1 (
    echo.
    echo   [失敗] 登入未完成，請重新執行本檔。
    goto :end
)
echo   [OK] 登入完成
echo.

REM ── 4. 驗證 ───────────────────────────────────────────────
echo [4/4] 確認結果 ...
echo.
python pipeline.py --preflight-only

echo.
echo ============================================================
echo   上方每一項都打勾，就代表登入已恢復，可以照常執行。
echo.
echo   若試算表那項顯示權限不足，那不是登入問題，
echo   是試算表還沒分享給服務帳號 lina-ocr@extreme-display-505910-s8...，
echo   請把該段訊息轉給管理者。
echo ============================================================

:end
echo.
pause
