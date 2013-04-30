# swift-ring-sync

## 概要

OpenStack Object Storage(Swift)のRingデータベースを、swiftクラスタを構成するマシン群に一斉配布するためのツールです。

このツールを利用する場合、その対象であるSwiftシステム自身のコンテナに、配布するRingファイルを格納します。
各マシンはこのツールを利用して、コンテナからRingファイルをダウンロードして、自分のRingファイルを更新します。

swift-ring-syncはコンテナ上のRingファイルと実行マシン上のRingファイルを同期するためのプログラムです。

swift-ring-uploaderはRingファイルをアップロードするためのプログラムです。


## 実行方法
```
swift-ring-sync 設定ファイル
```

実行すると設定されたファイル(設定句: rings)を、コンテナ（設定句: container）からダウンロードし、リング用ディレクトリ(設定句: ring_dir)に置きます。
コンテナ上のRingファイルに変更がなければ、何も行いません。

Ringファイルのダウンロードが発生した際には、古いRingファイルはバックアップディレクトリ(設定句: ring_backup_dir)に移動されます。バックアップファイルは指定された個数(設定句: max_backup)保存されますが、それ以上は古いものから順番に自動的に削除されます。

Ringファイルの置き換えはアトミックに行われるため、プログラムが異常終了してもRingファイルが破損するおそれはありません。

動作結果は標準出力にメッセージで出力されます(設定句: quiet)。ログファイルを指定(設定句: log)すれば同じ内容をログに出力します。

正常終了の場合、コマンドの終了コードは0を返します。それ以外の場合には0以外を返します。


```
swift-ring-updater 設定ファイル
```

実行すると設定されたファイル(設定句: rings)を、コンテナ（設定句: container）へアップロードします。

動作結果は標準出力にメッセージで出力されます(設定句: quiet)。ログファイルを指定(設定句: log)すれば同じ内容をログに出力します。

正常終了の場合、コマンドの終了コードは0を返します。それ以外の場合には0以外を返します。


### 認証について
このプログラムは、swiftの動作しているマシンから実行することを前提としています。その際にはkeystone等による認証をスキップし、指定されたアカウントのコンテナに直接アクセスします。これがデフォルトの動作です。

swiftの動作しているマシン以外から実行する場合には、以下のような設定を行ってください。通常の認証基盤を経たアクセスが実施されます。

```
use_internal = no
auth_url = http://127.0.0.1:8080/auth/v1.0
identity = admin:admin
password = admin
```

#### コマンドフック

以下の設定を指定することで、指定のタイミングで任意のコマンドを実行します。

* 同期実行前: hook_command_pre_sync
* 同期成功時: hook_command_sync_success
* 同期失敗時: hook_command_sync_failure
* 同期実行後: hook_command_post_sync

* アップロード実行前: hook_command_pre_upload
* アップロード成功時: hook_command_upload_success
* アップロード失敗時: hook_command_upload_failure
* アップロード実行後: hook_command_post_upload


フックコマンド失敗時にはそれを呼び出しているスクリプト自体の実行も中断します。フックコマンドが失敗してもスクリプトの動作を続行するためには、\*_continue_on_failの値をyesとします（デフォルトはnoです）

```
[ring-sync]
hook_command_pre_sync_continue_on_fail = yes
hook_command_sync_success_continue_on_fail = yes
hook_command_sync_failure_continue_on_fail = yes
hook_command_post_sync_continue_on_fail = yes
[ring-uploader]
hook_command_pre_upload_continue_on_fail = yes
hook_command_upload_success_continue_on_fail = yes
hook_command_upload_failure_continue_on_fail = yes
hook_command_post_upload_continue_on_fail = yes
```

#### 環境変数
設定ファイルの各設定句は、コマンドから環境変数として呼び出すことができます。

たとえば、ring_dirの設定値は、${srs_ring_dir}として呼び出すことができます。

```
hook_command_post_sync = ls -l ${srs_ring_dir}/*.ring.gz
```

とすれば、Ringファイルの同期完了後のRingファイルの情報を得ることができます

ringsを${srs_rings}として呼び出すと、"account.ring.gz container.ring.gz object.ring.gz"といったスペースで区切られた文字列が返ります。フックコマンドにシェルスクリプトを指定して、シェルスクリプトの中からこの環境変数を利用することもできます

