@echo off
REM start.bat — Phase 1: register project + generate spec.clarified.yaml (Windows)

SET FRAMEWORK_API_URL=%FRAMEWORK_API_URL%
IF "%FRAMEWORK_API_URL%"=="" SET FRAMEWORK_API_URL=http://localhost:7001

echo [start.bat] Posting spec.yaml to framework-api...

agentic-research start --spec "%CD%\spec.yaml" --out "%CD%\spec.clarified.yaml" --api "%FRAMEWORK_API_URL%"
