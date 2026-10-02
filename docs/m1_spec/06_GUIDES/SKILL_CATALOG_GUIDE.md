# Skill / Catalog 운용 가이드

## Skill = HOW
작업 절차, 검증 순서, 반복 workflow.

## Catalog = WHAT
현재 모델/CLI/capability/quota pool 등 변동 사실.

## JSON Schema = SHAPE
Catalog와 extension manifest를 기계 검증.

## One Source Multi Use
canonical Skill/Catalog 하나를 유지하고 CLI별 설치 위치에는 생성/동기화한 projection만 둡니다.
Windows 환경에서는 symlink보다 deterministic copy/materialization이 안전한 기본값입니다.

## Global instruction
항상 적용해야 하는 극소수 안정 제약만 프로젝트 전역 파일에 둡니다.
