@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo   Build SimpleTranslate (onedir)
echo ========================================
echo.

rem ── [0/5] Pre-checks ──────────────────────────────────────
echo [0/5] Checking dependencies...

rem Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo *** ERROR: Python not found on PATH. Please install Python 3.10+ ***
    pause
    exit /b 1
)

rem Check PyInstaller
python -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo *** ERROR: PyInstaller not installed. Run: pip install pyinstaller ***
    pause
    exit /b 1
)

rem Check spec file
if not exist "SimpleTranslate.spec" (
    echo *** ERROR: SimpleTranslate.spec not found ***
    pause
    exit /b 1
)

rem Check main entry
if not exist "main.py" (
    echo *** ERROR: main.py not found ***
    pause
    exit /b 1
)

rem Quick syntax check on main entry and src/
echo Checking syntax...
python -m py_compile main.py 2>&1
if errorlevel 1 (
    echo *** ERROR: main.py has syntax errors, aborting ***
    pause
    exit /b 1
)
for %%f in (src\*.py) do (
    python -m py_compile "%%f" 2>&1
    if errorlevel 1 (
        echo *** ERROR: %%f has syntax errors, aborting ***
        pause
        exit /b 1
    )
)
echo Pre-checks OK.

rem ── [1/5] Cleaning ────────────────────────────────────────
echo.
echo [1/5] Cleaning old build artifacts...
if exist build rmdir /s /q build
if exist dist  rmdir /s /q dist

rem ── [2/5] PyInstaller Build ───────────────────────────────
echo [2/5] Running PyInstaller...
python -m PyInstaller --noconfirm "SimpleTranslate.spec"

if errorlevel 1 (
    echo.
    echo *** Build failed, check errors above ***
    pause
    exit /b 1
)

rem ── [3/5] Copying Resources ───────────────────────────────
echo [3/5] Copying resources...
if not exist "dist\SimpleTranslate\resources\terms" mkdir "dist\SimpleTranslate\resources\terms"
copy /Y "resources\terms\embedded.json" "dist\SimpleTranslate\resources\terms\" >nul 2>&1
copy /Y "resources\terms\software.json"  "dist\SimpleTranslate\resources\terms\" >nul 2>&1
if exist "config.properties.example" copy /Y "config.properties.example" "dist\SimpleTranslate\" >nul 2>&1
if exist "config.properties"         copy /Y "config.properties"         "dist\SimpleTranslate\" >nul 2>&1
if exist "app.ico"                   copy /Y "app.ico"                   "dist\SimpleTranslate\" >nul 2>&1
if exist "test_manual.pdf"           copy /Y "test_manual.pdf"           "dist\SimpleTranslate\" >nul 2>&1

rem ── [4/5] Validating ──────────────────────────────────────
echo [4/5] Validating build...

if not exist "dist\SimpleTranslate\SimpleTranslate.exe" (
    echo.
    echo *** ERROR: dist\SimpleTranslate\SimpleTranslate.exe not found - build may have failed ***
    pause
    exit /b 1
)

set MISSING=0
if not exist "dist\SimpleTranslate\resources\terms\embedded.json" (
    echo *** WARNING: resources\terms\embedded.json missing in dist
    set /a MISSING=MISSING+1
)
if not exist "dist\SimpleTranslate\resources\terms\software.json" (
    echo *** WARNING: resources\terms\software.json missing in dist
    set /a MISSING=MISSING+1
)
if not exist "dist\SimpleTranslate\config.properties.example" (
    echo *** WARNING: config.properties.example missing in dist
    set /a MISSING=MISSING+1
)

rem Get EXE size
for %%A in ("dist\SimpleTranslate\SimpleTranslate.exe") do set EXE_SIZE=%%~zA
set /a EXE_MB=%EXE_SIZE% / 1048576
echo EXE found: dist\SimpleTranslate\SimpleTranslate.exe (~%EXE_MB% MB)

if %MISSING% gtr 0 (
    echo.
    echo *** %MISSING% resource(s) missing - see warnings above ***
)

rem ── [5/5] Done ────────────────────────────────────────────
echo.
echo [5/5] Done!
echo ========================================
echo   Build OK
echo   exe: dist\SimpleTranslate\SimpleTranslate.exe
echo   Run dist\SimpleTranslate\SimpleTranslate.exe to test
echo ========================================
echo.
pause
