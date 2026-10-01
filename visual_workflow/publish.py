"""Copy sanitized runtime evidence/exports, hash scoped instructions and build handoff."""
import hashlib
import html
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
# Optional: instruction/manifest files of other projects whose hashes are recorded (os.pathsep-separated paths).
EXTERNAL = [Path(p) for p in os.environ.get('EXTERNAL_POLICY_FILES', '').split(os.pathsep) if p]
POC_DIR = os.environ.get('POC_DIR', '')  # WSL path of the PoC runtime directory (required by the WSL-backed checks)


def main():
    if not POC_DIR:
        raise SystemExit('Set POC_DIR to the PoC runtime directory (WSL path)')
    evidence = HERE / 'evidence'
    generated = HERE / 'generated'
    evidence.mkdir(exist_ok=True)
    generated.mkdir(exist_ok=True)
    runtime = json.loads(subprocess.check_output(['wsl', '-e', 'cat', POC_DIR + '/runtime-evidence.json'], encoding='utf-8'))
    (evidence / 'runtime.json').write_text(json.dumps(runtime, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    external = {p.as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in EXTERNAL}
    (evidence / 'external-policy.json').write_text(json.dumps(external, indent=2) + '\n', encoding='utf-8')
    publish = ROOT / '.harness/n8n-dify-poc/publish'
    publish.mkdir(parents=True, exist_ok=True)
    for project in ('project-a', 'project-b'):
        for engine in ('n8n', 'dify'):
            name = engine + '-' + project + '.json'
            raw = subprocess.check_output(['wsl', '-e', 'cat', POC_DIR + '/exports/' + name], encoding='utf-8')
            data = json.loads(raw)
            encoded = json.dumps(data, ensure_ascii=False, indent=2) + '\n'
            (generated / name).write_text(encoded, encoding='utf-8')
            (publish / name).write_text(encoded, encoding='utf-8')
    rows = ''.join('<tr><td>' + html.escape(x['project']) + '</td><td>n8n成功 / Dify成功 / 模擬job完了</td><td>同一job保持</td></tr>' for x in runtime['results'])
    links = ''.join('<li><a href="' + engine + '-' + project + '.json">' + engine + '：' + project + ' の読み込み用フロー</a></li>' for project in ('project-a', 'project-b') for engine in ('n8n', 'dify'))
    steps = ''.join('<span>' + s + '</span>' for s in ('入力', 'Dify模擬企画', '成功・project検査', '模擬job開始', '状態確認', '結果返却'))
    page = '<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>n8n + Dify PoC</title><style>body{font:16px/1.8 system-ui,sans-serif;max-width:1000px;margin:40px auto;padding:0 20px;color:#172b44;background:#f6f8fc}h1{line-height:1.3}section{background:white;border:1px solid #d9e0eb;border-radius:12px;padding:22px;margin:20px 0}table{border-collapse:collapse;width:100%}td,th{padding:12px;border-bottom:1px solid #ddd;text-align:left}.flow{display:flex;flex-wrap:wrap;gap:12px}.flow span{background:#e8f1ff;border-radius:8px;padding:12px}.flow span+span:before{content:"→ ";color:#2563eb}a{color:#1756ab}</style><h1>n8n + Dify PoC</h1><p>既存システムを維持し、次の新規開発から使うための接続検証。</p><section><h2>実サービスで確認した工程</h2><div class="flow">' + steps + '</div><p>n8n 2.41.6 / Dify 1.17.1。モデルAPI・実動画生成・公開・課金処理は呼び出していません。</p><table><tr><th>プロジェクト</th><th>実測結果</th><th>同一依頼の再送</th></tr>' + rows + '</table><p>検証日時：' + html.escape(runtime['verified_at']) + '。新規開発の既定はn8n＋Dify＋OpenSpec＋LangGraph。既存アプリの全面移行は行いません。</p></section><section><h2>管理画面について</h2><p><a href="http://localhost:5678">n8n：工程編集・Executions</a> / <a href="http://localhost:8080">Dify：Workflow・Logs</a></p><p>この2リンクは実行環境のマシン上で利用するlocalhost URLです。リモート端末から管理画面には接続できません。PoC専用ownerでログインしてください。認証情報はローカルの専用領域に保存しています。</p></section><section><h2>読み込み用フロー</h2><p>認証情報を含まないテンプレートです。別環境に読み込む場合は、その環境の認証設定を割り当てます。</p><ul>' + links + '</ul></section><section><h2>検証範囲</h2><p>job ID・進捗・結果・エラー・冪等性、Dify失敗の後続停止を検証。模擬制作のため、実動画品質・既存APIsとの接続・本番負荷・長時間処理は次の新規開発で検証します。サービスと案内URLは配信元のサーバーが稼働している間に利用できます。</p></section></html>'
    (publish / 'index.html').write_text(page, encoding='utf-8')
    print(json.dumps({'published_files': 5, 'runtime_status': runtime['status'], 'external_policy_files': len(external)}))


if __name__ == '__main__':
    main()
