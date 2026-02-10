# sss-tiktok

Projeto em Python para extrair informacoes de videos do TikTok via [ssstik.io](https://ssstik.io/) e:

- baixar arquivo (CLI)
- expor uma API HTTP que retorna resumo completo em JSON

## Requisitos

- Python 3.10+
- `pip`

## Instalacao

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

O Chromium do Playwright e instalado automaticamente na primeira execucao, se necessario.

## CLI (download)

```bash
python3 main.py "https://www.tiktok.com/@user/video/123?is_from_webapp=1&sender_device=pc"
```

Prioridade de download:

- `Sem marca d'agua` -> `downloads/tiktok.mp4`
- `Sem marca d'agua HD` -> `downloads/tiktok_hd.mp4`
- `Download MP3` -> `downloads/tiktok.mp3`

## API HTTP

Iniciar servidor:

```bash
python3 api.py --host 0.0.0.0 --port 8000
```

Healthcheck:

```bash
curl "http://127.0.0.1:8000/health"
```

Extracao (GET):

```bash
curl -G "http://127.0.0.1:8000/extract" \
  --data-urlencode "url=https://www.tiktok.com/@user/video/123?is_from_webapp=1&sender_device=pc" \
  --data-urlencode "timeout_seconds=60"
```

Extracao (POST):

```bash
curl -X POST "http://127.0.0.1:8000/extract" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://www.tiktok.com/@user/video/123?is_from_webapp=1&sender_device=pc","timeout_seconds":60}'
```

## Rodando em segundo plano com systemd

1. Copie o unit file para o `systemd` de usuario:

```bash
mkdir -p ~/.config/systemd/user
cp deploy/systemd/sss-tiktok-api.service ~/.config/systemd/user/
```

2. (Opcional) sobrescreva configuracoes em `~/.config/sss-tiktok-api.env`:

```bash
cat > ~/.config/sss-tiktok-api.env <<'EOF'
PROJECT_DIR=/home/kaiky/Área de trabalho/sss-tiktok
HOST=0.0.0.0
PORT=8000
EOF
```

3. Ative e inicie o servico:

```bash
systemctl --user daemon-reload
systemctl --user enable --now sss-tiktok-api.service
```

4. Comandos uteis:

```bash
systemctl --user status sss-tiktok-api.service
systemctl --user restart sss-tiktok-api.service
systemctl --user stop sss-tiktok-api.service
journalctl --user -u sss-tiktok-api.service -f
```

5. Para manter rodando mesmo sem login na sessao:

```bash
sudo loginctl enable-linger "$USER"
```

Resposta JSON inclui:

- validacao da URL TikTok
- identificadores (`username`, `video_id`)
- metadados visiveis do video no ssstik (`author`, `description`, `stats`)
- links dos botoes (`without_watermark`, `without_watermark_hd`, `mp3`)
- link preferencial e tempo total da operacao

## Troubleshooting

- Se a URL tiver `&`, use aspas no shell.
- Se o site ativar anti-bot/reCAPTCHA, tente novamente depois de alguns minutos.
- Se mudar o HTML do ssstik, ajuste seletores em `main.py` e `api.py`.

## Aviso

Use somente para conteudo que voce possui permissao para baixar e processar.
