# Техническое исследование: Архитектура переключения аккаунтов в Google Antigravity IDE (macOS)

> **Статус документа:** Финализировано по результатам реверс-инжиниринга бинарников и структуры macOS.  
> **Проект:** QuotaAnalytics / Native Account Switcher  
> **Дата актуализации:** Ноябрь 2026  

---

## 1. Введение и цели исследования

В рамках проекта `QuotaAnalytics` решена задача мониторинга 5-часовых и недельных квот Google Cloud Code для 6 аккаунтов. Следующим логическим шагом является разработка собственного нативного модуля переключения активного аккаунта (**Account Switcher**) без использования тяжелых сторонних утилит и без риска повреждения пользовательских данных или блокировки учетных записей.

В данном документе консолидированы все проверенные факты, технические особенности хранения авторизации в macOS, результаты аудита существующих решений на рынке и оценка рисков.

---

## 2. Анализ рынка готовых свитчеров

В экосистеме было детально изучено поведение ведущих инструментов:
- **`lbjlaq/Antigravity-Manager`** (~32.9k ⭐, Rust/Tauri v2 + Axum) — абсолютный лидер, используемый для ротации и трансляции API.
- **`jlcodes99/cockpit-tools`** (~19.2k ⭐, Go/Electron) — мульти-IDE комбайн.
- **`Draculabo/AntigravityManager`** (~2.5k ⭐, Electron/React).
- **`anglee0323/agy-switch`** (~123 ⭐, Rust/Tauri) — виджет Menu Bar.
- **`Muriel-Gasparini/antigravity-account-switcher`** (~19 ⭐, Go) — реверс-прокси.

### Ключевой вывод анализа рынка:
1. **Невозможность «горячей» смены без рестарта IDE:** Ни один существующий в мире инструмент не меняет аккаунт в работающем окне десктопной Antigravity IDE без перезапуска. Процесс Extension Host (Node.js) и языковой сервер кэшируют токен в оперативной памяти и удерживают эксклюзивный файловый замок на SQLite-базе `state.vscdb`.
2. **Отказ от прокси-подхода:** Прокси-ротаторы (перехватывающие порт `127.0.0.1` на лету) крайне хрупки к обновлениям Google API, требуют постоянного фонового демона и не решают задачу для официального GUI.
3. **Стандарт надежности:** Все ведущие решения используют архитектуру **Graceful Native Switch** — контролируемое закрытие IDE, атомарная запись во все слои хранилища, очистка кэша скоупов и запуск приложения заново.

---

## 3. Точная карта хранения авторизации Antigravity на macOS

В отличие от простых приложений, Antigravity IDE распределяет состояние между четырьмя изолированными хранилищами:

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                            Google Antigravity IDE                            │
└──────┬───────────────────────┬───────────────────────┬───────────────────────┘
       │                       │                       │
       ▼                       ▼                       ▼
[1. SQLite state.vscdb]   [2. macOS Keychain]     [3. authstore Scope]     [4. app_storage.json]
 • USS Topic Protobuf      • service="gemini"      • credential-user-scope  • lastLoginUsername
 • authStateSentinel       • service="antigravity" • PinnedScope hash       • lastLoginIsGcpTos
 • oauthTokenInfoProto     • go-keyring-base64     • MUST BE DELETED!       • GUI active account
