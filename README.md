# bigdrum

[![hacs](https://img.shields.io/badge/HACS-Custom-orange.svg?)](https://github.com/hacs/integration)
[![Open your Home Assistant instance][ha-link-badge]][ha-link]

日立製ドラム式洗濯機 (BD-STX110G / BD-SX120H 系、2022年以前世代) 本体が `50000/udp` で提供している謎サービスを叩いて
運転状況 (状態 / コース / 残り時間 / AIお洗濯情報 / 各種お知らせ) を取得する Python ツール群。

専用アプリ「洗濯コンシェルジュ」を使わずに動作状況を取得・記録するためのもの。

参考 (プロトコル調査): [日立製洗濯機のAPIを洗う - てれこテレコム](https://telecotele.com/projects/hitachi-washing-api/)

## Home Assistant 統合 (HACS)

`custom_components/bigdrum` として Home Assistant カスタムインテグレーションも提供しています。
洗濯機の運転状態をクラウド API (外出先 OK) / LAN UDP (自宅) のどちらかで取得し、
センサー / バイナリセンサー / 操作サービス (`bigdrum.start` / `bigdrum.stop`) として公開します。
インストール・設定 (config flow)・提供エンティティ・対応機種の詳細は **`HOMEASSISTANT.md`** を参照。

HACS がインストール済みならこちらから 1 クリックで開けます:

[![Open your Home Assistant instance][ha-install-badge]][ha-install]

## 仕組み

洗濯機との通信プロトコル (UDP 50000 / クラウド API / 暗号化 / 平文・時刻形式 /
`SEARCH`・`MOVEINF`・`GETINF`・`CTRLINS` の形式 / 状態 blob パース / プッシュ通知) の
詳細は **`PROTOCOL.md`** を参照。

## 必要になる値

| 値 | 意味 | 取得方法 |
|---|---|---|
| `aes_key` / `aes_iv` | 本体通信の暗号鍵・IV (ペアリング時に作成され、以降は不変と推測) | `python -m bigdrum login` で自動取得 |
| `app_src` | アプリ (スマホ側) に紐づく番号 (mobileId) | 同上 (デバイス一覧から) |
| `app_dst` | 洗濯機に紐づく番号 (製品ID) | 同上 |
| `ckdn_key` / `ckdn_iv` / `cspf_id` | クラウドAPI通信の鍵・IV・HID | `login` の応答から自動保存 |
| `device_addr` | 洗濯機のIP (LAN 経由を使う場合のみ) | `python -m bigdrum search --save-device` |
| `broadcast_addr` | LAN のブロードキャストアドレス | 手動 (例: `192.168.1.255`) |

### 鍵の取得 (推奨: `login`)

「洗濯コンシェルジュ」アプリ (APK: `jp.co.hitachi_gls.concierge`) のデコンパイルから
通信手順を抽出済みなので、**スマホ・アプリなしで PC から直接ログイン**できます。
login2 / GLS API の URL・暗号化・応答構造などの詳細は `PROTOCOL.md` (§9) を参照。

```
python -m bigdrum login <メンバーズクラブID> <パスワード>
```

- ログイン応答から `ckdn_key` / `ckdn_iv` / `cspf_id` (HID) と
  **各デバイスの UDP 50000 用 AES 鍵/IV (`ckdn_device_key`)** を取得
- GLS API (`CMD=GETDEVIDHID`) でデバイス一覧 (src=mobileId / dst=製品ID / ニックネーム)
  を取得して `config.json` に保存
- 登録が 1 台だけなら自動的にそのデバイスが選択されます
- ログイン失敗は `9E2` (要求形式不正) / `9E3` (**ID またはパスワードが正しくありません**) と表示
- ログインは鍵の取得時だけでよく、その後は `config.json` の `ckdn_key` / `ckdn_iv` / UDP鍵 がそのまま使えます (再ログイン不要)。パスワードは保存されません。

## セットアップ

```
pip install -r requirements.txt
```

### 方法A: クラウド経由 (推奨。LAN不要・外出先OK)

```
python -m bigdrum login <メンバーズクラブID> <パスワード>
python -m bigdrum devices           # 必要なら
python -m bigdrum select <dst>      # 複数台の場合
python -m bigdrum cstatus           # 運転状態 (クラウド経由)
```

これだけで `ckdn_key/iv`・`cspf_id`・UDP鍵・`app_src/app_dst` が
`config.json` に揃います。

### 方法B: LAN 経由 (従来)

`login` で UDP 鍵が取得できていれば `config.json` は不要で、
`device_addr` / `broadcast_addr` を足すだけです:

```
python -m bigdrum search --save-device   # 洗濯機のIP発見
```

> LAN 経由は PC と洗濯機が同じブロードキャストドメイン (VLAN) にある必要があり、
> 洗濯機が電源 ON (かつ Wi-Fi 接続済み) でないと応答しない。

## 使い方

```
python -m bigdrum selftest           # 環境確認 (暗号化ラウンドトリップ)
python -m bigdrum login <id> <pwd>   # メンバーズログイン → 鍵・ID自動取得
python -m bigdrum devices            # 登録デバイス一覧
python -m bigdrum select <dst>       # デバイス選択
python -m bigdrum cstatus            # MOVEINF: 運転状態 (クラウド経由, --json)
python -m bigdrum search --save-device   # 洗濯機のIP発見 → device_addr 保存 (LAN)
python -m bigdrum status             # MOVEINF: 運転状態 (LAN経由, JSON は --json)
python -m bigdrum getinf 0005        # 残り運転時間(分) (LAN)
python -m bigdrum getinf 0001        # 現在のコースの設定値群 (LAN)
python -m bigdrum cgetinf 0005       # 残り運転時間(分) (クラウド経由・LAN不要)
python -m bigdrum cgetinf 0001       # 現在のコースの設定値群 (クラウド経由・LAN不要)
python -m bigdrum cgetinf all        # 全サブ (0001/0002/0003/0005/0006) 一括取得 (LAN不要)
python -m bigdrum sniff              # UDP 50000 受動捕捉 (アプリ操作と併用)
python -m bigdrum monitor --interval 30   # 常駐監視 → bigdrum_history.db
```

### sniff (鍵・番号の取得支援)

```
$ python -m bigdrum sniff
[sniff] listening on 0.0.0.0:50000 -> sniff.jsonl
[sniff] ここでスマホの洗濯コンシェルジュアプリで操作してください (Ctrl+C で終了)
```

- スマホでアプリを開き「状態確認」等操作を行うと、アプリがブロードキャストで出す
  `SEARCH` パケットが捕捉される
- `aes_key` / `aes_iv` が設定済みなら自動復号され、**`app_src` / `app_dst` が
  自動的に `config.json` に保存される**
- パケットはすべて `sniff.jsonl` (1件1行の JSON) に残る

### monitor (継続監視・履歴記録)

30秒 (既定) ごとに `MOVEINF` (+`GETINF 0005`) を取得し、**状態が変化したときだけ**
`bigdrum_history.db` (SQLite) に insert する。

```
CREATE TABLE history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT,            -- 状態変化時刻 (PC 基準)
    source TEXT,        -- moveinf
    status_code TEXT,
    status TEXT,        -- 運転中 / 運転終了 / 一時停止中 / 待機中 ...
    process TEXT,
    sel_course TEXT,    -- コース番号 (コース名は日立側のJSONと突き合わせ)
    remaining_time INTEGER,  -- 残り時間(分) [GETINF 0005]
    info TEXT,          -- お知らせ (コードF/C/I)
    ai_infos TEXT,      -- AIお洗濯情報 (JSON array)
    raw_json TEXT       -- 生レスポンス
);
```

```
sqlite3 bigdrum_history.db "SELECT ts, status, remaining_time FROM history ORDER BY id DESC LIMIT 10;"
```

洗濯機が電源 OFF の間は「応答なし」が記録され、復帰時に再び状態が記録される。

### GETINF サブコマンド

`getinf <DATA>` (LAN) / `cgetinf <DATA>` (クラウド経由・LAN不要) で設定・状態を取得
(`DATA=all` で全サブ 0001/0002/0003/0005/0006 を一括取得)。各コードの意味は `PROTOCOL.md` §7 を参照。

## 制約・注意点

- **状態取得はクラウド/LAN 両対応** — 外出先は `cstatus` (GLS クラウドAPI) 経由。
  **操作系**: CLI は `ctrl`/`cctrl`/`cctrld`、HA は `bigdrum.start`/`bigdrum.stop`。CTRLC=0003 (コース開始)・0001 (停止) に対応
- 鍵/IV はペアリング時に変更される可能性がある (未確認。その際は再 `login` で取得し直し)
- `remainingTime` は `MOVEINF` だけでは取れないコースがあり、`GETINF 0005` が本命
  (CLI `monitor`・HA 統合はいずれも `GETINF 0005` を優先・補完している)
- `date` / `time` フィールドは「現在時刻」ではなく状態変化 (すすぎ開始等) の時刻
- コース名は `selCourse` と日立 GLS 系 API から入手できる JSON (約800KB) と突き合わせると分かる
- 予約 (reserve) と CTRLC=0005 (その他操作) / 0006 (コースセットDL) は未実装 (仕様が未確認)
- 自己責任で。家庭内の自分の洗濯機に対してのみ使用すること

## 開発

- Python 3.10+ (Windows / Linux 対応。`SO_REUSEPORT` は POSIX 側でだけ設定)
- 依存: `pycryptodome`

```
bigdrum/
├── __init__.py
├── __main__.py     # CLI
├── config.py       # 設定 (JSON)
├── protocol.py     # AES-256-CBC + SHA256 チェックサム / パース
├── cloud.py        # メンバーズ login2 + GLS クラウドAPI (鍵・ID・状態)
├── client.py       # UDP 50000 クライアント (search/moveinf/getinf)
├── course.py       # CTRLD (42桁) 組み立て (COURSEINF → 0003)
├── sniff.py        # 受動スニッファ (JSONL, SRC/DST 自動抽出)
└── monitor.py      # 継続監視 (SQLite 履歴)

tests/
└── test_cloud_offline.py   # クラウドAPIのオフラインテスト (モックサーバ)
```

[ha-link-badge]: https://my.home-assistant.io/badges/hacs_repository.svg
[ha-link]: https://my.home-assistant.io/redirect/hacs_repository/?owner=nullsnet&repository=bigdrum&category=integration
[ha-install-badge]: https://my.home-assistant.io/badges/hacs_repository.svg
[ha-install]: https://my.home-assistant.io/redirect/hacs_repository/?owner=nullsnet&repository=bigdrum&category=integration
