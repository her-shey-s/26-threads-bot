# Threads 트렌드·레퍼런스 수집기

첨부 계획서를 바탕으로 만든 Python + SQLite MVP의 초기 구조입니다.
현재는 **가상 데이터로 수집 → 중복 제거 → 특징 추출 → 인기/시간 보정 TOP 10 → SQLite·JSON·CSV 저장**을 실행합니다.
실제 Threads 검색, 외부 트렌드 수집, 자동 게시, Telegram 전송은 구현하지 않았습니다.
**Threads 자동 게시 루프**는 dry-run까지 구현했고 실제 Threads 게시는 아직 검증하지 않았습니다. 계획은 아래 [로드맵](#로드맵)을 참고하세요.

## 실행

Python 3.11 이상이 필요하며, 애플리케이션의 외부 런타임 의존성은 없습니다.
저장소 루트에서 실행하세요.

```bash
python -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m threads_bot --source demo
.venv/bin/python -m unittest discover -s tests -v
```

재현 가능한 샘플 실행:

```bash
.venv/bin/python -m threads_bot --source demo --at 2026-10-10T09:00:00+09:00
```

- 가상 검색 결과 25개 → 고유 게시물 24개 → 인기 10개·시간 보정 10개
- 인용 수치가 없는 3개는 후보로 저장하고 두 순위에서 제외
- `data/demo.sqlite3`: 게시물, 관측 지표, 특징, 검색어, 일일 순위, 실행 이력
- `reports/demo/YYYY-MM-DD.json`: 순위, 후보별 누락 사유, 텍스트 특징
- `reports/demo/YYYY-MM-DD.csv`: 두 순위의 게시물 ID·점수·시각·URL

DB와 보고서는 Git에서 제외됩니다. 날짜 기준은 기본 `Asia/Seoul`, DB 시각은 UTC입니다.
`--database`, `--reports`, `--timezone`으로 경로와 보고서 시간대를 변경할 수 있습니다.
데모에는 실사용 토큰이나 네트워크 연결이 필요 없습니다.

## 코드 구조

```text
src/threads_bot/
  models.py      # 게시물·관측값·검색어 데이터 계약
  sources.py     # 교체 가능한 TrendSource/PostSource + 가상 수집기
  ranking.py     # 게시물 ID 중복 제거, 점수 산정, TOP 10
  features.py    # 길이·첫 줄·질문·본문 링크·미디어 수
  storage.py     # SQLite 스키마와 저장
  pipeline.py    # 일일 실행, 실행 이력, JSON/CSV 출력
  cli.py         # 명시적 --source demo 실행 진입점
tests/           # 랭킹·누락값·재실행·실패·전체 흐름 검증
docs/            # 설계 범위와 실제 API 연동 준비
.github/workflows/
  ci.yml         # push/PR 테스트
  demo.yml       # 수동 샘플 실행 및 7일 보관 아티팩트
  publish.yml    # Threads 게시 (수동 실행만, 예약은 주석 처리)
```

## 점수와 저장 규칙

인기 점수 `E = likes + 3×replies + 4×reposts + 3×quotes`.
시간 보정 점수 `S = E / (age_hours + 2)^0.7`.
계획서의 실험용 가중치이며 실제 반응 증가율이나 Threads 공식 순위를 의미하지 않습니다.

반응 지표 하나라도 없으면 `NULL`과 사유를 보관하고 점수 계산에서 제외합니다.
게시 시간이 없거나 미래이면 시간 보정 순위만 제외합니다. 관측된 0은 유효합니다.
같은 ID가 여러 번 검색되면 가장 최근의 관측 한 건을 사용합니다. 같은 시각이면 먼저 받은 건을 유지합니다.
지표를 합산하거나 서로 다른 관측의 필드를 섞지 않습니다.

동일 게시물·관측 시각은 한 번 저장합니다. 이후 관측은 지표 이력에 누적하고,
같은 날짜의 순위·보고서는 최신 실행으로 교체합니다. 실행 시도 이력은 모두 남깁니다.
빈 수집 결과는 실패로 기록하며 기존 순위를 삭제하지 않습니다.
제휴 여부는 `unknown`, 댓글 링크는 `not_collected`로 표시합니다.

## 로드맵

사용자가 정한 우선순위입니다. 주제(니치)는 나중에 사용자가 지정합니다.

1. **Threads 자동 게시 루프** ← 진행 중: 코드·dry-run 완료, 실게시 미검증
2. AI 글 자동 생성 모듈 (게시 대기열에 초안을 넣는 역할)
3. 주제·말투 커스터마이징 (프롬프트·스타일 설정을 생성 모듈에 입힘)
4. 기존 트렌드 수집기의 실데이터 연동 ([API 확인 항목](docs/phase-0.md)) — 생성 모듈의 주제 입력으로 활용

## Threads 자동 게시 루프

목표: **사람이 클릭하지 않아도 정해진 시각에 대기열의 글이 내 Threads 계정에 올라가는 것.**
이 단계에서는 AI 생성 없이 사람이 쓴 글을 대기열에 넣어 게시합니다. 생성 모듈은 2단계에서 같은 대기열에 붙입니다.

아래 API 내용은 Meta 공식 문서를 이 환경에서 직접 열람하지 못하고 검색 결과로 확인한 것입니다.
**"확인됨" 표시가 없는 항목은 실제 앱으로 호출해 본 뒤 [phase-0](docs/phase-0.md)에 근거를 남겨야 합니다.**

### A. 사용자가 직접 해야 하는 준비 (코드로 대신할 수 없음)

1. [Meta for Developers](https://developers.facebook.com/)에 개발자 계정을 만들고, **Threads API 사용 사례**로 앱을 생성합니다.
2. 앱에 게시 권한을 추가합니다. 검색 결과상 이름은 `threads_basic`, `threads_content_publish`입니다(대시보드에서 실제 이름 확인).
3. 앱 역할에 본인 Threads 계정을 **테스터**로 추가하고, Threads 앱 설정에서 초대를 수락합니다.
   내 계정에만 게시할 때 앱 심사 없이 테스터 역할만으로 되는지는 **미확인**입니다.
4. OAuth로 발급한 단기 토큰(약 1시간)을 `th_exchange_token`으로 **장기 토큰(60일)**으로 교환합니다.
5. 장기 토큰과 Threads 사용자 ID를 **GitHub 저장소 Secrets**에 저장합니다(예: `THREADS_ACCESS_TOKEN`, `THREADS_USER_ID`).
   토큰을 코드·커밋·로그·채팅에 붙여넣지 마세요.

### B. 게시 API 흐름 (검색 결과 기준)

```text
1) POST /{threads-user-id}/threads           media_type=TEXT, text=...    → 컨테이너 ID
2) (컨테이너 상태 확인 후)
3) POST /{threads-user-id}/threads_publish   creation_id=<컨테이너 ID>   → 게시물 ID
4) 링크·대가성 문구는 본인 게시물에 댓글로: 1)에 reply_to_id=<게시물 ID> 추가 후 동일 흐름
```

| 항목 | 검색으로 본 내용 | 상태 |
| --- | --- | --- |
| 게시 한도 | 계정당 24시간 이동 구간 기준 API 게시 250개, `GET /{id}/threads_publishing_limit`로 사용량 조회 | 공식 개요 문서 인용, 실호출 미확인 |
| 본문 길이 | 500자 (이모지 계산 방식 주의) | 서드파티 가이드, 미확인 |
| 토큰 | 장기 토큰 60일, 발급 후 24시간 이상 지난 만료 전 토큰을 `GET /refresh_access_token?grant_type=th_refresh_token`으로 갱신 | 공식 문서 인용, 실호출 미확인 |
| 컨테이너 생성~게시 대기, 상태 필드·오류 값 | — | 미확인 |
| 본문 링크 개수 제한·링크 미리보기 파라미터 | — | 미확인 |
| API 자체 예약 게시 기능 | — | 미확인 (없다고 가정하고 스케줄러를 직접 둠) |

### C. 구현 (dry-run까지 완료, 실게시 미검증)

사용법:

```bash
# 대기열 상태 확인 (게시 예정 / due / 이미 처리됨)
.venv/bin/python -m threads_bot.publish_cli check
# 예정 시각이 지난 글 1개를 dry-run으로 처리 (네트워크 호출 없음, 이력은 data/에 저장)
.venv/bin/python -m threads_bot.publish_cli run
# 실게시: THREADS_USER_ID, THREADS_ACCESS_TOKEN 환경 변수가 있어야 동작, 이력은 content/history.jsonl
.venv/bin/python -m threads_bot.publish_cli run --mode live
```

대기열 파일 `content/queue/<id>.json` (예시는 `content/examples/`, 여기 있는 파일은 게시되지 않음):

```json
{
  "text": "본문 (필수)",
  "scheduled_at": "2026-11-20T20:00:00+09:00",
  "affiliate": true,
  "reply_text": "링크와 대가성 문구 (선택, 본인 글에 댓글로 게시)"
}
```

| 결과 | 의미 | 다음 실행에서 |
| --- | --- | --- |
| `published` | 게시 성공 (댓글 실패는 별도 기록, 본문은 다시 올리지 않음) | 완료 |
| `waiting` | 일일 상한·최소 간격·API 한도 여유 부족 | 다시 시도 |
| `rejected` | 길이 초과·중복·대가성 문구 누락 | 제외 (파일 수정 시 새 id로 다시 넣기) |
| `retry` | 컨테이너 단계 일시 오류 | 최대 3회까지 재시도 후 `failed` |
| `uncertain` | 게시 단계에서 응답을 못 받음 (올라갔을 수도 있음) | **자동 재시도 안 함, 사람이 확인** |
| `auth_error` | 토큰 만료·권한 부족 (종료 코드 3) | 이력에 남기지 않아 토큰 수정 후 재게시 |

기본 안전값: 하루(24시간 이동 구간) 3개, 게시 간 120분, 500자. `--max-per-day`, `--min-interval-minutes`로 변경합니다.
환경 변수 `THREADS_PUBLISH_PAUSED=true`이면 아무것도 게시하지 않습니다.

GitHub Actions `Threads publish` 워크플로(`.github/workflows/publish.yml`)는 현재 **수동 실행만** 켜져 있습니다.

- 수동 실행에서 `mode`를 `dry-run` 또는 `live`로 고릅니다. live 실행 후 `content/history.jsonl`을 저장소에 커밋합니다.
- 예약 실행은 파일의 `schedule` 주석을 풀어 켭니다. 예약 실행의 모드는 저장소 변수 `THREADS_SCHEDULED_MODE`(`live`일 때만 실게시)로 정합니다.
- 긴급 정지는 저장소 변수 `THREADS_PUBLISH_PAUSED=true`로 합니다.

설계 흐름:

```text
GitHub Actions cron (예: 하루 N회)
  → 대기열에서 "게시 예정 시각 ≤ 지금"이고 아직 안 올린 글 1개 선택
  → 안전장치 검사 (아래)
  → Publisher.publish(draft)   # DryRunPublisher(기본) | ThreadsPublisher(실게시)
  → 결과(게시물 ID·시각·오류)를 게시 이력에 기록
```

- **대기열**: 저장소의 `content/queue/` 아래 JSON 파일(글 1개 = 파일 1개, 본문·예정 시각·댓글 링크·제휴 여부).
  GitHub Actions는 실행 간 SQLite가 유지되지 않으므로, 게시 이력은 파일로 남기고 워크플로가 저장소에 다시 커밋합니다.
  같은 글 중복 게시를 막기 위해 워크플로는 `concurrency`로 한 번에 하나만 실행합니다.
- **Publisher 인터페이스**: 기존 `TrendSource`/`PostSource`처럼 교체 가능하게 두고, 기본값은 실제 호출 없는 dry-run입니다.
  실게시는 `--publish live` 같은 명시 옵션과 Secrets가 모두 있을 때만 동작합니다.
- **안전장치**
  - 하루 게시 수 상한과 게시 간 최소 간격 (예: 하루 3~5개, 실제 API 한도보다 훨씬 낮게)
  - 같은 본문·최근 글과 거의 같은 글 차단
  - 길이 초과 시 게시하지 않고 실패 기록 (잘라서 올리지 않음)
  - 제휴 글은 대가성 문구가 없으면 게시 거부 (예: "이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다.")
  - 긴급 정지 스위치: 저장소 변수 하나로 전체 게시 중단
  - 게시 전 `threads_publishing_limit` 조회, 한도 근접 시 건너뜀
- **실패 처리**: 일시적 오류만 제한 횟수 재시도, 인증 오류(토큰 만료)는 재시도 없이 실패 기록과 알림
- **토큰 갱신**: 만료 전에 주기적으로 갱신하는 별도 워크플로가 필요합니다.
  갱신된 토큰을 GitHub Secret에 다시 저장하려면 기본 `GITHUB_TOKEN`이 아닌 별도 권한 토큰이 필요하므로,
  **저장 위치(GitHub Secret 자동 갱신 vs 외부 비밀 저장소 vs 수동 갱신 알림)는 결정이 필요합니다.**

### D. 검증 순서

1. dry-run 단위 테스트: 대기열 선택, 안전장치, 중복 방지, 이력 기록, 실패 처리 (네트워크 없이)
2. 로컬에서 실제 토큰으로 **글 1개 수동 게시** → 응답 필드와 오류 형식을 `phase-0.md`에 기록
3. 댓글(링크) 게시, 한도 조회, 토큰 갱신을 각각 1회 실호출로 확인
4. GitHub Actions 수동 실행(`workflow_dispatch`)으로 1회 게시
5. 확인이 끝난 뒤에만 cron 예약을 켭니다. GitHub Actions cron은 몇 분에서 수십 분 늦게 실행될 수 있습니다.

클라우드 스케줄러가 맞지 않으면, 같은 CLI를 켜둔 PC의 작업 스케줄러(cron/작업 스케줄러)로 실행할 수 있습니다.

## 트렌드 수집기 후속 과제

1. [API 확인 항목](docs/phase-0.md)에 실제 승인 권한과 제공 필드를 기록합니다.
2. 허용된 외부 트렌드 소스와 Threads 검색 수집기를 데이터 계약에 맞춰 구현합니다.
3. 지표 가용성에 맞춰 비교 가능한 랭킹 기준을 정하고 실제 후보 데이터로 검증합니다.
4. DB 외부 영속 저장·복구·백업을 붙인 뒤 매일 예약 실행을 켭니다.
5. 재시도, 인증 만료 감지, 누락일 점검과 선택적 알림을 붙입니다.

GitHub Actions 아티팩트는 데모 결과 확인용입니다. 다음 실행으로 DB를 복원하지 않으므로
현재 워크플로는 누적 수집 운영이나 장기 백업을 제공하지 않습니다.
`Test` 워크플로는 push/PR에서 실행되고, `Manual demo report`는 GitHub Actions에서
수동 실행할 수 있습니다. 원격 실행 결과는 해당 저장소의 Actions 탭에서 확인하세요.

새 Codex 작업은 이 저장소와 개발 환경을 선택하고 `AGENTS.md`를 읽어 시작합니다.
현재 진행 상태와 다음 단계는 [개발 현황](docs/status.md)에 기록합니다.

설계의 결정 사항과 제한은 [MVP 설계](docs/architecture.md)를 참고하세요.
