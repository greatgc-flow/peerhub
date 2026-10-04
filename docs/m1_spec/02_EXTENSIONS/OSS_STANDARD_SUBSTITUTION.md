# 기존 기능의 표준/OSS 대체 우선 검토

## 유지/재사용 가치
- Python stdlib: `sqlite3`, `dataclasses`, `argparse`, `pathlib`, `logging`, `subprocess`, `tomllib`.
- SQLite WAL/transaction/UoW patterns.
- `psutil`/Windows process identity-tree handling이 실제 필요한 구간.
- Git/Git worktree.
- GitHub Actions/PyPI trusted publishing 같은 배포 플랫폼.

## 기본 미도입
- Alembic: 현재 simple ordered SQL migrations가 충분하면 추가하지 않음.
- Pluggy: real third-party plugin ecosystem이 생기기 전에는 explicit composition.
- Kafka/Redis: M1 local durable communication에는 과도함.
- Celery/Temporal/LangGraph: explicit workflow engine 요구가 생기기 전에는 미도입.
- Rich/Textual: UI 가치가 검증되면 optional; Core dependency로 만들지 않음.

## 외부 표준
JSON Schema/Agent Skills는 기본 활용.
MCP/A2A/OTel/OpenAPI/AsyncAPI/CloudEvents는 실제 경계에서만.
