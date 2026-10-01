# n8n + Dify: 視覚的な工程構築と閲覧専用ビューア

このディレクトリは、新規パイプラインを **n8n（工程構築・実行履歴）+ Dify（AI 処理）** で組み、その実登録グラフを **ログイン不要・閲覧専用** のページとして納品するための道具一式（PoC）。ほかのリポジトリ（dev-acceptance-harness の gate、ops-acceptance-harness の LangGraph）から `integrations/visual_workflow/` として使われる想定で、ここでは `visual_workflow/` のまま置いている。

> 注意: この公開版のデータはすべて合成。`generated/` と `viewer_generated/` は同梱の `templates.py` から再生成できる模擬 PoC であり、実在のワークフローではない。

## 役割

|部品|担当|
|---|---|
|n8n|視覚的な工程構築、API 連携、実行履歴、Dify 失敗の検知|
|Dify|AI 処理の視覚的構築。この PoC は Code ノードだけで模擬企画を作る（モデル API を呼ばない）|
|PoC adapter（`bridge.py`）|SQLite に模擬 job を保存し、認証付き API で状態と結果を返す|
|`policy.py`|manifest の検査（frameworks、job 契約、public_view、inspection_view）|
|`viewer.py` / `capture_viewer.py`|実登録グラフから公開用の読み取り専用 snapshot を作る（値・コード・認証は含めない）|
|`delivery.py` / `verify_viewer.py`|固定ファイルだけを GET で配信し、匿名取得と snapshot 一致を検証する|

n8n の登録フロー: PoC request → Dify plan → Validate Dify result → Submit mock job → Wait for mock job → Observe job → Return result。
Dify の登録 app: Input → Prepare mock plan → Result。project-a / project-b は別 app・別 workflow。

## 動かし方（WSL + Docker）

実行環境のディレクトリは環境変数 `POC_DIR`（WSL 内のパス）で指定する。秘密情報（owner の資格情報、adapter token、暗号鍵）は `deploy.py prepare` が `POC_DIR/secrets.json` に生成する。**リポジトリには含まれない。外部へ共有しない。**

```powershell
wsl -e env POC_DIR=<dir> python3 visual_workflow/deploy.py prepare   # ソース配置、loopback/mount/restart の検査
wsl -e env POC_DIR=<dir> python3 visual_workflow/deploy.py up
wsl -e env POC_DIR=<dir> python3 visual_workflow/bootstrap.py        # owner 初期化、workflow 登録、E2E
wsl -e env POC_DIR=<dir> python3 visual_workflow/deploy.py status
wsl -e env POC_DIR=<dir> python3 visual_workflow/deploy.py down
```

サービスは loopback のみ公開し、自動起動・自動再起動は設定しない。`docker compose down -v` は実行しない。

## 閲覧ページの作り方

1. `capture_viewer.py` が実登録グラフを取得し、`viewer.py` が公開用 snapshot と静的サイト（`viewer_generated/`）を作る。同梱の `viewer_public/` は静的サイトの雛形。
2. `delivery.py --root <publish dir> --viewer-root viewer_generated` で固定ファイルだけを配信（GET のみ、POST は 405）。公開は各自の環境で行う。
3. `verify_viewer.py` が匿名 HTTP で取得し、snapshot の一致・未登録 route の 404・POST 405 を確認して receipt（`evidence/`）を書く。
4. `policy.py <manifest> [--future-defaults]` が manifest と receipt の整合を検査する。receipt が無い、または snapshot が古いと不合格になる。

同梱の `manifest.json` は合成例（URL は例示用）。receipt は各自が公開して取得したときに生成されるため、同梱していない。

## 新規開発で行うこと

1. 新規部分を OpenSpec の change として作る。
2. workflow manifest を作る（`manifest.json` を参照）。n8n / Dify export、非同期 job 契約、固定検証を記載する。
3. 工程の構築・連携は n8n、AI 処理は Dify。既存システムの全面移行は混ぜない。
4. `python visual_workflow/policy.py <manifest>` を change 専用の固定テストに含める。
5. 新規 job は job_id / status / progress / result / error を返し、冪等キーで重複を防ぐ。
6. 実サービス統合、失敗経路、既存への影響を検証し、独立レビューを通す。

frameworks の欠落は policy が拒否する。例外には `exception: {"user_authorized": true, "reason": "...", "authorization_reference": "..."}` を保存する。

## 依存と未解決の参照（この公開版）

- Docker / WSL、Dify 1.17.1、n8n 2.41.6（deploy.py が固定）。
- `audit.py` / `verify_viewer.py` は、他プロジェクトの指示ファイル・manifest を環境変数（`EXTERNAL_POLICY_FILES`、`VIEWER_EXTRA_MANIFESTS`）で任意に追加できる。未設定なら対象は同梱の manifest だけ。
- `evidence/`（実行証跡、receipt）と `config/registered_workflow_views.json` は同梱しない。`capture_viewer.py` は後者が無ければ読み込まない。
- `policy.py` のコーディング委譲の既定モデル名は例（`CODING_DELEGATION_MODEL` で上書き）。
- dev-acceptance-harness の gate から使う場合は、このディレクトリを `integrations/visual_workflow/` に置く（`policy.py` / `viewer.py` が pin の対象）。