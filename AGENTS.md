# 프로젝트 작업 안내

## 목표와 현재 범위

Threads 트렌드 후보를 수집·분석하고 일일 인기 TOP 10과 시간 보정 TOP 10을 저장합니다.
현재는 합성 데이터 기반 Python·SQLite MVP이며 실제 API 연동은 아직 없습니다.
AI 글 생성과 자동 게시는 현재 개발 범위 밖입니다.

작업 전 `README.md`, `docs/status.md`, `docs/architecture.md`를 읽고,
실제 데이터 연동 작업은 `docs/phase-0.md`의 미확인 항목부터 확인하세요.
API 권한이나 필드 제공 범위를 추측해 구현하거나 검증 완료로 보고하지 마세요.

## 개발과 검증

기존 체크아웃을 사용하세요. 각 클라우드 작업은 이미 격리되어 있으므로 사용자가 요청하지 않는 한
추가 Git worktree를 만들지 마세요.

저장소 루트에서:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m threads_bot --source demo --at 2026-10-10T09:00:00+09:00
```

기준 데모 결과는 후보 25개, 고유 게시물 24개, 각 순위 10개입니다.
데모와 실데이터는 별도 DB·보고서 경로를 사용하세요.
지표가 없으면 `None`/SQL `NULL`과 사유를 유지하고 0을 추측하지 마세요.
비밀 값은 코드·로그·보고서·커밋에 넣지 마세요.
저장된 SQLite와 보고서는 Git에서 제외됩니다.

## 이어서 작업하기

완료한 기능, 검증 결과, 다음 과제를 `docs/status.md`에 반영하세요.
작업 공간 수정, Git 커밋, GitHub push, 클라우드 환경 Publish는 각각 별도 상태입니다.
실제로 확인한 상태만 보고하세요. GitHub 변경은 사용자 요청 범위에서 커밋·push합니다.
