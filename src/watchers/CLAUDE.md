# watchers — CLAUDE.md

SUIデイリーと Even G2ウォッチャーの朝刊、そして SUI速報を、Grok Bot を使わずに作って Discord #ニュース に投稿する。本体は GitHub Actions、時刻どおりの起動は Cloudflare Worker `watchers-trigger`、執筆は Claude API（Sonnet 5.5）が担う。

## 経緯と正本

- 2026-11-03 Aki 要求「grokbotを使わないでも…ディスコードには投げられるように」「今のフォーマットを維持」「品質は維持してほしい」
- 承認済みプラン: `C:/Users/2004t/.claude/plans/noble-noodling-kettle.md`
- chat-log: `C:/Aki/my-vault/08_mycontext/chat-logs/2026-11-03_grok-watchers-offload.md`
- 仕様の正本（Grok 時代の記憶ファイル）: `C:/Aki/my-vault/09_grok/sui-daily.md`・`C:/Aki/my-vault/09_grok/even-g2-watcher.md`
- OSS 探索: 情報源の取得はこのリポジトリの既存部品で足りる（feed_fetcher・reddit_fetcher・article_fetcher・discord_poster）。X の読み取りは Claude の Web 検索と fxtwitter API の組み合わせ（Aki 決定「Claudeの検索を先に試す」）。Grok の collect.py は Grok の計算機の中にしか無いため、記憶ファイルの仕様から作り直した

## Aki の決定（2026-11-03）

- X の取得: 最初は Claude の Web 検索を試す方針だった。試験の結果、返った37件はすべて4〜10月の古い投稿で使えなかった。ログインなしのブラウザは X が自動アクセスを 403 で拒んでおり、すり抜けるのはボット検知の回避にあたるため採らない。最終決定は「Xなしで進める」。X の工程は `WATCHER_X_SEARCH=1` のときだけ動く（既定は止める）。X API を入れるなら Aki の判断が先
- 執筆モデル: `claude-sonnet-5-5`
- 起動: GitHub Actions＋Cloudflare（GitHub の schedule は 2.5〜10 時間遅れた実測があるため）

## 2026-11-04 の情報源の追加（Aki 要求「情報量が減ってしまった」）

- 原因（実測）: Grok 版は投稿の URL の約半分が X 由来だった（Even 10/20〜11/03 は 14〜32 本中 6〜11 本、SUI は 6〜20 本中 1〜13 本）。X を止めたうえ、Grok が Web 検索で拾っていたメディア記事・Even Hub・提携先の発表を拾う取得源が無かった。11/04 の Even は、前夜 19:14 の試験投稿で集計期間が13時間に縮んでいた
- Aki 原文: 「evenに関してはevenhubでどのようなものが公開されているのか…調べて欲しい」「サークルあたりとか…テザーみたいなステーブルコイン周りの情報…jpycとかusdcとかusdt」「suiに関してはもうちょっと欲しいしaiエージェント決済とかについてももうちょっと欲しい」
- 足したもの
  - Even Hub の公開ストア API（`/api/v1/storefront/discovery`・`/detail`）。公開ページ `https://hub.evenrealities.com/catalogue` が認証なしで使っている API で、robots.txt は全許可。版ごとの `updated_at`・`changelog` で更新を判定する。一覧 API `/api/v1/apps/list` は 401（アプリ内トークンが要る）なので使わない
  - XR メディアの RSS（UploadVR・Road to VR・Engadget・The Verge・9to5Google・MoguraVR）
  - SUI: USDT・USDC・JPYC の発行残高（DefiLlama）、Tether の RSS、Circle の pressroom・blog（RSS が無いので既読一覧と比べる）、Walrus ブログ、Stripe ブログ・AP2 の GitHub・x402 の npm、暗号資産メディア6紙の RSS（話題で絞る）、節「🤖 AIエージェント決済」
  - 決済の業界紙の RSS（PYMNTS・Finextra・Payments Dive・The Fintech Times・Electronic Payments International・ペイメントナビ）と Cointelegraph の AI タグ。AI エージェント決済の記事は暗号資産メディアより業界紙に多かった（11/04 の実測）
  - Web 記事の検索（`WATCHER_NEWS_SEARCH=1` のときだけ動く・既定は止める）。Claude の Web 検索は URL を挙げるだけで、検索結果に出た URL しか採らない。日付と中身は記事ページの metadata をコードが読んで確かめる。11/04 の dry-run では候補の大半が古く（SUI は30件中1件・Even は30件中0件が期間内）、X と同じ傾向だったので既定で止めた
