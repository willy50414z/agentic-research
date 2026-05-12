## Context

`feat/remove_langgraph` ブランチで LangGraph と plugin registry が除去された結果、以下の問題が残存している：

1. **套件名稱無意義**：`framework/` はどんなプロジェクトでも使える名前で、コードベースの意図を伝えない
2. **外部系統接口散落在根目錄**：`minio_client.py`（MinIO）、`planka.py`（Planka）が廠商名称で露出し、交換コストが高い
3. **LangGraph 移除の遺毒**：`QuantAlphaPlugin` class は 5 つの step function を包むだけの空殻で、`workflow.py` が plugin インスタンスを経由して呼び出す迂回構造が残っている
4. **llm 関連ファイルが分散**：`llm_target.py`、`llm_preflight.py` がルート直下に散らばっている
5. **`quant_alpha/` の命名がコンテンツと乖離**：中身は全て freqtrade 回測ツールで、`quant_alpha` はもはや意味を持たない

## Goals / Non-Goals

**Goals:**
- `framework/` → `app/` への完全なパッケージリネーム
- 外部システムアダプターを `app/clients/` に集約し機能名称に変更
- LLM 関連モジュールを `app/llm/` に統合
- `quant_alpha/` → `app/freqtrade/` へのリネーム
- `QuantAlphaPlugin` class 除去、module-level functions への変換
- `workflow.py` から plugin 抽象層を除去し step functions を直接 import
- 全 import パスの更新（`framework/`、`tests/`、`main.py`）
- 陳腐化した prompt テキスト（"Plugin 欄位"）の削除

**Non-Goals:**
- 業務ロジックの変更（backtest 動作、workflow steps の挙動）
- API エンドポイント・レスポンス形式の変更
- DB スキーマの変更
- docker-compose / Dockerfile の変更
- 新機能追加
- `freqtrade/` 内部ファイルのロジック変更

## Decisions

### 決定 1：パッケージ名を `app` に

**選択**：`app/`

**代替案と却下理由**：
| 候補 | 却下理由 |
|---|---|
| `agentic_research` | 長すぎる、import が冗長 |
| `engine` | 抽象的すぎ、何を動かすか不明 |
| `agent` | 第三方ライブラリと衝突リスク |
| `core` | `app` と同様に汎用すぎるが `app` より明示性が低い |

`app` は FastAPI アプリの標準慣例（`from app.api.server import app` は自然）かつ短い。

### 決定 2：外部アダプターを `clients/` に集約

**選択**：`app/clients/`

**代替案と却下理由**：
| 候補 | 却下理由 |
|---|---|
| `adapters/` | Clean Architecture 用語、チームに馴染みが薄い |
| `integrations/` | 長い、`external/` と意味重複 |
| `external/` | 「外部」という意味は正確だが `clients` ほど明示的でない |

`clients/` は「外部サービスのクライアント」という意味で直感的。

### 決定 3：`quant_alpha/` → `freqtrade/`（廠商名称を使用）

本来は廠商名称より機能名称（`backtest/`）が好ましいが、フォルダ内の全ファイルが freqtrade に強く結合（`freqtrade_runner.py`、freqtrade config 生成、freqtrade ZIP パース）しており、`backtest/` では freqtrade 固有の実装であることが伝わらない。ユーザーの明示的な選択に従い `freqtrade/` とする。

フォルダ名に `freqtrade` が入るため、`freqtrade_cli.py` → `cli.py`、`freqtrade_runner.py` → `runner.py` と前綴を省略してコンパクトにする。

### 決定 4：`QuantAlphaPlugin` class を module-level functions に変換

`plugin.py` の `QuantAlphaPlugin` は：
- 状態を持たない（`__init__` が何もしない）
- 5 つの `*_node` メソッドを持つだけ
- plugin 抽象基底クラスも除去済みで、class である必然性がない

`steps.py` として module-level functions に変換し、`workflow.py` から直接 import する。`plugin` 引数は全 `_run_*` 関数から除去。

## Risks / Trade-offs

- **Import パス見落とし** → `grep -rn "from framework\|import framework"` で全箇所を事前確認済み（約 45 箇所）。テストが全通過すれば検証完了。
- **`python -m app.freqtrade.cli` のパス変更** → `freqtrade_cli.py` docstring にのみ記載。実際のコードでは直接 `run_backtest_is_oos()` を呼ぶため runtime 影響なし。docstring 更新で対応。
- **Prompt テキストの "quant_alpha" 残留** → `prompts/quant_alpha/` ディレクトリ名と `spec_review` prompt 内のテキストを両方更新する必要がある。見落とし時は spec review フローで LLM が誤った plugin 名を出力する。

## Migration Plan

1. `framework/` ディレクトリを `app/` にリネーム
2. サブディレクトリ・ファイルを新配置に移動（`clients/`、`llm/`、`freqtrade/`）
3. `app/freqtrade/plugin.py` → `steps.py` にリネームし class を module functions に変換
4. `app/workflow.py` の plugin 抽象を除去
5. 全 `from framework.` → `from app.` の一括置換（`main.py`、`app/` 内部、`tests/`）
6. `quant_alpha` → `freqtrade` の import パス更新
7. `prompts/quant_alpha/` → `prompts/freqtrade/` リネーム
8. `spec_review` prompt テキストの "Plugin 欄位" 記述を削除
9. `pytest` 全スイート通過確認

ロールバック：git revert（純粋なリネーム・移動のみなので影響範囲が明確）

## Open Questions

なし。探索フェーズで全決定事項を確認済み。
