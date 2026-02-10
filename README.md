# sss-tiktok

Script em Python para baixar video (sem marca d'agua, HD, ou MP3) via [ssstik.io](https://ssstik.io/) a partir de uma URL do TikTok.

## Requisitos

- Python 3.10+
- `pip`
- Linux/macOS/Windows

## Instalacao

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

O Chromium do Playwright e instalado automaticamente na primeira execucao, se necessario.

## Uso

```bash
python3 main.py "https://www.tiktok.com/@user/video/123?is_from_webapp=1&sender_device=pc"
```

Saida padrao:

- arquivo salvo em `downloads/tiktok.mp4` (prioridade: sem marca d'agua)
- fallback para `downloads/tiktok_hd.mp4`
- fallback final para `downloads/tiktok.mp3`

## Como funciona

1. Abre o `ssstik.io/pt-1` com Playwright em modo headless.
2. Envia a URL do TikTok no formulario.
3. Localiza os botoes de download (`without_watermark`, `without_watermark_hd`, `music`).
4. Baixa o arquivo escolhido com `requests`.

## Troubleshooting

- Se a URL tiver `&`, use sempre entre aspas.
- Se o site ativar anti-bot/reCAPTCHA, tente novamente depois de alguns minutos.
- Se mudar o HTML do ssstik, atualize os seletores em `main.py`.

## Aviso

Use somente para conteudo que voce tem permissao para baixar e reutilizar.
