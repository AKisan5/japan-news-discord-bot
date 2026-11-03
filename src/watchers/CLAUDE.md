# watchers — CLAUDE.md

SUIデイリーと Even G2ウォッチャーの朝刊、そして SUI速報を、Grok Bot を使わずに作って Discord #ニュース に投稿する。本体は GitHub Actions、時刻どおりの起動は Cloudflare Worker `watchers-trigger`、執筆は Claude API（Sonnet 5.5）が担う。

## 経緯と正本

- 2026-11-03 Aki 要求「grokbotを使わないでも…ディスコードには投げられるように」「今のフォーマットを維持」「品質は維持してほしい」
- 承認済みプラン: `C:/Users/2004t/.claude/plans/noble-noodling-kettle.md`
- chat-log: `C:/Aki/my-vault/08_mycontext/chat-logs/2026-11-03_grok-watchers-offload.md`
- 仕様の正本（Grok 時代の記憶ファイル）: `C:/Aki/my-vault/09_grok/sui-daily.md`・`C:/Aki/my-vault/09_grok/even-g2-watcher.md`
- OSS 探索: 情報源の取得はこのリポジトリの既存部品で足りる（feed_fetcher・reddit_fetcher・article_fetcher・discord_poster）。X の読み取りは Claude の Web 検索と fxtwitter API の組み合わせ（Aki 決定「Claudeの検索を先に試す」）。Grok の collect.py は Grok の計算機の中にしか無いため、記憶ファイルの仕様から作り直した

## Aki の決定（2026-11-03）

- X の取得: Claude の Web 検索を先に試す。足りなければ X API（有料・Aki の判断）
- 執筆モデル: `claude-sonnet-5-5`
- 起動: GitHub Actions＋Cloudflare（GitHub の schedule は 2.5〜10 時間遅れた実測があるため）

## 品質の仕組み（崩さない）

1. **取得は機械で行う**。RSS・API・GitHub の結果だけを証拠にする
2. **X は2段で取る**。Claude の Web 検索で投稿 URL を見つけ、fxtwitter API で本文・投稿者・時刻を取り直す。時刻が対象期間より前の投稿は捨てる
3. **執筆は構造化出力にする**。項目ごとに title・meta・what・why・urls を受け取り、Markdown はコード（render）で組む。こうすると形式が崩れない
4. **URL を照合する**。執筆結果の URL は証拠の中にあるものだけを残し、無い URL は落とす。そのうえで HTTP 確認をして、404/410 の項目は落とす
5. **報告済みの除外**。`state/watchers/{sui,even}.json` の `reported` に載った URL は候補から外す
6. **投稿しない日**。新しい一次ソースが0件なら投稿しない

## 動かし方

- `python -m src.watchers.run --target even|sui|sui-alert [--dry-run] [--channel news|test]`
- `--dry-run` は投稿も状態の更新もせず、本文を標準出力と `$GITHUB_STEP_SUMMARY` に出す
- `channel=test` は #bot（1365351401173028916）に `DISCORD_BOT_TOKEN` で投稿する。本番は `DISCORD_WEBHOOK_URL`（#ニュース）で、username を上書きする
- 無人の起動（Worker・schedule）の投稿先は、リポジトリ変数 `WATCHER_CHANNEL` で決まる（未設定なら test）。Aki が #bot での試験を見て品質を認めたら `news` にする（出自: Aki 承認プランの完了条件）
- #bot への投稿も状態ファイルを進める。dry-run と `--replay` は進めない
- 個人の文脈（Aki の好み）は公開リポジトリに書かない。リポジトリ変数 `WATCHER_SUI_CONTEXT`・`WATCHER_EVEN_CONTEXT` から読む

## 費用の歯止め（出自: Fable 判断）

- Web 検索の `max_uses` は8
- 1回の実行で Claude を呼ぶのは「X 検索1回＋執筆1回」まで
- SUI速報で Claude を呼ぶのは、A1〜A6 のアラートが出たときだけ
- 1回あたりの $ は `usage_tracker` で Actions のサマリーに出す
