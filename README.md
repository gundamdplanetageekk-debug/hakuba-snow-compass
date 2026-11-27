# Hakuba Snow Compass — Phase 1 MVP

白馬バレー全10スキー場向けの英語Webアプリです。気象データから「今日（14時以降は明日）のおすすめゲレンデ」を自動でランキングします。BCモードは標高別の風、レッドフラッグ、ゲート、ルールを1画面にまとめています。

## ファイル構成
```
index.html                 UI・CONFIG・描画（英語UI。冒頭の CONFIG だけ編集すればOK）
engine.js                  スコアリング・BC判定ロジック（DOM非依存・Nodeでテスト可）
data/resorts.json          10スキー場の地形プロファイル（標高・方角・ツリー・風耐性・BCゲート）
data/safety.json           BCゲート有無、ローカルルール、JAN・Compass・警察リンク、緊急番号
data/shuttle.json          シャトル路線体系（時刻表は自動取得後に入る）
scripts/fetch_weather.py   Open-Meteo → weather.json（1時間1回、全スキー場を1リクエスト）
scripts/update_lifts.py    hakubavalley.com 各スキー場リフト運行ページ → lifts.json
scripts/update_shuttle.py  公式シャトルページのPDF変更検知 → 時刻抽出 → レビュー依頼Issue
.github/workflows/update-data.yml  上記の定期実行（結果は data ブランチへ）
```

## ローカルで動かす
`index.html` をダブルクリックで開くと、JSONの読み込みがブラウザに止められます。フォルダで次を実行してください。
```
python3 -m http.server 8000   →  http://localhost:8000
```
weather.json がまだ無いときは、ブラウザから Open-Meteo を直接呼んで表示します（`ALLOW_LIVE_FALLBACK`）。

## 公開手順（推奨：Cloudflare Pages ＋ GitHub）
1. GitHubで**public**リポジトリを作り、このフォルダ一式をpushします（rawのJSON配信と、Actions無料枠を使うため）。
2. Cloudflare Dashboard → Workers & Pages → Create → Pages → Connect to Git でリポジトリを選びます。
   Framework preset: **None** / Build command: **空欄** / Output directory: **/** のまま Deploy します。
3. Settings → Builds → Branch control で、Production branch を `main` にし、Preview branches を **None** にします（`data` ブランチの毎時更新でビルドを走らせないため）。
4. GitHub → Actions → `update-data` → **Run workflow**（task: all）を1回実行します。`data` ブランチが作られます。
5. `index.html` の CONFIG を書き換えます。
   `DATA_BASE_URL: 'https://raw.githubusercontent.com/<ユーザー名>/<リポジトリ名>/data/'`
   これでサイトの再デプロイなしに、天気・リフト・シャトルが毎時更新されます。
6. アフィリエイトURLを `CONFIG.LINKS` に差し替えてpushします。旅行ガイド（Gumroad）が完成したら `GUMROAD_GUIDE` のURLを入れ、`SHOW_GUMROAD: true` にします（Resort Modeの「Sort your day」欄に表示されます）。

Vercelを使う場合も、手順2を「Vercel → Add New Project → Import」に置き換えるだけで動きます（ビルド設定は不要）。ただし下記の注意を読んでください。

## ⚠️ 収益化前に必ず対応すること
- **Open-Meteo**：無料APIは非商用専用です。アフィリエイト収益があるサイトは商用にあたるので、有料プラン（API Standard）を契約し、キーを GitHub → Settings → Secrets → `OPEN_METEO_API_KEY` に登録してください。スクリプトが自動で商用エンドポイントに切り替わります。キーはブラウザ側（CONFIG）に**入れない**でください。あわせて、サイトの来訪者がAPIを直接呼ばないように、運用開始後は `ALLOW_LIVE_FALLBACK: false` にすることを推奨します。
- **Vercel Hobby（無料）**：非商用限定で、アフィリエイトが主目的のサイトや広告付きサイトは商用扱いです。Vercelを使うならPro（有料）にしてください。無料で運用するならCloudflare Pagesを推奨します。
- **ステマ規制**：フッターにアフィリエイト表記を入れ、リンクには `rel="sponsored"` を付けてあります。表記は消さないでください。

## スコアリング・アルゴリズム（Resort Mode）
各スキー場の**山頂・山麓の2地点**を、標高補正つきで取得します（10場×2＝20地点を1リクエスト）。対象は当日（または翌日）08–16時です。

| 要素 | 内容 |
|---|---|
| 新雪 | 直前24hの降雪×地形係数 `snowBias`（北部コルチナ1.35〜南部爺ガ岳0.8）。逓減カーブで最大40点 |
| 雪質 | 山頂気温 −6℃以下で+15（ドライ）、0℃超で−10。山麓で雨なら−10 |
| 風 | 山頂ガスト（最大）が強いほど減点。`upperHoldGust` 超で上部リフト運休想定、`fullHoldGust` 超で全体×0.35 |
| シェルター | 強風・視界不良・強降雪の日は、ツリー度（0–3）×5点を加点 |
| 標高差 | 穏やかな日だけ加点。運休想定時は `fallbackTop` で計算 |
| 実運行 | lifts.json が新しければ、実際の稼働率で補正（推測より優先） |

検証として3つのシナリオを模擬データで確認しました。晴天無風では八方が1位、ドカ雪ではコルチナ→乗鞍→ツリーのある所の順、強風では八方・五竜が最下位になり、低標高・樹林帯が上位に来ます。どれも定説どおりの並びです。係数はすべて resorts.json にあるので、シーズン中の実績を見てJSONだけで調整できます。

**BC Mode** は、850hPa（約1,500m）と700hPa（約3,000m）の風・気温、凍結高度、24/72h新雪から「風成スラブの付きやすい斜面方位（風下）」と気象レッドフラッグを出します。**雪崩危険度は出しません**（JANの領域）。初回は3項目の同意チェックが必要です（同意はlocalStorageに保存）。

## 自動更新の仕組み
- **毎時**：天気。JST 6–17時はリフト運行状況も取得します。lifts.json は公式の一覧ページから各場のURLを自動で見つけるので、URLが変わっても追従します。
- **毎日 3:40 JST**：シャトル公式ページのPDFのハッシュを比較し、変化があれば時刻表を抽出して `needsReview: true` にし、Issueを自動作成します。人が数件を照合して `false` にするまで、アプリは抽出した時刻を**表示しません**（観光客の足に関わるため、シーズン1回・1分の確認ゲートを残しています）。
- `data` ブランチは常に1コミットに上書きするので、リポジトリは肥大化しません。

## 未検証・次フェーズ
- このサンドボックスは外部ネットワークに出られないため、Open-Meteo、hakubavalley.com、PDFへの実通信テストはしていません。UIとロジックは模擬データで検証済みです。最初の `Run workflow` のログで確認してください。
- resorts.json のうち `confidence: "approx"` の場（岩岳・栂池・乗鞍・鹿島槍・爺ガ岳・さのさか）は、座標と一部の標高が概算です。
- 食事・医療施設は、Phase 2 で Places API 等から自動収集する想定です（現状は緊急番号のみ）。
