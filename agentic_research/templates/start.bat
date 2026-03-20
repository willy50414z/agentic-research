@echo off
REM start.bat — spec review: moves card to Spec Pending Review, starts graph if clean (Windows)

SET FRAMEWORK_API_URL=%FRAMEWORK_API_URL%
IF "%FRAMEWORK_API_URL%"=="" SET FRAMEWORK_API_URL=http://localhost:7001

echo [start.bat] Submitting spec.md to framework-api...

agentic-research start --spec "%CD%\spec.md" --out "%CD%\spec.clarified.md" --api "%FRAMEWORK_API_URL%"
