@echo off
REM Abre o Chrome instalado no PC usando o perfil padrao (com as sessoes/logins salvos)
REM e navega direto para o TikTok, para uso manual.
REM
REM A automacao por codigo (electron/tiktok_uploader.js) NAO usa este perfil --
REM desde o Chrome 136 o Google bloqueia debug remoto no perfil padrao por
REM seguranca, entao o script usa um perfil Chrome dedicado e separado.

set CHROME_PATH="C:\Program Files\Google\Chrome\Application\chrome.exe"

if not exist %CHROME_PATH% (
    echo Chrome nao encontrado em %CHROME_PATH%
    pause
    exit /b 1
)

start "" %CHROME_PATH% --profile-directory="Default" "https://www.tiktok.com/tiktokstudio/upload?from=creator_center&tab=video"
