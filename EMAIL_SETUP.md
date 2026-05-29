# Email 驗證設定

## SMTP 設定（Gmail）

設定檔：`/root/smtp_config.json`



### 產生 Gmail App Password
1. 開啟兩步驟驗證：https://myaccount.google.com/signinoptions/twosv
2. 產生 App Password：https://myaccount.google.com/apppasswords
3. 用 16 碼 App Password 取代上方 `password`

### 關閉驗證（選用）
將 `require_verification` 設為 `false` 即可跳過 Email 驗證。

## 寄信方式
使用標準 SMTP STARTTLS（port 587），透過 Python smtplib 發送。
註冊時產生 64 字元驗證 token，存入 `verify_tokens` 表，連結有效 24 小時。
