# Threads 트렌드 수집·글쓰기 도구

우선 흐름은 **수집 결과 JSON → 짧은 한국어 글 선별 → Markdown → Codex 글쓰기 → 초안 Markdown**입니다.
사용자가 선택한 기본 방식은 파일 가져오기와 Codex 글쓰기이며 Meta 앱이나 AI API 키 없이 쓸 수 있습니다.
실제 수집 파일의 품질은 아직 검증하지 않았습니다. 자동 발행은 후속 범위입니다.

기본 조건: 최근 48시간, 공백 포함 200자 이하, 미디어 없는 한국어 글, 가중 반응 100 이상.
지역 필드가 없으면 **한국어권 대리지표**로 표시합니다. 한국 작성 위치를 확인한 것은 아닙니다.
주제를 제한하지 않으며 총 반응과 글 길이를 함께 평가합니다. 전체 Threads 순위는 보장하지 않습니다.

네트워크·설치·키 없이 실행하는 예시:

```bash
PYTHONPATH=src python3 -m threads_bot collect --source demo
PYTHONPATH=src python3 -m threads_bot write --reference reports/reference-demo/2026-10-13.md
```

두 번째 명령은 Codex용 작성 요청을 저장합니다. Codex가 작성한 JSON은 `write --drafts-input`으로 검증·저장합니다.
실제 파일 가져오기와 전체 사용법은 [수집·글쓰기 안내](docs/reference-workflow.md)를 참고하세요.
선택적 Apify 수집 및 AI API 연결 코드도 있으며 실연결은 미검증입니다.
기존 Python·SQLite 샘플 흐름은 아래 명령으로 계속 사용할 수 있습니다.

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

## 기존 SQLite 흐름의 후속 구현

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
