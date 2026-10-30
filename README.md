# 日本語ニュース Discord 自動投稿ボット

国内外の RSS フィードから見出し・リンク・フィード内の要約だけを、毎朝 07:55 JST と夜 22:00 JST に Discord へ自動投稿します。
記事ページの本文取得（有料記事を含む）や LLM は使いません。Grok のトークンも不要です。
GitHub Actions で完結するため、外部サーバー・DB・有料サービスは一切不要です。

---

## 1 分セットアップ

### 手順

1. **このリポジトリを Fork**
   右上の "Fork" ボタンをクリックしてください。

2. **Discord Webhook URL を取得**
   投稿先チャンネルの「チャンネルの編集」→「連携サービス」→「ウェブフック」→「新しいウェブフック」からコピーします。

3. **GitHub Secret を登録**
   Fork したリポジトリの **Settings → Secrets and variables → Actions → New repository secret** で以下を追加します。

   | Name | Value |
   |---|---|
   | `DISCORD_WEBHOOK_URL` | 取得した Webhook URL（ニュース投稿用） |
   | `DISCORD_BOT_TOKEN` | Discord Bot のトークン（✅確認・リマインダー用） |

4. **Actions を有効化**
   **Actions タブ** を開き、"I understand my workflows, go ahead and enable them" をクリックします。

Bot をサーバーに追加し、投稿先で「チャンネルを見る」「メッセージ履歴を読む」「メッセージを送信」の権限を付与してください。常駐プロセスは不要です。トークンやWebhook URLはGitHub Secrets／環境変数でのみ管理し、リポジトリ内のファイルに保存しないでください。

既定の投稿先は #ニュース（`1290456657653928048`）、サーバーは `1173405237390868610` です。変更時はWebhookの投稿先も合わせ、Actions Variables の `DISCORD_CHANNEL_ID`（投稿追跡先）と `DISCORD_GUILD_ID`（ジャンプリンク）を設定してください。ローカルでは同名の環境変数を使用します。

Actions の書き込み権限を有効にしてください（Settings → Actions → General → Workflow permissions → Read and write permissions）。スケジュール実行には、変更をデフォルトブランチへ取り込む必要があります。

---

## フィードの追加・変更

`config/feeds.yml` を編集するだけでフィードを追加・削除できます。コードの変更は不要です。

```yaml
feeds:
  - name: "追加したいフィード名"
    url: "https://example.com/feed.rss"
    category: "カテゴリ名"
    enabled: true
```

`enabled: false` にするとそのフィードは無効化されます。

---

## 投稿時刻の変更

`.github/workflows/post-news.yml` の `cron` 式を書き換えてください。

```yaml
on:
  schedule:
    - cron: "55 22 * * *"  # ← UTC 時刻。07:55 JST = 前日22:55 UTC
```

[crontab.guru](https://crontab.guru/) で希望の時刻を確認できます。

---

## 手動実行

GitHub の **Actions タブ** → **Post Daily News** → **Run workflow** から手動実行できます。
動作確認や緊急投稿に利用してください。

---

## ✅ 既読リマインダー

Webhookは `wait=true` で投稿IDを取得し、成功した各メッセージを `state/pending_reads.json` に保存します。各投稿の案内に従い、読んだメッセージに✅（U+2705）を付けてください。

`check-reminder.yml` は毎日10・12・15・18・21時 JSTに古い投稿から確認します。bot以外の誰かの✅で対象を未読一覧から除き、それまでは各実行につき各投稿へ最大1回、ジャンプリンク付きの返信を送ります。botだけの✅は既読になりません。APIエラーや削除済みメッセージは一覧に残してエラーを報告します。

投稿履歴と未読状態はActionsがコミットします。両ワークフローは同じconcurrency groupで状態更新を直列化します。リマインダーもActionsから手動実行できます。

## ローカルでの動作確認

```bash
# 依存関係をインストール
pip install -r requirements.txt

# テスト実行
pytest

# 実行 (実際に投稿されます)
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
python -m src.main

# DISCORD_BOT_TOKEN を環境変数に設定してから実行（実際にリマインドします）
python -m src.check_reminder
```

---

## 注意事項

- **GitHub Actions の cron はベストエフォート**です。実行時刻が数分〜十数分遅延することがあります。厳密な時刻を要件とする用途には向きません。
- 1 フィードのフェッチ失敗は他フィードの投稿に影響しません。自動的に次回実行で復帰します。
- 同じ記事は 2 回以上投稿されません (`state/posted_links.json` で管理)。

---

## ライセンス

MIT License — 詳細は [LICENSE](LICENSE) を参照してください。