```
 #!/bin/bash
 for i in ${srs_rings}
 do
   echo ${i}
 done
 exit 0
```

Ringファイルごとの実行結果も環境変数で取り出すことができます。結果が複数の場合にはスペースで区切られた文字列となります。
 * srs_sync_no_need: 同期の必要のなかったRingファイル
 * srs_sync_success: 同期に成功したRingファイル
 * srs_sync_failure: 同期に失敗したRIngファイル
 * srs_upload_no_need: アップロードの必要のなかったRingファイル
 * srs_upload_success: アップロードに成功したRingファイル
 * srs_upload_failure: アップロードに失敗したRingファイル


##### 環境変数一覧
###### swift-ring-sync
 * srs_container
 * srs_ring_dir
 * srs_ring_backup_dir
 * srs_auth_url
 * srs_identity
 * srs_password
 * srs_account_id
 * srs_max_backup
 * srs_rings
 * srs_sync_no_need
 * srs_sync_success
 * srs_sync_failure

###### swift-ring-uploader
 * srs_container
 * srs_ring_dir
 * srs_auth_url
 * srs_identity
 * srs_password
 * srs_account_id
 * srs_rings
 * srs_upload_no_need
 * srs_upload_success
 * srs_upload_failure


## 設定ファイル

### swift-ring-sync用設定句
 [ring-sync]

* use_internal
  *  内部モードを利用するか（swiftサーバ内部からのアクセス）デフォルト: yes
* account_id
  * 内部モード利用時のアカウントID デフォルト: なし
* auth_url
  * 内部モードを利用しない場合の認証先URL デフォルト: なし
* identity
  * 内部モードを利用しない場合の認証ユーザ名 デフォルト: なし
* password
  * 内部モードを利用しない場合の認証パスワード デフォルト: なし
* container_name
  * Ringファイルを格納しているコンテナ名 デフォルト: rings
* ring_dir
  * Ringファイルをダウンロードする先のディレクトリ デフォルト: /etc/swift
* ring_backup_dir
  * 古いRingファイルをバックアップする先のディレクトリ デフォルト: /etc/swift/backup
* max_backup
  * 古いRingファイルをバックアップする数 デフォルト: 8
* log
  * ログファイル  デフォルト: なし
* rings
  * 同期対象のファイル名 デフォルト: account.ring.gz container.ring.gz object.ring.gz
  * この設定句に設定した値が、デフォルト値を上書きします。同期したいファイルを追加する場合には、デフォルト値を含めて全てのファイル名を列挙して下さい。
  * Ex: account.ring.gz container.ring.gz object.ring.gz account.builder container.builder object.builder
* hook_command_pre_sync
  *  同期の実行前に実行するコマンドを指定します。デフォルト: なし
* hook_command_pre_sync_continue_on_fail
  * hook_command_pre_syncで指定したコマンドが失敗した場合の挙動を指定します。yesの場合はコマンドが失敗してもswift-ring-syncは動作を続けます。noの場合はswift-ring-syncも終了します。その場合のswift-ring-syncの終了コードはhook_command_pre_syncのものになります。デフォルト: no
* hook_command_sync_success
  * 同期が成功した場合に実行するコマンドを指定します（この成功には同期の必要がなかった場合は含まれません）。デフォルト: なし
* hook_command_sync_success_continue_on_fail
  * hook_command_sync_successで指定したコマンドが失敗した場合の挙動を指定します。yesの場合はコマンドが失敗してもswift-ring-syncは動作を続けます。noの場合はswift-ring-syncも終了します。その場合のswift-ring-syncの終了コードはhook_command_sync_successのものになりますデフォルト: no
* hook_command_sync_failure
  * 同期が失敗した場合に実行するコマンドを指定します。デフォルト: なし
* hook_command_sync_failure_continue_on_fail
  * hook_command_sync_failureで指定したコマンドが失敗した場合の挙動を指定します。yesの場合はコマンドが失敗してもswift-ring-syncは動作を続けます。noの場合はswift-ring-syncも終了します。その場合のswift-ring-syncの終了コードはhook_command_sync_failureのものになります。デフォルト: no
* hook_command_post_sync
  * 同期の実行後に実行するコマンドを指定します。デフォルト: なし