- 取らないもの: jpyc.co.jp は自動アクセスに 403 を返すので取りに行かない（回避しない）。Google ニュースの RSS は記事へのリンクが Google 経由になり本文を確かめられないので使わない。Bing ニュースの RSS は規約が RSS リーダーでの表示以外の利用を禁じているので使わない
- 集計期間は最短26時間にした（前回投稿が近くても縮めない）。重複は報告済みの URL の除外で防ぐ
- SUI の件数の目安を「8〜12件」から「12〜18件」に上げた。出自: Fable 判断（Aki「もうちょっと情報量は多いほうがいい」と、範囲がステーブルコイン・エージェント決済に広がったため）。目安であり、水増しはしない

## 品質の仕組み（崩さない）

1. **取得は機械で行う**。RSS・API・GitHub の結果と、コードが記事ページから読んだ内容だけを証拠にする（Claude の検索は URL の候補を出すだけ）
2. **X（今は止めている）**。動かす場合は、投稿 URL を見つけてから fxtwitter API で本文・投稿者・時刻を取り直し、時刻が対象期間より前の投稿は捨てる
3. **執筆は構造化出力にする**。項目ごとに title・meta・what・why・urls を受け取り、Markdown はコード（render）で組む。こうすると形式が崩れない
4. **URL を照合する**。執筆結果の URL は証拠の中にあるものだけを残し、無い URL は落とす。そのうえで HTTP 確認をして、404/410 の項目は落とす
5. **報告済みの除外**。`state/watchers/{sui,even}.json` の `reported` に載った URL は候補から外す
6. **投稿しない日**。新しい一次ソースが0件なら投稿しない

## 動かし方

- `python -m src.watchers.run --target even|sui|sui-alert [--dry-run] [--channel news|test]`
- `--dry-run` は投稿も状態の更新もせず、本文を標準出力と `$GITHUB_STEP_SUMMARY` に出す
- `channel=test` は #bot（1365351401173028916）に `DISCORD_BOT_TOKEN` で投稿する。本番は `DISCORD_WEBHOOK_URL`（#ニュース）で、username を上書きする
- 無人の起動（Worker・schedule）の投稿先は、リポジトリ変数 `WATCHER_CHANNEL` で決まる（未設定なら test）。2026-11-03 から `news`。Aki 決定「#ニュースで試す」で、試験も #ニュース で行う。#bot はリポジトリの DISCORD_BOT_TOKEN のボットに書き込み権限が無く 403 になるため
- #bot への投稿も状態ファイルを進める。dry-run と `--replay` は進めない
- 個人の文脈（Aki の好み）は公開リポジトリに書かない。リポジトリ変数 `WATCHER_SUI_CONTEXT`・`WATCHER_EVEN_CONTEXT` から読む

## 費用の歯止め（出自: Fable 判断）

- Web 検索の `max_uses` は X が8、Web 記事が6
- 1回の実行で Claude を呼ぶのは執筆1回（Web 記事の検索を有効にしたときは＋1回）
- Web 記事は1回15件まで、メディア RSS は話題ごとに sui 10・stable 12・agentpay 10 件まで（新しい順）
- Even Hub の詳細取得は1回80件まで
- SUI速報で Claude を呼ぶのは、A1〜A6 のアラートが出たときだけ
- 1回あたりの $ は `usage_tracker` で Actions のサマリーに出す
