# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## O que é

Downloader de aulas em vídeo da Hotmart (plataforma `app-vlc.hotmart.com`). Automatiza o Chrome via Selenium para fazer login, navegar entre aulas de um curso, interceptar as URLs HLS (`.m3u8`) do tráfego de rede e baixar cada vídeo com `yt-dlp`, convertendo para MP4 via `ffmpeg`.

## Comandos

```powershell
# Ativar venv (Windows)
.venv\Scripts\activate

# Instalar dependências
pip install -r requirements.txt

# Rodar (script interativo — pede URLs e config via input())
python app.py
python inner.py
```

Não há testes, lint ou build. O `ffmpeg.exe` (~148 MB) já está versionado na raiz e é detectado automaticamente.

## Arquitetura

Tudo vive em uma única classe `HotmartDownloader`, **duplicada** em `app.py` e `inner.py`. Os dois arquivos são idênticos exceto pelos **XPaths** de scraping — cada um corresponde a um layout diferente do player Hotmart. Ao corrigir lógica, aplique em ambos os arquivos; ao ajustar seletores quebrados, edite só o arquivo do layout em questão.

Seletores que divergem entre os arquivos:
- `get_lesson_title` — XPath do `<h1>` do título
- `click_play_button` — último seletor da lista de fallbacks
- `click_next_lesson_button` — XPath do botão "próxima aula"

### Fluxo (`process_course_auto`)
1. `setup_driver` — Chrome com flags anti-detecção + `goog:loggingPrefs` performance logs ligados (essencial: é daí que saem as URLs de vídeo).
2. `manual_login` — abre a página de login e espera (polling de até 5 min) o usuário logar manualmente; detecta sucesso pela mudança de URL (saída de `/login`, entrada em `club`/`content`).
3. Loop por aula: `navigate_to_lesson` → `click_play_button` → `extract_video_urls_from_logs` → `download_with_ytdlp` → `click_next_lesson_button`. Para quando o botão "próxima" some ou no teto de 200 aulas.

### Pontos críticos
- **`extract_video_urls_from_logs`** é o coração: lê os performance logs do Chrome, filtra eventos `Network.responseReceived` e mantém só URLs `vod-akm.play.hotmart.com` com `.m3u8`, excluindo thumbnails/imagens. Prioriza `master.m3u8`.
- **XPaths são frágeis** — quebram quando a Hotmart muda o front. São o primeiro suspeito quando o scraping falha. Screenshots de debug são salvas em `<download_dir>/screenshots/`.
- **Ambiente corporativo**: suporta `ssl_ignore` (`nocheckcertificate`) e `custom_ca_path` (`ca_certs`) no yt-dlp para proxies com SSL interception.
- Arquivos já existentes em `download_dir` são pulados (idempotente por nome de arquivo).

As pastas `NX Zero a Cem/`, `NZ Zero a Cem/`, `Solid/`, `Inner/` são saídas de downloads anteriores (vídeos `.mp4`), não código.
