# 수집 결과 JSON → Markdown → Codex 글쓰기

## 선택한 기본 방식

수집 서비스에서 JSON을 내보내고 Codex에 가져옵니다. 개발자 계정과 API 키 없이 쓸 수 있습니다.
파일 경로 자체가 Threads를 크롤링하지는 않습니다. 실제 수집은 별도 서비스에서 진행합니다.
현재 지원하는 파일 계약은 [WebData Labs Threads Scraper](https://apify.com/webdata_labs/threads-scraper)의 JSON dataset입니다.
다른 서비스의 파일은 필드 매핑을 추가해야 합니다. 실제 수집 성공과 한국어 품질은 미검증입니다.

주제를 좁히지 않으려면 당일 한국 트렌드와 `요즘, 사람, 진짜, 생각, 오늘, 인생, 이유, 나만` 같은 일상 검색어를 섞으세요.
이 Actor의 설명상 검색은 키워드당 약 20개이고 페이지네이션이 없어 후보 범위에 편향이 남습니다.
전체 Threads 인기 순위를 얻는 방식은 아닙니다.

저장소 루트에서 실행합니다. 입력 파일은 Git 제외 경로인 `data/`에 두세요.

```bash
# 합성 예시
PYTHONPATH=src python3 -m threads_bot collect --source demo

# 실제 파일
PYTHONPATH=src python3 -m threads_bot collect --source json --input data/threads-export.json

# 실제로 생성된 날짜의 경로 사용
PYTHONPATH=src python3 -m threads_bot write --reference reports/reference-live/YYYY-MM-DD.md
```

다음처럼 Codex에 요청합니다:

> 레퍼런스 `.md`와 `.drafts.prompt.md`를 읽고 새로운 글 3개를 작성해줘.
> JSON을 data/codex-drafts.json에 저장하고 write --drafts-input으로 검증해 Markdown으로 저장해줘.

```bash
PYTHONPATH=src python3 -m threads_bot write \
  --reference reports/reference-live/YYYY-MM-DD.md --drafts-input data/codex-drafts.json
```

결과: 레퍼런스 `.md`/`.json`, 작성 요청 `.drafts.prompt.md`, 검토용 초안 `.drafts.md`/`.drafts.json`.
후킹 점수 내림차순으로 정렬합니다. 점수는 편집상 추정치이며 반응 확률이 아닙니다.
초안은 원문 완전 복사·길이·잘못된 참고 ID·링크·중복을 검사하지만 모든 유사 표현과 사실을 검증하지는 않습니다.
본문에는 점수·분석 내용을 넣지 않고 메타데이터로 분리합니다. 발행은 하지 않습니다.

## 입력 형식과 기준

파일은 JSON 배열입니다. 다음은 합성 예시입니다.

```json
[{"type":"post","postId":"fictional-id","text":"짧은 한국어 예시입니다.",
  "username":"fictional-author","url":null,"date":"2026-10-13T08:00:00+09:00",
  "scrapedAt":"2026-10-13T09:00:00+09:00","media":[],
  "likeCount":500,"replyCount":30,"repostCount":10,"quoteCount":5}]
```

- `postId`, `text` 필수. 날짜는 시간대가 있는 ISO 시각. `date`가 없거나 미래/기간 밖이면 제외.
- `scrapedAt`이 없으면 가져오기 시각으로 기록. 같은 ID는 최신 관측의 전체 필드를 사용.
- `media: []`, `mediaCount: 0`, `mediaType: TEXT_POST` 등 무미디어 근거가 필요.
  이미지·영상·캐러셀·미디어 여부 불명은 제외. 미디어 파일은 다운로드하지 않음.
- 기본 한국어 4글자 이상, 문자 중 한글 비율 35% 이상을 한국어권 대리지표로 사용.
  공급원이 제공한 `countryCode`가 `KR`이 아니면 제외. `--strict-region`은 확인된 `KR`만 선택.
  해당 필드가 없는 데이터에는 strict 모드를 적용하면 모두 제외될 수 있음.
- 미제공 반응 수는 null/생략으로 유지. 실제 관측 0과 구분.
  기본 네 가지 지표 모두 필요. 일부만 제공되면 모든 후보에 동일한 `--metric-basis likes,replies` 등을 적용.

기본 최근 48시간, 공백 포함 200자 이하, 가중 반응 100 이상, 상위 20개.
`--hours`, `--max-chars`, `--min-engagement`, `--limit`으로 조정합니다.

`E = likes + 3×replies + 4×reposts + 3×quotes`

`score = E / sqrt(max(chars, 40) / 40)`

긴 글에 완만한 길이 페널티를 적용하며 극단적으로 짧은 글의 과대평가를 줄입니다.
임의의 초기 기준이며 팔로워·노출량·반응 진위를 보정하지 않습니다.
시간 보정 점수와 100자당 반응도 저장하되 기본 정렬에는 사용하지 않습니다.
조건 충족 글이 없으면 `.rejected.json`에 제외 집계를 남기고 이전 성공 레퍼런스를 보존합니다.
같은 날짜의 성공 재실행은 파일을 교체합니다. 파일 두 개 전체의 원자적 저장은 보장하지 않으며 동시 실행은 피하세요.

## 선택적 서비스 연결

기본 파일+Codex 경로에는 필요하지 않습니다. 향후 자동화를 위한 미검증 연결 코드입니다.
환경 비밀 설정에 `APIFY_TOKEN`과 `OPENAI_API_KEY`를 넣고 네트워크 허용 목록에
`api.apify.com`, `trends.google.com`, `api.openai.com`을 추가해야 합니다.

```bash
# 한국 Google Trends RSS 12개 + 일상 검색어 8개를 자동 발굴
PYTHONPATH=src python3 -m threads_bot collect --source apify --max-charge 1.0

# Responses API JSON 출력을 지원하는 사용 가능 모델 지정
PYTHONPATH=src python3 -m threads_bot write \
  --reference reports/reference-live/YYYY-MM-DD.md --model YOUR_AVAILABLE_MODEL
```

요청당 Apify 지출 상한을 설정하고 유료 POST는 자동 재시도하지 않습니다.
연결이 끊기면 서비스 콘솔에서 상태를 확인하세요. 결과는 `data/live`에도 보존합니다.
RSS는 외부 관심 신호이며 Threads 인기 자체가 아닙니다. 직접 검색어는 `--query`를 반복해 지정합니다.
AI 요청 미완료/검증 실패 시 이전 초안은 보존합니다. 작성 요청만 생성되면 아직 초안은 없는 상태입니다.

검토한 계약: [Apify 동기 실행](https://docs.apify.com/api/v2/actor-run-sync-get-dataset-items-post),
[Google Trends 내보내기](https://support.google.com/trends/answer/3076011?hl=en),
[Responses JSON 출력](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses).
