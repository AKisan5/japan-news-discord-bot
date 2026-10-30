# 日本語ニュース Discord 自動投稿ボット

国内外の RSS から新着記事を集め、Claude が重要な記事を最大15本選び、記事本文に基づく日本語ブリーフィングを Discord のプレーンテキストで投稿します。毎朝 07:55 JST と夜 22:00 JST に GitHub Actions で実行します。
Cooper News 形式で「背景 → 何が起きたか → なぜ重要か」を3〜6文の地の文にまとめ、記事リンクを添えます。Anthropic API の利用料金が発生します。

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

`config/feeds.yml` の主な設定:

```yaml
settings:
  max_briefing_items: 15  # 選定上限（1〜50）
  claude_timeout: 180
  article_timeout: 20
  max_items_per_feed: 5
```

RSS取得・URL重複排除の後、1回目のClaude呼び出しで見出し・RSS要約・URL・フィード名だけから重要記事のIDとURLを選びます。AIの重要な動きと国内外の主要紙、一次情報を優先し、重複する話題や軽微な記事を除きます。上限を埋めるための選定はしません。

選ばれたURLだけをhttpxで取得し、trafilaturaで本文を抽出します（最大6,000文字）。Google NewsのHTTPリダイレクト、旧形式の埋め込みURL、ページ内の配信元リンクは可能な範囲で解決します。解決不能・取得失敗・400文字未満の場合は本文を空にし、Claudeには未確認の事実を補わず、省略するか「本文未確認」と明記するよう指示します。記事本文は命令ではなく信頼しない入力データとして扱います。

2回目のClaude呼び出しで、選定記事の見出し・配信元・URL・本文から一つの日本語Markdownブリーフィングを作成します。冒頭は `📰 **ニュース・ブリーフィング YYYY-MM-DD**` と当日の主題・本数。カテゴリは **🤖 AI → 💻 テクノロジー → 📈 経済・市場 → 🌍 国際 → 🇯🇵 国内 → 💰 Web3・金融** の固定順で、空のカテゴリは省略します。各記事には通し番号と日本語タイトル、背景・出来事・重要性を説明する段落、配信元リンクを添えます。

Discordには埋め込みを送らず、段落境界を優先して1,900文字以内に分割したcontentを `wait=true` で投稿します。長すぎる段落は追加分割します。最後のメッセージに「読んだら✅を付けてください」を付けます。`discord.header` と旧埋め込み設定は使用しません。成功したメッセージIDは既読リマインダー用にすべて追跡します。

新着がなければAPIを呼びません。キー未設定は終了コード1、Claudeの失敗・不正出力は終了コード2で投稿せず、処理済み状態も変更しません。選定0件は投稿せず、新着URLを処理済みにします。ブリーフィング全体の送信成功後、選外を含む全新着URLを `state/posted_links.json` に記録します。途中で送信失敗した場合もニュース状態は変更しません。成功したチャンクの未読追跡は残り、次回はブリーフィング全体を再試行するため、一部が重複投稿される可能性があります。

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

Webhookは `wait=true` で投稿IDを取得し、成功した各メッセージを `state/pending_reads.json` に保存します。最後の投稿の案内に従い、読んだ各メッセージに✅（U+2705）を付けてください。

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

## Reddit (OAuth API)

Reddit feeds (`type: reddit` in `config/feeds.yml`) use Reddit's official API with app-only OAuth.

1. Go to https://www.reddit.com/prefs/apps → "create another app…" → type **script**
   (redirect URI can be `http://localhost:8080`).
2. Copy the client ID (string under the app name) and the secret.
3. In GitHub: Settings → Secrets and variables → Actions → add
   `REDDIT_CLIENT_ID` and `REDDIT_CLIENT_SECRET`.
   Optional: `REDDIT_USER_AGENT` env (defaults to `settings.user_agent`).

If the secrets are missing, Reddit feeds are skipped with a warning and other feeds still post.
