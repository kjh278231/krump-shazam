# 작업 규칙

## 프로젝트 개요

- 사용 목적, 아키텍처, 검색 단계별 기술 배경, 데이터, 문서 지도는 `docs/overview.md`에 정리돼 있다.
  처음 작업할 때 먼저 읽는다.
- 세부 문서는 구성 요소별로 나뉘어 있다. 바뀐 곳의 문서를 같이 고친다.
  - 검색 단계, 판정 기준과 보정, Panako 패치, 서버 속도: `docs/search-pipeline.md`
  - 확장 프로그램 동작과 화면, 동의, 웹스토어 배포: `docs/extension.md`
  - 평가 세트·도구와 측정 결과: `docs/evaluation.md`
  - 설치, 인덱스 빌드, 실행: `README.md`
  - 배포 구성과 절차: `docs/lambda-deployment.md`
- 구조나 검색 단계가 바뀌면 `docs/overview.md`도 같이 고친다.

## 배포 (AWS Lambda)

- 배포는 **origin에 push된 main 브랜치만 기준으로** 한다. 예외는 없다.
  - 메인 체크아웃에서, `main`이 `origin/main`과 같고 추적 파일에 미커밋 변경이 없을 때만 배포한다.
  - 워크트리, 다른 브랜치, 미커밋 변경, push하지 않은 커밋으로는 배포하지 않는다.
- 순서: 커밋 → main에 합치기 → `git push origin main` → `python server/deploy/stage.py` →
  `python server/deploy/aws_setup.py data image function` (코드만 바뀌었으면 `image function`).
- 스크립트가 강제한다(`server/deploy/release.py`).
  - `stage.py`는 위 조건이 아니면 멈추고, 묶은 커밋을 `build/commit.txt`에 남긴다.
  - `aws_setup.py`의 `data`·`image`·`function`은 같은 조건에 더해 `build/`가 그 커밋으로 만들어졌는지 확인한다.
  - 이미지 태그 끝에 커밋 해시가 붙는다. `function`은 태그가 현재 커밋과 같을 때만 함수를 바꾼다.
  - `warm`(웜 유지 스케줄)도 push된 main에서만 돈다.
  - 확인을 건너뛰는 옵션은 두지 않는다.
- 다른 세션이 main에 미커밋 작업을 남겨 두었으면, 그 작업이 커밋·push된 뒤 그 위에 합쳐 배포한다.
  미커밋 상태를 그대로 배포하면 다음 배포가 그 변경을 되돌린다.
