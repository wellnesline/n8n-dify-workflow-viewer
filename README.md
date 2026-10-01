# n8n-dify-workflow-viewer

Tools to build a pipeline on n8n (orchestration) and Dify (AI steps), then deliver its real registered graph as a login-free, read-only viewer page.

新規パイプラインを n8n（工程構築・実行履歴）と Dify（AI 処理）で組み、実際に登録されたグラフから「ログイン不要・閲覧専用」のビューアを作って納品するための PoC 一式です。公開用に整理したサンプル実装です。データはすべて合成です。

## 概要

発注者に「実際に動いている工程」を見せたいとき、管理画面を開放するわけにはいきません。このリポジトリは、n8n と Dify に登録されたワークフローから、値・コード・認証情報を含まない公開用の snapshot だけを取り出し、固定ファイルだけを GET で配信する閲覧ページを作ります。納品物が元の export と一致していることは、匿名 HTTP 取得と SHA256 で検証します。

## 主な機能・設計のポイント

- **公開用 projection**: `viewer.py` は任意のワークフロー値をそのまま通さず、上限を設けた公開用の snapshot に整形します。秘密情報は含めません。
- **固定ファイルだけを配信**: `delivery.py` は許可リストにあるファイルだけを GET で返します（POST は 405、未登録の経路は 404、既定は loopback）。
- **匿名取得での検証**: `verify_viewer.py` は認証なしで取得し、配信バイトと snapshot の一致、未登録経路の 404、POST の 405 を確認して receipt を書きます。receipt が無い・snapshot が古いと `policy.py` が不合格にします。
- **manifest ゲート**: `policy.py` は manifest を検査します（使用フレームワーク、job 契約、`public_view`、`inspection_view`）。フレームワークの省略には、利用者の明示的な許可と理由・参照の記録を要求します。`--future-defaults` で、工程図とコーディング委譲の既定も要求できます。
- **非同期 job 契約のモック**: `bridge.py` は模擬 job を SQLite に保存し、認証付き API で状態と結果を返します。job_id / status / progress / result / error と冪等キーを持ち、冪等キー（`Idempotency-Key`）が必須で、同じキーの依頼には同じ job を返し、同じキーで内容が違う入力は拒否します。トークンは 24 文字以上を要求し、比較は定数時間で行います。本番システム・シェル・モデル API は呼びません。
- **ローカル実行環境**: `deploy.py` が WSL 上の Docker で n8n と Dify を loopback のみで起動し（自動起動・自動再起動なし、`docker compose down -v` は実行しない）、`bootstrap.py` が owner 初期化・ワークフロー登録・E2E を行います。秘密情報は実行環境側に生成され、リポジトリには含まれません。
- **エラー要約のみ表示**: `diagnose.py` は、ノードのエラーを上限付きの要約だけで表示し、実行データ全体や秘密は出しません。

## ディレクトリ構成

```
visual_workflow/
  templates.py         n8n / Dify の秘密情報を含まない export を生成
  bootstrap.py         owner 初期化、export の取り込み、E2E
  deploy.py            WSL + Docker での起動・停止・状態確認
  bridge.py            模擬 job の API（SQLite、認証付き）
  capture_viewer.py    登録済みグラフの取得と公開 projection の保存
  viewer.py            公開用 snapshot と静的サイトの生成
  delivery.py          固定ファイルだけを返す配信サーバー
  verify_viewer.py     匿名取得での検証と receipt の作成
  policy.py            manifest の検査
  audit.py / publish.py / diagnose.py   読み取り専用の確認、納品物の作成、エラー要約
  manifest.json        合成の manifest 例
  generated/           生成済みの n8n / Dify export（合成）
  viewer_generated/    生成済みの閲覧サイト（合成）
  viewer_public/       静的サイトの雛形
  PLAN.md / VIEWER_PLAN.md / README.md   計画と補足
```

`visual_workflow/README.md` に、この PoC の手順と注意をより詳しく書いています。

## 動かし方

必要なもの: Python 3.10 以上、Windows の場合は WSL と Docker（n8n・Dify の起動用）。

```powershell
wsl -e env POC_DIR=<dir> python3 visual_workflow/deploy.py prepare
wsl -e env POC_DIR=<dir> python3 visual_workflow/deploy.py up
wsl -e env POC_DIR=<dir> python3 visual_workflow/bootstrap.py
python visual_workflow/delivery.py --root <publish dir> --viewer-root viewer_generated
python visual_workflow/policy.py visual_workflow/manifest.json --future-defaults
```

環境変数:

| 変数 | 用途 |
|---|---|
| `POC_DIR` | WSL 内の実行環境ディレクトリ（秘密情報はここに生成） |
| `ADAPTER_TOKEN` | 模擬 job API（`bridge.py`）の認証トークン |
| `EXTERNAL_POLICY_FILES` | 任意。ハッシュを記録する他プロジェクトの指示ファイル（パス区切りで複数） |
| `VIEWER_EXTRA_MANIFESTS` | 任意。検証対象に加える他の manifest |
| `CODING_DELEGATION_MODEL` | 任意。コーディング委譲の既定モデル名を上書き |

既定の待受ポート（すべて loopback）: Dify の nginx は 8080、n8n は 5678、模擬 job API は 8787、閲覧用の配信 `delivery.py` は 8090（`--port` で変更）。

## 制限事項（閉包）

- **同梱していない依存**（Python）: なし。標準ライブラリのみで動きます。外部要件は Docker / WSL、Dify 1.17.1、n8n 2.41.6（`deploy.py` が版を固定）です。
- 同梱データは合成です。`generated/` と `viewer_generated/` は `templates.py` から再生成できる模擬 PoC で、実在のワークフローではありません。`manifest.json` の URL は例示用です。
- 実行証跡（`evidence/`、receipt）と登録済みビューの設定（`config/registered_workflow_views.json`）は同梱していません。receipt は各自が公開・取得したときに生成されます。
- 公開（インターネット上への配信）は含みません。各自の環境で行ってください。
- 他の公開リポジトリとの関係: `dev-acceptance-harness` の gate が `policy.py` / `viewer.py` を呼びます（`integrations/visual_workflow/` に置く前提）。`orbit-agent-workflow-mock` の `prepare.py` は、このディレクトリの `bootstrap.py` / `templates.py` / `viewer.py` を使います。
- `audit.py` / `publish.py` は、`visual_workflow/` が `integrations/visual_workflow/` に置かれている前提でルートを推定します。

## ライセンス

All rights reserved. 閲覧目的で公開しています。詳細は LICENSE を参照。