* hook_command_post_sync_continue_on_fail
  * hook_command_post_syncで指定したコマンドが失敗した場合の挙動を指定します。yesの場合はコマンドが失敗してもswift-ring-syncは動作を続けます。noの場合はswift-ring-syncも終了します。その場合のswift-ring-syncの終了コードはhook_command_post_syncのものになります。このフックはswift-ring-syncの終了する直前に実行されるので、yesとnoの相違は終了コードが同期の結果か、hook_command_post_syncの結果であるかの違いになります。デフォルト: no
* quiet
  * 実行経過のメッセージを標準出力しません。同じ内容はログには出力されます。また、フックコマンドの標準出力には影響しません。デフォルト: no


### swift-ring-uploader用設定句
 [ring-uploader]

 * use_internal
   * 内部モードを利用するか（swiftサーバ内部からのアクセス）デフォルト: yes
 * account_id
   * 内部モード利用時のアカウントID デフォルト: なし
 * auth_url
   * 内部モードを利用しない場合の認証先URL デフォルト: なし
 * identity
   * 内部モードを利用しない場合の認証ユーザ名 デフォルト: なし
 * password
   * 内部モードを利用しない場合の認証パスワード デフォルト: なし
 * container_name
   * Ringファイルを格納しているコンテナ名 デフォルト: rings
 * ring_dir
   * アップロードするRingファイルの存在しているディレクトリ デフォルト: /etc/swift
 * log
   * ログファイル  デフォルト: なし
 * rings
   * アップロード対象のファイル名 デフォルト: account.ring.gz container.ring.gz object.ring.gz
   * この設定句に設定した値が、デフォルト値を上書きします。アップロードしたいファイルを追加する場合には、デフォルト値を含めて全てのファイル名を列挙して下さい。
   * Ex: account.ring.gz container.ring.gz object.ring.gz account.builder container.builder object.builder
 * hook_command_pre_upload
   * アップロードの実行前に実行するコマンドを指定します。デフォルト: なし
 * hook_command_pre_upload_continue_on_fail = no
   * hook_command_pre_uploadで指定したコマンドが失敗した場合の挙動を指定します。yesの場合はコマンドが失敗してもswift-ring-uploaderは動作を続けます。noの場合はswift-ring-uploaderも終了します。その場合のswift-ring-uploaderの終了コードはhook_command_pre_uploadのものになります。デフォルト: no
 * hook_command_upload_success
   * アップロードが成功した場合に実行するコマンドを指定します。デフォルト: なし
 * hook_command_upload_success_continue_on_fail = no
   * hook_command_upload_successで指定したコマンドが失敗した場合の挙動を指定します。yesの場合はコマンドが失敗してもswift-ring-uploaderは動作を続けます。noの場合はswift-ring-uploaderも終了します。その場合のswift-ring-uploaderの終了コードはhook_command_upload_successのものになりますデフォルト: no
 * hook_command_upload_failure
   * アップロードが失敗した場合に実行するコマンドを指定します。デフォルト: なし
 * hook_command_upload_failure_continue_on_fail
   * hook_command_upload_failureで指定したコマンドが失敗した場合の挙動を指定します。yesの場合はコマンドが失敗してもswift-ring-uploaderは動作を続けます。noの場合はswift-ring-uploaderも終了します。その場合のswift-ring-uploaderの終了コードはhook_command_upload_failureのものになります。デフォルト: no
 * hook_command_post_upload
   * アップロードの実行後に実行するコマンドを指定します。デフォルト: なし
 * hook_command_post_upload_continue_on_fail
   * hook_command_post_uploadで指定したコマンドが失敗した場合の挙動を指定します。yesの場合はコマンドが失敗してもswift-ring-uploaderは動作を続けます。noの場合はswift-ring-uploaderも終了します。その場合のswift-ring-uploaderの終了コードはhook_command_post_uploadのものになります。このフックはswift-ring-loaderの終了する直前に実行されるので、yesとnoの相違は終了コードが同期の結果か、hook_command_post_uploadの結果であるかの違いになります。デフォルト: no
* quiet
  * 実行経過のメッセージを標準出力しません。同じ内容はログには出力されます。また、フックコマンドの標準出力には影響しません。デフォルト: no

以上
