# Test it from the command line

Calls the OCR service with PowerShell only, no admin page. Port 29262. Replace `<...>` values. The commands were written from the code and not yet run against a live server.

## Get a token

Every call except `/health` needs a token while `SERVICE_ADMIN_PASSWORD` is set. Admin calls take the admin password itself; a token works only on `/v1/*`. Create one token and reuse it. Each `POST /admin/tokens` call makes a new one, so do not put it in a loop. Revoke extras on the admin page.

```powershell
$base = "http://127.0.0.1:29262"
$pw   = @{ "X-Internal-Token" = "<SERVICE_ADMIN_PASSWORD>" }
$tok  = (Invoke-RestMethod -Method Post -Uri "$base/admin/tokens" -Headers $pw -ContentType "application/json" -Body '{"name":"cli-test"}').token
$tok
$h    = @{ "X-Internal-Token" = $tok }
Invoke-RestMethod "$base/health"
```

`$pw` (the admin password) is for `/admin/*` only. `$h` (the token) is for `/v1/*` only; using the password there gives `Invalid token`. `$tok` prints once: save it to skip this step next time, then set `$h` from it. With no admin password set there is no auth: skip `-Headers $h`.

## Call it

```powershell
Invoke-RestMethod "$base/v1/device" -Headers $h       # which model this machine uses, and why
Invoke-RestMethod -Method Post -Uri "$base/v1/ocr" -Headers $h -ContentType "image/png" -InFile "<page.png>"
```

The answer has `text`, `format` (`markdown` from the GPU model, `text` from the CPU model), `model`, `device` and `duration_ms`. Use `image/jpeg` or `image/webp` for those types; the service reads the file's first bytes, not the header.

## Problems

| Answer | Cause |
|---|---|
| `401` | Wrong value: admin routes need the admin password, `/v1/*` needs a token. |
| `403` | The token's *projects* list does not include the model this machine uses (`paddleocr-vl` on a GPU machine, `pp-ocrv5` otherwise). |
| `415` | The body is not a PNG, JPEG or WebP image. |
| `413` | The image is over the *Largest image* setting. |
| `429` | Token rate used up or too many calls waiting; wait `Retry-After` seconds. |
| `503` | The model is not loaded: weights not downloaded, or still loading. |
| connection refused | Not running, or another port; see the run-path guides. |

Before first inference, follow [Downloaded weights](guide-web-ui.md#downloaded-weights): open `/admin/` → Updates → Download. Startup never downloads weights.
