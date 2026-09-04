@echo off
rem Atalho de partida: prepara o que falta e abre o painel.
rem Idempotente -- pode rodar quantas vezes quiser.
setlocal
cd /d "%~dp0"

if not exist ".env" (
  echo [1/3] criando .env a partir do exemplo
  copy /y ".env.example" ".env" >nul
) else (
  echo [1/3] .env ja existe
)

if not exist "entrada" (
  echo [2/3] criando entrada/ com os arquivos de exemplo
  mkdir "entrada"
  copy /y "exemplos\*" "entrada\" >nul
) else (
  echo [2/3] entrada/ ja existe
)

if not exist "dados\flow02.sqlite3" (
  echo [3/3] primeira coleta, para o painel abrir com conteudo
  call "%~dp0flow02.cmd" coletar --plataforma mercadolivre
) else (
  echo [3/3] banco ja existe
)

echo.
call "%~dp0flow02.cmd" web %*
endlocal
