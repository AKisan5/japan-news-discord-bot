# 日本語ニュース Discord 自動投稿ボット

日本語ニュースサイトの RSS フィードを毎朝 10:00 JST に Discord へ自動投稿します。  
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
   | `DISCORD_WEBHOOK_URL` | 取得した Webhook URL |

4. **Actions を有効化**  
   **Actions タブ** を開き、"I understand my workflows, go ahead and enable them" をクリックします。

以上で翌朝 10:00 JST から自動投稿が開始されます。

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
    - cron: "0 1 * * *"   # ← UTC 時刻。10:00 JST = 01:00 UTC
```

[crontab.guru](https://crontab.guru/) で希望の時刻を確認できます。

---

## 手動実行

GitHub の **Actions タブ** → **Post Daily News** → **Run workflow** から手動実行できます。  
動作確認や緊急投稿に利用してください。

---

## ローカルでの動作確認

```bash
# 依存関係をインストール
pip install -r requirements.txt

# テスト実行
pytest

# デバッグログで実行 (実際には投稿されます)
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
LOG_LEVEL=DEBUG python -m src.main
```

---

## 注意事項

- **GitHub Actions の cron はベストエフォート**です。実行時刻が数分〜十数分遅延することがあります。厳密な時刻を要件とする用途には向きません。
- 1 フィードのフェッチ失敗は他フィードの投稿に影響しません。自動的に次回実行で復帰します。
- 同じ記事は 2 回以上投稿されません (`state/posted_links.json` で管理)。

---

## ライセンス

MIT License — 詳細は [LICENSE](LICENSE) を参照してください。