```

### 3.1. Главная база состояния: `User/globalStorage/state.vscdb`
* **Расположение:** `~/Library/Application Support/Antigravity/User/globalStorage/state.vscdb`
* **Формат:** База данных SQLite (таблица `ItemTable (key TEXT PRIMARY KEY, value TEXT)`).
* **Шифрование SafeStorage / DPAPI:** **ОТСУТСТВУЕТ**. Все данные хранятся как открытый Base64 Protobuf.
* **Ключевые поля:**
  1. `antigravityUnifiedStateSync.oauthToken`:  
     Внешний топик Unified State Sync (USS) в Protobuf-кодировке. Содержит две связки:
     - `authStateWithContextSentinelKey`: JSON-объект `{"state":"signedIn","context":{"project":"","showProjectError":false,...}}`.
     - `oauthTokenInfoSentinelKey`: Base64 от вложенного бинарного Protobuf сообщения `OAuthTokenInfo` (содержит `access_token`, тип `Bearer`, `refresh_token` и Unix timestamp истечения).
  2. `antigravityAuthStatus`:  
     JSON вида `{"name":"username","email":"user@gmail.com","apiKey":"ya29..."}`.
  3. `antigravityOnboarding`:  
     Строковое значение `"true"`.
  4. `jetskiStateSync.agentManagerInitState`:  
     **УДАЛЯЕТСЯ** (`DELETE FROM ItemTable WHERE key = ...`) для сброса старого контекста.
  5. `antigravityUnifiedStateSync.userStatus`:  
     **УДАЛЯЕТСЯ** при смене, что инициирует автоматический запрос `fetchAvailableModels` и актуализирует список доступных моделей под новый профиль.

### 3.2. Системная связка ключей: macOS Keychain
* **Сервисы:** `gemini` и `antigravity`.
* **Аккаунт:** `antigravity`.
* **Формат данных:** `go-keyring-base64:<Base64(JSON)>`, где JSON имеет структуру:
  ```json
  {
    "token": {
      "access_token": "ya29...",
      "token_type": "Bearer",
      "refresh_token": "1//...",
      "expiry": "2026-11-07T15:30:00.000000Z",
      "expires_in": 3599
    },
    "auth_method": "consumer",
    "id_token_email": "target@gmail.com"
  }
  ```
* **Механика обновления:** Обновление производится атомарной утилитой без предварительного удаления (исключает пустое состояние):
  ```bash
  security add-generic-password -s gemini -a antigravity -w "go-keyring-base64:..." -U -A
  security add-generic-password -s antigravity -a antigravity -w "go-keyring-base64:..." -U -A
  ```

### 3.3. Скрытый идентификатор скоупа: `credential-user-scope`
* **Расположение:** `~/Library/Application Support/Antigravity/credential-user-scope`
* **Назначение:** Содержит хэш вида `u7e61e91ef6b54a75`. 
* **Поведение бинарника `language_server`:** В модуле `google3/third_party/jetski/cortex/utils/authstore` сервер сохраняет текущий `PinnedScope`. Если этот файл не удалить при смене учетной записи, языковой сервер продолжит опрашивать MCP-инструменты и контекст от лица предыдущего пользователя. **Файл обязательно удаляется при переключении.**

### 3.4. Метаданные клиента: `app_storage.json`
* **Расположение:** `~/Library/Application Support/Antigravity/app_storage.json`
* **Поля для обновления:**
  - `agy.onboarding.lastLoginUsername`: `"target@gmail.com"`
  - `agy.onboarding.lastLoginIsGcpTos`: `false`

### 3.5. Сетевой кэш соединений: `~/Library/HTTPStorages/com.google.antigravity`
* Папка сетевого кэша macOS URLSession/Chromium. Удаляется при смене для принудительного разрыва keep-alive соединений по HTTP/2 к Google Cloud Code PA.

---

## 4. Оценка и элиминация рисков (Risk Mitigation)

| Риск | Причина возникновения | Решение в нашем свитчере |
| :--- | :--- | :--- |
| **Рассинхрон сессий (Ghost Session)** | Частичная запись (например, обновили JSON, но не Keychain) | Все 4 слоя обновляются строго в единой атомарной транзакции Python-скрипта. |
| **Прерывание активной работы агента** | Пользователь нажал смену, пока агент генерирует код | **Pre-flight Socket Check**: проверка активных TCP-соединений к Google API (`lsof -nP -iTCP -sTCP:ESTABLISHED`). При активности свитч блокируется с предупреждением. |
| **Сбой из-за протухшего токена** | У целевого аккаунта истек или отозван refresh-токен | **Pre-flight Token Refresh**: скрипт сначала обновляет токен через Google OAuth API. Если Google вернул ошибку, IDE не закрывается, переключение прерывается. |
| **Блокировка базы SQLite (`SQLITE_BUSY`)** | Конкурентный доступ из стороннего процесса во время закрытия IDE | Мягкое закрытие через AppleScript (`tell app "Antigravity" to quit`) + циклический опрос `ps` и `SingletonLock` до полного снятия блокировок. |
| **Ошибка `HTTP 400 Bad Request`** | Попытка продолжить старый чат с HMAC-подписью мыслей | Чистый перезапуск процесса IDE формирует валидное официальное рукопожатие с Google Cloud Code. |
| **Потеря истории диалогов** | Страх повредить проекты | **0% риска**: история чатов хранится в `~/.gemini/antigravity/brain/<UUID>/` изолированно по UUID сессий и физически не затрагивается. |

---

## 5. Выводы для разработки

Разработка собственного свитчера в рамках `QuotaAnalytics` является полностью осуществимой, безопасной и превосходит сторонние решения за счет:
1. **Отсутствия лишних зависимостей:** Решение реализуется на стандартной библиотеке Python 3 (`sqlite3`, `subprocess`, `urllib`, `json`).
2. **Прозрачности кода:** 0% закрытых скомпилированных бинарников.
3. **Бесшовной интеграции:** Прямая связь с `accounts.json`, SwiftBar меню-баром и Liquid Glass Dashboard.
