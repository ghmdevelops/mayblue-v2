@echo off
rem Entrypoint para uso manual e para o Agendador de Tarefas do Windows.
rem Nao depende de instalacao via pip (o PyPI esta bloqueado nesta rede).
setlocal
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
python -X utf8 -m flow02 %*
endlocal
