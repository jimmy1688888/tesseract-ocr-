@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

REM ============================================================
REM  OCR pipeline 憑證設定腳本
REM  用途：新機器首次使用時執行一次，設定 ADC + 服務帳戶模擬
REM  注意：登入步驟必須由使用者本人輸入自己的公司帳號密碼
REM ============================================================

REM ── 請先修改以下兩行為實際值 ──────────────────────────────
set "SA_EMAIL=jimmy-468@temporal-ring-494709-e7.iam.gserviceaccount.com"
set "PROJECT_ID=temporal-ring-494709-e7"
REM ──────────────────────────────────────────────────────────

echo.
echo ============================================================
echo   OCR pipeline 憑證設定
echo ============================================================
echo   服務帳戶 : %SA_EMAIL%
echo   計費專案 : %PROJECT_ID%
echo ============================================================
echo.

set "NOTSET="
if "%SA_EMAIL%"=="你的SA@你的專案.iam.gserviceaccount.com" set "NOTSET=1"
if "%PROJECT_ID%"=="你的專案ID" set "NOTSET=1"
if defined NOTSET (
    echo [錯誤] 尚未設定 SA_EMAIL 與 PROJECT_ID
    echo        請用記事本開啟本檔案，修改最上方那兩行後再執行。
    goto :end
)

REM ── 步驟 1：檢查 gcloud CLI ────────────────────────────────
echo [1/5] 檢查 Google Cloud CLI ...
where gcloud >nul 2>&1
if errorlevel 1 (
    echo   [失敗] 找不到 gcloud 指令。
    echo.
    echo   請先安裝 Google Cloud CLI：
    echo       https://cloud.google.com/sdk/docs/install
    echo   安裝完成後「重新開啟」本視窗再執行一次。
    goto :end
)
echo   [OK] 已安裝
echo.

REM ── 步驟 2：檢查殘留的金鑰環境變數 ─────────────────────────
echo [2/5] 檢查 GOOGLE_APPLICATION_CREDENTIALS 環境變數 ...
if defined GOOGLE_APPLICATION_CREDENTIALS (
    echo   [警告] 偵測到殘留設定：%GOOGLE_APPLICATION_CREDENTIALS%
    echo.
    echo   這個變數會「優先於」待會要設定的憑證被採用，
    echo   若不清除，程式會繼續使用舊的金鑰檔，本次設定等於沒生效。
    echo.
    echo   正在移除使用者層級的設定 ...
    reg delete "HKCU\Environment" /F /V GOOGLE_APPLICATION_CREDENTIALS >nul 2>&1
    if errorlevel 1 (
        echo   [注意] 移除失敗，可能設定在「系統變數」層級（需系統管理員權限）。
        echo          請手動至：系統內容 - 進階 - 環境變數，刪除該筆後重跑本腳本。
    ) else (
        echo   [OK] 已移除。本腳本結束後請「重新開啟」命令視窗使變更生效。
    )
    set "GOOGLE_APPLICATION_CREDENTIALS="
) else (
    echo   [OK] 無殘留設定
)
echo.

REM ── 步驟 3：ADC 登入（需本人操作）──────────────────────────
echo [3/5] 登入並設定服務帳戶模擬 ...
echo.
echo   ============================================================
echo     接下來會開啟瀏覽器，請「使用您自己的公司 Google 帳號」登入。
echo     請勿使用他人帳號 —— 稽核記錄會依此帳號歸屬操作責任。
echo   ============================================================
echo.
pause

call gcloud auth application-default login --impersonate-service-account=%SA_EMAIL%
if errorlevel 1 (
    echo.
    echo   [失敗] 登入未完成。
    echo   若錯誤訊息包含 getAccessToken denied，代表您的帳號尚未取得
    echo   該服務帳戶的「服務帳戶權杖建立者」角色，請聯絡管理者授權。
    goto :end
)
echo   [OK] 登入完成
echo.

REM ── 步驟 4：設定計費專案 ───────────────────────────────────
echo [4/5] 設定計費專案 ...
REM 模擬型 ADC 不能用 gcloud set-quota-project，改為直接把 quota_project_id 寫進 ADC 檔
set "ADC_FILE=%APPDATA%\gcloud\application_default_credentials.json"
python -c "import json,sys; p=sys.argv[1]; d=json.load(open(p,encoding='utf-8')); d['quota_project_id']=sys.argv[2]; json.dump(d,open(p,'w',encoding='utf-8'),indent=2,ensure_ascii=False)" "%ADC_FILE%" "%PROJECT_ID%"
if errorlevel 1 (
    echo   [失敗] 設定計費專案失敗。
    echo   請確認 python 可用、且步驟 3 已成功登入^(ADC 檔存在^)。
    goto :end
)
echo   [OK] 已將 quota_project_id 設定為 %PROJECT_ID%
echo.

REM ── 步驟 5：執行環境驗收 ───────────────────────────────────
echo [5/5] 執行環境驗收 ...
echo.
if not exist "pipeline.py" (
    echo   [略過] 目前目錄下找不到 pipeline.py。
    echo          請將本腳本放到專案資料夾內，或手動切換目錄後執行：
    echo              python pipeline.py --preflight-only
    goto :end
)
python pipeline.py --preflight-only

echo.
echo ============================================================
echo   設定流程結束
echo   若上方檢查全數為 OK，即可開始使用：
echo       python pipeline.py --file docs\某一份.docx --log-level DEBUG
echo ============================================================

:end
echo.
pause
endlocal
