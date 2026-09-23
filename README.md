# bigdrum

[![hacs](https://img.shields.io/badge/HACS-Custom-orange.svg?)](https://github.com/hacs/integration)
[![Open your Home Assistant instance][ha-link-badge]][ha-link]

日立製ドラム式洗濯機 (BD-STX110G / BD-SX120H 系、2022年以前世代) の Home Assistant カスタムインテグレーション。

専用アプリ「洗濯コンシェルジュ」を使わずに、洗濯機の運転状態 (状態 / コース / 残り時間 / AIお洗濯情報 / 各種お知らせ) を取得し、コースの開始 / 停止を操作できます。

## 動作原理

洗濯機本体が `50000/udp` で提供している通信サービス、または日立クラウド API (GLS) を直接叩いて状態を取得します。

- **LAN 経由** (自宅): UDP `MOVEINF` を 3秒間隔でポーリング
- **クラウド経由** (外出先): GLS API を 60秒間隔でポーリング
- 両方が利用可能なら LAN を優先し、LAN に到達不能なときだけクラウドに自動切替

## インストール (HACS)

1. HACS → **Custom repositories** に `https://github.com/nullsnet/bigdrum` を category `Integration` で追加
2. **Hitachi Big Drum** を検索してダウンロード
3. Home Assistant を再起動
4. **設定 → デバイスとサービス → 統合を追加 → Hitachi Big Drum**

HACS がインストール済みならこちらから 1 クリックで開けます:

[![Open your Home Assistant instance][ha-install-badge]][ha-install]

## 設定

設定ウィザード (config flow) でのみ設定します。

### クラウド (推奨)

日立メンバーズクラブの ID/パスワードを入力すると、ログイン後にクラウド鍵・トークン・UDP 鍵まで自動取得されます。自宅外からも利用可能です。

### LAN

ペアリング済みの AES 鍵/IV と洗濯機の IP を手入力します。インターネット不要ですが洗濯機と同じネットワークが必須です。

## 提供されるエンティティ

### センサー

| 名称 | 内容 |
|---|---|
| 運転状態 | 運転中 / 運転終了 / 一時停止中 / 待機中 等 |
| 運転フェーズ | 洗い / すすぎ / 脱水 / 乾燥 |
| 選択コース | コース名 (日本語) |
| 残り時間 | 残り運転時間 (分) |
| AIお洗濯情報 | 洗濯機の AI が検出した情報 |
| お知らせ | 通知 (故障 / 情報 等) |
| コース設定 | 洗い / 脱水 / 乾燥 / すすぎ 等の設定値 |
| 洗剤 自動投入量 | 洗剤の投入量 (mL) |
| 柔軟剤 自動投入量 | 柔軟剤の投入量 (mL) |

### バイナリセンサー

| 名称 | 判定 |
|---|---|
| 運転中 | 運転中かどうか |
| リモート操作可能 | リモート操作が可能な状態か |
| 洗剤残量少 | 洗剤が少なくなっている |
| 柔軟剤残量少 | 柔軟剤が少なくなっている |
| オンライン | 洗濯機が電源 ON かつ到達可能 |

### サービス (操作)

| サービス | 動作 |
|---|---|
| `bigdrum.start` | コース選択 + 開始 (予約開始も可能) |
| `bigdrum.stop` | 運転停止 |

`bigdrum.start` の詳細:

- `course_no`: コース番号 (select ドロップダウン、例「標準コース (洗乾)」= `1201`)
- `delay_minutes`: 予約開始 (N 分後・任意、1-1440)
- `device_id`: 対象の洗濯機 (複数台設定時のみ)
- `options`: コース設定の上書き (YAML / オートメーション用)

## 対応機種と注意

- 動作確認済みは **2022 年以前世代の `BD-STX110G`** のみ。他機種・新世代では動作しない可能性があります。
- `bigdrum.start` はコースカタログ (COURSEINF) 取得にクラウド API を使うため、**クラウド鍵が必須**です。`bigdrum.stop` は LAN 専用でも利用可能です。
- 自己責任で。家庭内の自分の洗濯機に対してのみ使用すること。

## 詳細ドキュメント

- **PROTOCOL.md**: 通信プロトコルの詳細 (UDP / クラウド API / 暗号化 / パケット形式)
- **HOMEASSISTANT.md**: Home Assistant 統合の実装詳細 (coordinator / エンティティ / サービス / 設定フロー)

## ライセンス

MIT License (詳細は [LICENSE](LICENSE) を参照)

[ha-link-badge]: https://my.home-assistant.io/badges/hacs_repository.svg
[ha-link]: https://my.home-assistant.io/redirect/hacs_repository/?owner=nullsnet&repository=bigdrum&category=integration
[ha-install-badge]: https://my.home-assistant.io/badges/hacs_repository.svg
[ha-install]: https://my.home-assistant.io/redirect/hacs_repository/?owner=nullsnet&repository=bigdrum&category=integration
