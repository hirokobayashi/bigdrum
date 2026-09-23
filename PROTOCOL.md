# PROTOCOL.md — 日立製ドラム洗濯機 通信プロトコル仕様

本ドキュメントは `bigdrum/` (CLI) と `custom_components/bigdrum/` (HA) が実装する
日立ドラム洗濯機 (専用アプリ「洗濯コンシェルジュ」) との通信プロトコルをまとめる。
動作確認済み実機は **`BD-STX110G`** (モデルコード `01003517`) のみ。

> 識別子・鍵の**取得方法**は README.md / AGENTS.md を参照。本ドキュメントは「通信の形式」に専念する。

---

## 目次

1. [識別子](#1-識別子)
2. [暗号化 (2系統)](#2-暗号化-2系統)
3. [平文フォーマット (k=v)](#3-平文フォーマット-kv)
4. [時刻形式 (2系統)](#4-時刻形式-2系統)
5. [LAN UDP (ポート 50000)](#5-lan-udp-ポート-50000)
6. [MOVEINF — 運転状態](#6-moveinf--運転状態)
7. [GETINF — 設定・状態](#7-getinf--設定状態)
8. [CTRLINS — 操作指令](#8-ctrlins--操作指令)
9. [クラウド API](#9-クラウド-api)
10. [COURSEINF — コースカタログ](#10-courseinf--コースカタログ)
11. [プッシュ通知 (FCM)](#11-プッシュ通知-fcm)
12. [パケット捕捉](#12-パケット捕捉)

---

## 1. 識別子

| 名称 | 長さ | 意味 / 用途 |
|---|---|---|
| `app_src` (SRC / mobileId) | 20桁 hex | アプリ (スマホ側) に紐づく ID。要求/応答の `SRC`。 |
| `app_dst` (DST / 製品ID) | 20桁 hex | 洗濯機本体に紐づく ID。要求/応答の `DST`。**先頭 8 桁がモデルコード** (例 `01003517`)、COURSEINF の `MODEL` パラメータにも使用 (`dst[:8]`)。残り 12 桁 (`dst[8:20]`) は **MAC 相当** で、アプリの「確認」画面 (`CO00352Activity.dataUI`) が `XX:XX:XX:XX:XX:XX` と表示 (HA 側は `const.mac_from_dst()` で小文字 MAC 化し `CONNECTION_NETWORK_MAC` 接続として登録)。 |
| `cspf_id` (HID) | - | ログイン応答で取得する Hitachi ID。GLS API の `HID` パラメータ。 |
| `ckdn_key` / `ckdn_iv` | 32B / 16B (hex) | GLS クラウドAPI 通信用 AES-256 鍵/IV。 |
| `udp_key` / `udp_iv` | 32B / 16B (hex) | 各デバイスの UDP 50000 通信用 AES-256 鍵/IV (`ckdn_device_key` から取得)。 |
| `SEQ` | 1 byte | 要求シーケンス番号。送信時 `seq % 256`、`%02X` 表示。 |
| `APPTYPE` | `"8"` | アプリ種別。クラウド API 全要求に付与。 |
| `ckdn_token` | - | GLS API の認証トークン。**User-Agent ヘッダとして送信** (§9.2)。 |

モデルコードは `Constant` の機種定数に対応する (例 `01003517` = `BD_STX110G`,
`MODEL_NO_BEFORE2022` に属する 2022 年以前世代)。アプリ側のモデル判定
(`appDst.startsWith(BD_SX120H)` 等) は `BD-STX110G` では全て false。

---

## 2. 暗号化 (2系統)

両系統とも **AES-256-CBC + ゼロパディング** (`protocol.encrypt` / `protocol.decrypt`)。
唯一の違いは **チェックサム付きか否か**。混同しないこと。

### 2.1 UDP 50000 (チェックサムあり) — `protocol.build_request`

```
平文 P = "SEQ=..&SRC=..&DST=..&GRP=REQ&CMD=..&[DATA=..]"
パケット = AES-256-CBC(P + ゼロパッド)  ‖  SHA256(P)[:16]
```

- 末尾 16 バイトは **要求平文の SHA256 の先頭 16 B** (インテグリティチェックサム)。
- 受信時 `parse_packet` は `パケット[:-16]` を復号し、チェックサムの検証はしない
  (暗号化鍵が一致すれば復号できることを信頼)。

### 2.2 クラウド (login2 / GLS API) (チェックサムなし) — `protocol.encrypt`

```
body = AES-256-CBC(平文 + ゼロパッド)
```

- チェックサムは付かない。
- 平文は k=v (`email=..&password=..` や `CMD=..&HID=..`)。

### 2.3 ゼロパディング

`pad = 16 - (len % 16)`, 末尾に `0x00 * pad`。復号時 `rstrip(b"\x00")` で除去
(k=v 形式の平文末尾が 0x00 になることはないため安全)。

---

## 3. 平文フォーマット (k=v)

UDP とクラウドの両方で使う共通の `k=v&k=v` 文字列。

### 3.1 UDP 要求 (`build_request`)

```
SEQ={seq%256:02X}&SRC={app_src}&DST={app_dst}&GRP=REQ&CMD={cmd}&[DATA={data}]
```

- `GRP` = `REQ` (要求) / `ACK` (応答・成功) / `NAK` (応答・拒否)。
- `DATA` は CMD により任意 (§6/§7/§8)。無い場合 `...&CMD=MOVEINF&` の形で終了。

### 3.2 クラウド要求 (`gls_call`)

```
CMD={cmd}&HID={cspf_id}&APPTYPE=8[&SRC=..&DST=..&DATA=..]
```

- パラメータ名は**明示** (UDP と異なりカンマ結合せず曖昧さなし)。
- `MOVEINF/GETINF/CTRLINS` は `SRC`/`DST` (＋ `DATA`/`CTRLC`/`CTRLD`) を付ける。

### 3.3 応答の `GRP`

- UDP: `GRP=ACK` (受理) / `GRP=NAK` (拒否)。
- クラウド: 数値の `GRP`。**`GRP ∈ {0, 3, 4}` が成功**。それ以外 (例 `2`) = 洗濯機オフ / 未報告。
  状態 blob は `DATA` をカンマ分割した要素から取る (§6.1)。

---

## 4. 時刻形式 (2系統)

アプリの `TimeUtils` と同一形式を再現。曜日は**日曜=0 … 土曜=6**
(Python `weekday()` は月=0 なので `(weekday()+1) % 7`)。

| 関数 | 桁数 | 進数 | 形式 | 用途 |
|---|---|---|---|---|
| `now_string_hex()` | 16 | 16進 | `YYMMDDhhmmss` + 曜日 `dd` の各 2 桁 hex | **UDP CTRLINS** の TIME |
| `now_string_dex()` | 15 | 10進 | `YYYYMMDDhhmmss` + 曜日 1 桁 | **クラウド CTRLINS** の TIME |

- hex 例: `07EA091411253900` (2026-09-20 16:41:59 日曜)。
- dex 例: `202609201641590`。

---

## 5. LAN UDP (ポート 50000)

- 洗濯機は **`50000/udp`** でサービスを提供。
- **レスポンスは常に「送信者側の `50000/udp`」に向く** → クライアントも `50000` にバインドして送受信すること (`client.BigDrumClient._socket`)。
- 洗濯機は**要求応答型のみ**で、不意打ちの UDP (ポーリング応答・イベント通知) は送らない。
- UDP は**ユニキャスト**なので PC 側でのパッシブ sniff は不可 (§12)。

### 5.1 CMD 一覧 (UDP)

| CMD | DATA | 用途 |
|---|---|---|
| `SEARCH` | (なし) | **ブロードキャスト**送信で洗濯機の IP を発見 (Service Discovery)。洗濯機が返すパケットの送信元 IP = `device_addr`。洗濯機に IP 固定ができないため必要。 |
| `MOVEINF` | (なし) | 運転状態取得 (§6)。 |
| `GETINF` | `0001`/`0002`/`0003`/`0005`/`0006` | 設定・状態取得 (§7)。 |
| `CTRLINS` | `TIME,CTRLC,CTRLD` | 操作指令 (§8.1)。 |

`SEARCH` 以外の CMD は `(device_addr, 50000)` 宛のユニキャスト。

---

## 6. MOVEINF — 運転状態

### 6.1 応答 DATA の形式

- **UDP**: `DATA = {prefix},{blob}` → blob はカンマ分割 **idx 1**。
- **クラウド**: `DATA = ...,{blob}` → blob はカンマ分割 **idx 3**。

`_moveinf_blob()` が経路別に blob を抽出 (要素数 >= 4 なら idx3、さもなくば idx1)。

### 6.2 成功判定

クラウド応答は `GRP ∈ {0,3,4}` のみ成功 (§3.3)。`GRP` がそれ以外なら blob は無効
(洗濯機オフ/未報告) と扱う。

### 6.3 状態 blob (hex 文字列, 44 バイト = 88 桁) のフィールド位置

| 位置 (桁) | フィールド | 説明 |
|---|---|---|
| `[0:4]` | date.year | 年 (hex) |
| `[4:6]` | date.month | 月 (hex) |
| `[6:8]` | date.day | 日 (hex) |
| `[8:10]` | time.hour | 時 (hex) |
| `[10:12]` | time.minute | 分 (hex) |
| `[12:16]` | (未使用) | 予約関連? |
| `[16:18]` | statusCode | 状態コード (§6.4) |
| `[18:20]` | processAll | 処理全体フラグ |
| `[20:22]` | process | 処理 (優先的に `status` を上書き) |
| `[22:26]` | selCourse | 選択コース番号 (4桁) |
| `[26:28]` | infoCodeC | お知らせコード C |
| `[28:30]` | infoCodeF | お知らせコード F |
| `[30:32]` | infoCodeI | お知らせコード I (§6.5) |
| `[32:34]` | (未使用) | |
| `[34:36]` | aiInfoFlag | AI お洗濯情報ビット (§6.6) |
| `[36:48]` | downloadCourse1 | DLコース1 (12桁) |
| `[48:60]` | downloadCourse2 | DLコース2 |
| `[60:72]` | downloadCourse3 | DLコース3 |
| `[72:76]` | remainingTime | 残り時間 (分, hex) |
| `[76:78]` | reasonFlag | 残り時間変化理由ビット (§6.7) |
| `[78:82]` | yoyakuRemainingCounter | 予約残り (4桁) |
| `[82:84]` | remoteOperationStatus | リモート操作状態 |
| `[84:88]` | uniqueId | |

> `date`/`time` は「現在時刻」ではなく**状態変化 (すすぎ開始等) の時刻**。状態が遷移した瞬間にその時点の本体時計が刻まれる (待機中は固定)。実測で分単位ではなくフェーズ遷移ごとにしか更新されないため、生時計・運転開始時刻ではない。

### 6.4 statusCode (`STATUS_MAP`)

| code | 表示 |
|---|---|
| `00` | 本体の電源が入っていないか、通信ができない状態 |
| `05` | 予約中 |
| `06` / `07` | 運転中 |
| `40`〜`47` | 槽内冷却中 |
| `86` / `87` | 一時停止中 |
| (その他) | 待機中 (デフォルト) |

`process` (`PROCESS_MAP`) が存在すると `status` を**上書き**:

| process | 表示 |
|---|---|
| `FF` | 運転終了 |
| `FA` | 乾燥終了・ふんわりガード運転中 |
| `FB` | 自動投入部お手入れ運転中 |

### 6.4b 運転フェーズ (`PROCESS_ALL_PHASES`)

アプリは「運転中」に加えて**現在のフェーズ (洗い/すすぎ/脱水/乾燥)** をスチーパで表示する。

- 判定は `processAll` (`[18:20]`) × `process` (`[20:22]`)。両方とも §6 の blob から取る。
- 対応表は apk の `ImagePattern2B` (画像名 `status_<フェーズ数>_<種別>_<ステップ>`) を基に作成。画像名の**先頭数字 = フェーズ数** (標準コース 1201 = 4 フェーズ) で、その `processAll` 内の `process` を昇順に並べた**位置 = フェーズ順序**に対応させる。
- 例: `processAll=1E` (course 1201 洗乾) は `process` 02/03/04/05 → 洗い/すすぎ/脱水/乾燥。
- 条件: `statusCode` が `06`/`07` (運転中) のときのみ。`process=FF` (運転終了)・未設定 (`00`)・未知の `processAll` は `None`。
- **確定度**: `1E` (course 1201 洗乾) は実機ライブ取得で確認済み。`0F`/`1B`/`3E` (同 4 フェーズ)・`07`/`0B`/`0E`/`2E`/`3A`/`1A` (3 フェーズ)・`03`/`06`/`0A`/`0C`/`26`/`2A` (2 フェーズ)・`1F` (5 フェーズ) は apk 構造からの推定 (フェーズ名は「洗い→すすぎ→脱水→乾燥」の標準順序で埋める)。コースごとに実機で確認した場合は §6.4b の表と `PROCESS_ALL_PHASES` を修正すること。
- 単一フェーズの特殊運転 (`02`/`04`/`08`/`10`/`22`: ふんわりガード・お手入れ等) はフェーズの対象外 (`process` が空でフェーズを特定できない)。

| processAll | フェーズ | process → フェーズ |
|---|---|---|
| `1E` ⭐ | 4 (洗乾) | 02 洗い / 03 すすぎ / 04 脱水 / 05 乾燥 |
| `0F` | 4 | 01 洗い / 02 すすぎ / 03 脱水 / 04 乾燥 |
| `1B` | 4 | 01 洗い / 02 すすぎ / 04 脱水 / 05 乾燥 |
| `3E` | 4 | 02 洗い / 03 すすぎ / 04 脱水 / 05 乾燥 |
| `07` | 3 (洗濯) | 01 洗い / 02 すすぎ / 03 脱水 |
| `0B` | 3 | 01 洗い / 02 すすぎ / 04 脱水 |
| `0E` | 3 | 02 洗い / 03 すすぎ / 04 脱水 |
| `2E` | 3 | 02 洗い / 03 すすぎ / 04 脱水 |
| `3A` | 3 | 02 洗い / 04 すすぎ / 05 脱水 |
| `1A` | 3 | 02 洗い / 04 すすぎ / 05 脱水 |
| `03` | 2 | 01 洗い / 02 脱水 |
| `06` | 2 | 02 洗い / 03 脱水 |
| `0A` | 2 | 02 洗い / 04 脱水 |
| `0C` | 2 | 03 洗い / 04 脱水 |
| `26` | 2 | 02 洗い / 03 脱水 |
| `2A` | 2 | 02 洗い / 04 脱水 |
| `1F` | 5 | 01 洗い / 02 すすぎ / 03 脱水 / 04 乾燥 / 05 乾燥 |

⭐ = 実機ライブ取得で確認済み。

### 6.5 お知らせコード I (`INFO_CODE_I_MAP`)

| code | 内容 |
|---|---|
| `01` | ドア開閉確認 |
| `02` | 糸くずフィルター |
| `03` | 乾燥フィルター |
| `04` | 乾燥容量オーバー |
| `05` | 槽洗浄おすすめサイン |
| `06` | 槽洗いおすすめサイン |

いずれかのコードが `00` 以外なら `info = "お知らせがあります"`。

### 6.6 AI お洗濯情報 (`AI_INFO_BITS`, aiInfoFlag のビット)

| ビット | 内容 |
|---|---|
| `0b100000` | 水分量少 → 脱水時間短縮 |
| `0b10000` | すすげている → すすぎ時間短縮 |
| `0b1000` | 化繊多 → すすぎ水量減 |
| `0b100` | 汚れ多 → 洗い時間延長 |
| `0b10` | 硬度低・水温高 → 洗い時間短縮 |
| `0b1` | 硬度低・水温高 → 洗剤量減 |

### 6.7 残り時間変化理由 (`REMAIN_REASON_BITS`, reasonFlag)

| ビット | 内容 |
|---|---|
| `0b10000` | 衣類の片寄りを直しました |
| `0b1000` | 運転内容を見直しました |
| `0b100` | 運転内容を見直しています |
| `0b10` | 衣類の片寄りを直しています |
| `0b1` | 泡消し運転を実施しています |

---

## 7. GETINF — 設定・状態

`DATA = {sub}` を送信し、応答 `DATA = {sub},{value}` を受信 (`value` は hex)。
`_parse_getinf` が sub ごとにパースする。

| sub | 内容 | value 桁数 |
|---|---|---|
| `0001` | 現在のコースの設定値群 | 72桁 |
| `0002` | 選択中の設定 (洗濯/洗い込み の各項目) | 20桁 |
| `0003` | 設定値の別表現 | 28桁 |
| `0005` | **残り運転時間 (分)** | 任意 (`int(v,16)`) |
| `0006` | 予約関連 | 任意 (`int(v,16)`) |

> `remainingTime` は `MOVEINF` だけでは取れないコースがあり、`GETINF 0005` が本命。

### 7.1 sub=0001 のフィールド位置 (value 72桁)

| 位置 | フィールド | 位置 | フィールド |
|---|---|---|---|
| `[0:4]` | yoyaku | `[20:22]` | untenStart |
| `[4:6]` | arai | `[22:24]` | shuryoYokoku |
| `[6:8]` | susugi | `[24:26]` | dassuiGuai |
| `[8:10]` | dassui | `[26:28]` | kawakiGuai |
| `[10:12]` | kanso | `[28:30]` | onpuHogushi |
| `[12:14]` | onsuiMisth | `[30:32]` | funwari |
| `[14:16]` | sensor | `[32:34]` | atatameAuto |
| `[16:18]` | autoOsouji | `[34:36]` | dassuikido |
| `[18:20]` | oyutori | `[36:38]` | onpuDassui |

| 位置 | フィールド | 位置 | フィールド |
|---|---|---|---|
| `[38:40]` | joshitsu | `[50:52]` | kaitenShawa |
| `[40:42]` | oyutoriMemory | `[52:54]` | toridashi |
| `[42:44]` | shimizuSusugi | `[54:56]` | jidoTonyuZanryoFlag |
| `[44:46]` | suiryo | `[56:58]` | senzaiMeigara |
| `[46:48]` | sosaMukoOn | `[58:60]` | senzaiKijun |
| `[48:50]` | suiryoOme | `[60:62]` | senzaiAiSusugi |

`[62:64]` = senzaiJidoTonyu, `[64:66]` = nyunanzaiMeigara, `[66:68]` = nyunanzaiKijun,
`[68:70]` = nyunanzaiJidoTonyu, `[70:72]` = suion。
`jidoTonyuZanryoFlag[54:56]` はビット: `&0b1`=洗剤残量少、`&0b10`=柔軟剤残量少。

### 7.1b 値の名称化 (コース相対・COURSEINF の `disp`)

- 0001 の各フィールド値 (2桁16進) は**絶対コードではなくコース相対のスケール**。
  COURSEINF (§8) の各コースが `disp` (value↔name) / `option` (選べる値) /
  `initial` (既定値) を持ち、**同一 value がコース間で別名称になる**。
  実測 (BD-STX110G・カタログ153コース): `arai=0C` → 12分/60分/105分、
  `dassui=01` → 1分/30分/自動、`kanso=13` → 20分/30分/60分。
  よって値を解釈するには**現在選択コース (selCourse) の `disp`** が必須。
- コースの `disp` で名称化できる (`disp` を持つ) フィールドは **9 個**
  (アプリの設定画面で表示されるもの):
  `arai` / `susugi` / `dassui` / `kanso` / `suiryo` / `sensor` /
  `jido_osouji` (= 0001 `autoOsouji`) / `oyutori` / `onsui_mist` (= 0001 `onsuiMisth`)。
  残り約 24 フィールドは `disp` なしのフラグ/内部用 (値解釈なし・生値のままでよい)。
- **洗剤/柔軟剤 (`senzaiKijun` / `nyunanzaiKijun`) は `disp` 参照ではなく 16進投入量 (mL)**。
  アプリは `getSenzaiKijun10()` = `BigInteger(hex,16).toString(10)` + " mL" と表示
  (BD01070:311 / BD01081:501) するので、実測 `0A` → **「10 mL」**。
  コース側の `senzai`/`softener` (disp: なし/自動…) は CTRLD 送信用のモード選択で、
  GETINF の Kijun (投入量の実値) とは別物。
- 全 35 フィールドは固定構造のため**常に返る**。コースで未対応の設定は値 `00` (なし) になるだけでフィールドは消えない。

### 7.2 sub=0002 のフィールド位置 (value 20桁)

| 位置 | フィールド | 位置 | フィールド |
|---|---|---|---|
| `[0:2]` | sentakuArai | `[10:12]` | araiInuiSusugi |
| `[2:4]` | sentakuSusugi | `[12:14]` | araiInuiDassui |
| `[4:6]` | sentakuDassui | `[14:16]` | araiInuiKanso |
| `[6:8]` | sentakuKanso | `[16:18]` | sentakuSuiryo |
| `[8:10]` | araiInuiArai | `[18:20]` | araiInuiSuiryo |

### 7.3 sub=0003 のフィールド位置 (value 28桁)

| 位置 | フィールド | 位置 | フィールド |
|---|---|---|---|
| `[0:4]` | yoyaku | `[12:14]` | onsuiMist |
| `[4:6]` | arai | `[14:16]` | sensor |
| `[6:8]` | susugi | `[16:18]` | autoOsouji |
| `[8:10]` | dassui | `[18:20]` | oyutori |
| `[10:12]` | kanso | `[20:22]` | suiryo |

`[22:24]` = senzai, `[24:26]` = nyunanzai, `[26:28]` = suion。

---

## 8. CTRLINS — 操作指令

洗濯機の操作 (開始/停止/コース設定) は CTRLC + CTRLD を送って行う。
**UDP とクラウドで CTRLD の渡し方が異なる** (§8.1 vs §8.2)。

### 8.1 UDP (DATA をカンマ結合)

```
CMD=CTRLINS  DATA = {TIME_hex(16)},{CTRLC},{CTRLD}
```

- `TIME` = `now_string_hex()` (16桁 hex)。
- 応答は `GRP=ACK` (受理) / `GRP=NAK` (拒否)。
- 実測: 42桁 CTRLD (§8.4) を送ると `GRP=ACK / CTRLINS DATA=00` が返り運転開始。

### 8.2 クラウド (明示パラメータ)

```
CMD=CTRLINS&SRC={app_src}&DST={app_dst}&TIME={TIME_dex(15)}&CTRLC={ctrlc}&CTRLD={ctrld}
```

- `TIME` = `now_string_dex()` (15桁 10進)。
- 応答 `ResCtrlIns`: **`SUCCESS=0` + `DATA=FF` が受理**。`SUCCESS=1/2` はエラー `E0013`。

### 8.3 CTRLC コード系統 (4桁 16進)

| CTRLC | 意味 | CTRLD |
|---|---|---|
| `0001` (BD00401) | 単純 開始=`02` / 停止=`03` | コース参照なし (実機 UDP では **NAK**) |
| `0003` (BD00304A/BD00313A) | **コース設定 + 開始** | 42桁コース文字列 (§8.4)。予約時は先頭4桁に**開始までの分数**の `%04X`、即時開始は `0000` |
| `0005` (BD00400) | その他の操作 | - |
| `0006` (BD00306) | コースセットダウンロード | §8.5 |
| (BD00310/311/801/1072/1073) | 動的系 | CTRLC 自体が可変 |

### 8.4 CTRLD (CTRLC=0003, 42桁)

| 区間 | 桁 | 内容 |
|---|---|---|
| `0000` | 4 | 開始マーカー。即時=`0000`、予約=**開始までの分数**の `%04X` (§8.4 注) |
| `course_no` | 4 | コース番号 (例 `1101`) |
| `moto_course_no` | 4 | 元コース番号 |
| `suiryo` | 2 | 水温 |
| `arai` | 2 | 洗い |
| `susugi` | 2 | すすぎ |
| `dassui` | 2 | 脱水 |
| `kanso` | 2 | 乾燥 |
| `tokusyu` | 8 | 特殊機能 |
| `onsui_mist` | 2 | お湯ミスト |
| `sensor` | 2 | センサ |
| `jido_osouji` | 2 | 自動お掃除 |
| `oyutori` | 2 | お取り置き |
| `senzai` | 2 | 洗剤 |
| `softener` | 2 | 柔軟剤 |
| **計** | **42** | |

- コース 1101 (標準コース, `susugi=02`) の例: `000011011101000C02040000000000000201000101`。
- **組み立ては `custom_components/bigdrum/course.py::build_course_ctrld()`** (CLI: `cctrld <course_no>`)。
  COURSEINF の `initial` を使用し、`initial="Z"` のフィールドは既定
  (`sensor=02, jido_osouji=01, senzai=01, softener=01`) を使う。
- 42 桁が実機の要求長。旧推測の 46 桁は拒否 (NAK / `SUCCESS=1`) の原因だった。

**開始マーカー (`[0:4]`) の意味** (アプリ `BD00313AFragment` / `BD00304AFragment` から確定):
- 予約開始は「**開始までの分数**」を `%04X` して置く (相対値。時刻の絶対値ではない)。
  アプリは予約時刻の epoch millis と現在時刻の差を分に変換し `String.format("%04X", minutes)` で置換する。
- 即時開始 = `0000`。
- 検証 (アプリと同一): `minutes == 0` / `minutes <= yoyaku.initial` / `minutes > 1440` で拒否。
  つまり **`yoyaku.initial < minutes <= 1440`** が有効域 (上限 1440 分 = 24 時間)。
- `yoyaku.initial` = コースごとの**予約最低リードタイム (分)**。COURSEINF の
  `course.N.yoyaku.initial` にある (BD-STX110G では `0` / `180` / `300` のみ)。
  例: `initial=180` のコースは 180 分より短く先に予約できない。
- HA サービス `bigdrum.start` の `delay_minutes` (10進・分) はこの分数そのもの。
  コード側で `f"{m:04X}"` に変換して `[0:4]` へ代入する。

### 8.5 CTRLD (CTRLC=0006, コースセットダウンロード)

`BD00306Fragment.getData` からデコンパイル。初期値 (`getInitial`) を使用:

```
[mDlMemory]
+ コース1/2/3 各: courseNo(4)+motoCourseNo(2)+arai+susugi+dassui+kanso+tokusyu(2)
+ onsuiMist+sensor+jidoOsouji+oyutori
+ (H世代モデル等の条件時 "00")
```

`BD-STX110G (01003517)` は `BD_SX120H (01003C16)` ではないため、
`appDst.startsWith(BD_SX120H)` 等のモデル判定は全て false。

---

## 9. クラウド API

### 9.1 login2 (認証)

- URL: `https://kadenfan.hitachi.co.jp/my/api/login2`
- `APPTYPE=8`。
- body は AES-256-CBC 平文 `email={id}&password={pass}&app_id=8` を
  **固定鍵/IV** (`cloud.MEMBERS_KEY` / `cloud.MEMBERS_IV`, APK `ConstantCo` の
  byte[] と `tests/test_cloud_offline.py` 冒頭で突き合わせてリグレッション検査) で暗号化。
- 成功時は k=v 応答: `cspf_id`, `token`, `ckdn_key`, `ckdn_iv`,
  `ckdn_token`, `ckdn_device_key` (UDP 鍵 dst,key,iv を含む)。
  `ckdn_device_key` の末尾バイナリフレームは無視し**先頭 hex** を採用。
- **失敗は HTTP 200 + 16B バイナリ** `status<HEX3>` + NULパッド + `0x10`
  (`9E2`=形式不正, `9E3`=認証エラー)。HTTP エラーにはならない。

### 9.2 GLS API

- URL: `https://tkg.capsys21.hitachi-gls.co.jp/cgi-bin/ap_outer_ctrl_filter2.cgi`
- body は §2.2 のように `ckdn_key`/`ckdn_iv` で AES 暗号化 (§3.2 の k=v)。
- **認証は `User-Agent: {ckdn_token}`**。`okhttp/...` 等の UA では **401** になる。
- 応答は k=v (`GRP=..&CMD=..&[DATA=..&SUCCESS=..]`) や JSON (COURSEINF)。

#### CMD 一覧 (クラウド)

| CMD | パラメータ | 用途 |
|---|---|---|
| `GETDEVIDHID` | - | デバイス情報・HID 取得 |
| `MOVEINF` | SRC, DST | 運転状態 (§6) |
| `GETINF` | SRC, DST, DATA(=sub) | 設定・状態 (§7) |
| `CTRLINS` | SRC, DST, TIME, CTRLC, CTRLD | 操作 (§8.2) |
| `COURSEINF` | MODEL, VERSION | コースカタログ (§10) |
| `FWINFO` | SRC, DST | 本体のバージョン (応答 `FWVER` / `FWMODE` / `FWSTS`) |
| `PUSHTOKEN` / `PUSHREG` / `PUSHINF` | - | FCM 通知 (§11) |

- `FWINFO` 応答は k=v (`GRP=0&FWVER=...&FWMODE=...&FWSTS=...`)。`FWVER` がアプリの「本体のバージョン」(例 `04051122`)。ファーム更新時のみ変わる。**UDP では取得できずクラウド API のみ**。

### 9.3 コマンド ↔ API 対応 (CLI)

| CLI | 経路 | 実 API |
|---|---|---|
| `status` / `cstatus` | UDP / クラウド | MOVEINF |
| `search` | UDP | SEARCH |
| `getinf` / `cgetinf` | UDP / クラウド | GETINF |
| `ctrl` / `cctrl` | UDP / クラウド | CTRLINS |
| `ccourse` | クラウド | COURSEINF |
| `cctrld` | クラウド (組み立て+送信) | COURSEINF → CTRLINS |

---

## 10. COURSEINF — コースカタログ

- `CMD=COURSEINF&MODEL={dst[:8]}&VERSION={version}`。
- 応答は JSON: `{"code":"0000", "msg":"success", "data":{"course_set":[...]}}`。
- 構造: `course_set[]` → 各 `course_List` → `course1` / `course2` / `course3`。
- 各 course フィールド:

| キー | 意味 |
|---|---|
| `course_no` | コース番号 (例 `1101`) |
| `course_tab` | タブ名 (例 `標準`) |
| `course_type` | 種別 (例 `11`) |
| `moto_course_no` | 元コース番号 |
| `tokusyu` | 特殊機能 (8桁) |
| `untenjikan` | 運転時間 |
| `yoyaku` | 予約 |
| `suiryo` / `arai` / `susugi` / `dassui` / `kanso` | 水温/洗い/すすぎ/脱水/乾燥 |
| `onsui_mist` / `sensor` / `jido_osouji` / `oyutori` / `senzai` / `softener` | 各オプション |

- 各オプションフィールドは `{"initial": "<2桁>", ...}` の形式で初期値 (initial) を持つ。
  `initial="Z"` は「未定」で、CTRLD 組み立て時は既定値 (§8.4) を使う。
- 実機カタログは `captures/courseinf_01003517.json` に保存済み。
- `selCourse` (MOVEINF blob, §6.3) の course_no は**先頭2桁=操作** (11=洗濯 / 12=洗乾 /
  13=乾燥 …)、**末尾2桁=コース種別**。同じ `course_set_name` (例「標準コース」) を
  1101/1201/1301 が共有し、`course_tab` (洗濯/洗乾/乾燥…) で操作を区別する。
  (HA 側の表示は **HOMEASSISTANT.md** 参照)

---

## 11. プッシュ通知 (FCM)

- **プッシュは FCM のみ**:
  - `PUSHTOKEN` = FCM token 登録
  - `PUSHREG` = 購読
  - `PUSHINF` = 未読取得
- サーバー側の購読 API が無いため**プッシュ受信は不可** (実運用は下記ポーリングに依る)。
- 公式アプリ自体は**ポーリング方式** (`LocalService.java`):
  - 自宅 Wi-Fi = UDP MOVEINF **3秒**
  - 外出先 = クラウド **60秒**
- FCM 通知は「受信時に 1 回だけポーリングを発動する補助」に過ぎない。
- 洗濯機は LAN でも不意打ち UDP を送らない (全て要求応答型, §5)。

---

## 12. パケット捕捉

- 洗濯機の UDP は**ユニキャスト**のため **PC 側での sniff は不可**。
- スマホ側で **PCAPdroid** (root 不要・VPN 方式) で取得。
- `tools/decrypt_capture.py` が pcapng を読み AES で復号し、送受信の平文を
  抽出・比較する (依存: `dpkt`。`dpkt.ip.ip2str` は無いので `socket.inet_ntoa` を使用)。
- 復号結果は `tools/capture_analysis.txt` (git 対象外) に出力。

