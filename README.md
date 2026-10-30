# 日本語ニュース Discord 自動投稿ボット

国内外の RSS フィードから新着記事を取得し、Claude が重要なニュースを選び、日本語の短い見出し・2〜4文の要約・重要な理由・カテゴリを Discord に投稿します。毎朝 07:55 JST と夜 22:00 JST に GitHub Actions で実行します。
記事本文は取得せず、フィードの見出しと要約だけを使います。Anthropic API の利用料金が発生します。英語の見出し一覧へのフォールバックはありません。

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
   | `ANTHROPIC_API_KEY` | Anthropic API キー（重要ニュース選定・日本語要約に必須） |
   | `DISCORD_BOT_TOKEN` | Discord Bot のトークン（✅確認・リマインダー用） |

4. **Actions を有効化**
   **Actions タブ** を開き、"I understand my workflows, go ahead and enable them" をクリックします。

Bot をサーバーに追加し、投稿先で「チャンネルを見る」「メッセージ履歴を読む」「メッセージを送信」の権限を付与してください。常駐プロセスは不要です。トークンやWebhook URLはGitHub Secrets／環境変数でのみ管理し、リポジトリ内のファイルに保存しないでください。

既定の投稿先は #ニュース（`1290456657653928048`）、サーバーは `1173405237390868610` です。変更時はWebhookの投稿先も合わせ、Actions Variables の `DISCORD_CHANNEL_ID`（投稿追跡先）と `DISCORD_GUILD_ID`（ジャンプリンク）を設定してください。ローカルでは同名の環境変数を使用します。

Actions の書き込み権限を有効にしてください（Settings → Actions → General → Workflow permissions → Read and write permissions）。スケジュール実行には、変更をデフォルトブランチへ取り込む必要があります。

---

## Claude による選定・要約

`ANTHROPIC_API_KEY` は GitHub Actions Secret またはローカルの環境変数で設定します。キーをファイルやログに保存しないでください。既定モデルは `claude-haiku-4-5-20251001`。Actions Variables の `ANTHROPIC_MODEL`（ローカルでは同名の環境変数）で変更できます。

`config/feeds.yml` の `settings.max_curated` は投稿上限（既定50、1〜50）、`settings.claude_timeout` はAPIタイムアウト秒数（既定120）です。35件までを1回のAPI呼び出しで処理し、それを超える場合は分割します。全結果を重要度順（同点ならAI優先）に並べ、重複URLを除いて上限を適用します。各フィードから最大5件取得し、重要な記事が少ない場合は50件未満になります。AI・テクノロジー・フィンテック・Web3・市場を中心に、一次情報を優先し、軽微な記事や重複する話題を除きます。

記事内容から固定カテゴリを選び、AI → テクノロジー → 経済・市場 → 国際 → 国内 → Web3・金融の順で投稿します。カテゴリ内は重要度順、1メッセージ最大5件です。カテゴリの最初の投稿に件数、後続に「続き」を表示し、各投稿で✅を案内します。カテゴリ見出しが優先され、設定の discord.header は使いません。主要な海外紙・日本紙とAI専門フィードを含み、見出しとRSS本文の事実・重要な理由を日本語で要約します。

新着がなければAPIを呼びません。新着があるのにキーが未設定の場合は終了コード1、API失敗や不正JSONでは終了コード2で投稿せず、処理済み状態も変更しません。重要記事が0件なら投稿せず、新着URLを処理済みにします。選外の記事も `state/posted_links.json` に記録するため、毎回同じ記事を選定し直しません。Discord送信が失敗した記事だけは再試行のため未処理に残します。

Claude の要約は提供された見出し・要約に基づくため、詳細は記事リンクで確認してください。投稿は「📰 本日の重要ニュース（Claude要約）」で始まり、既存の✅案内と既読リマインダーを使います。

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
# ANTHROPIC_API_KEY を環境変数に設定してから実行
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
python -m src.main

# DISCORD_BOT_TOKEN を環境変数に設定してから実行（実際にリマインドします）
python -m src.check_reminder
```

---

## 注意事項

- **GitHub Actions の cron はベストエフォート**です。実行時刻が数分〜十数分遅延することがあります。厳密な時刻を要件とする用途には向きません。
- 1 フィードのフェッチ失敗は他フィードの投稿に影響しません。自動的に次回実行で復帰します。
- 処理済みURLは `state/posted_links.json` で管理し、保持件数（既定1000件）の範囲で再処理を防ぎます。

---

## ライセンス

MIT License — 詳細は [LICENSE](LICENSE) を参照してください。
