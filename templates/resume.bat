@echo off
REM resume.bat — advance Phase 1 clarification loop or trigger Phase 2 (Windows)

SET FRAMEWORK_API_URL=%FRAMEWORK_API_URL%
IF "%FRAMEWORK_API_URL%"=="" SET FRAMEWORK_API_URL=http://localhost:7001

echo [resume.bat] Advancing workflow...

agentic-research resume --spec-clarified "%CD%\spec.clarified.yaml" --spec "%CD%\spec.yaml" --api "%FRAMEWORK_API_URL%"
