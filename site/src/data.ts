import rulesData from './data/rules.json';

export interface Violation {
  rule_id: string;
  message: string;
  severity: string;
  start_line: number;
  start_col: number;
  end_line: number;
  end_col: number;
  snippet: string;
}

export interface RuleEntry {
  id: string;
  kind: 'rule' | 'pattern';
  language: string;
  title: string;
  guideline: string;
  tags: string[];
  good_example: string | null;
  bad_example: string | null;
  rule_type: string | null;
  engine_message: string | null;
  violations: Violation[];
}

export interface Category {
  slug: string;
  count: number;
  languages: string[];
  entries: RuleEntry[];
}

const data = rulesData as { categories: Category[] };

export const categories: Category[] = data.categories;

export function findCategory(slug: string): Category | undefined {
  return categories.find((c) => c.slug === slug);
}

export const CATEGORY_DESCRIPTIONS: Record<string, string> = {
  types: '타입 시스템을 최대로 활용하는 규칙 — any 금지, enum 대체, 판별 유니온, as const 추론.',
  naming: '식별자 이름 규칙 — 함수·타입·클래스의 케이스 컨벤션.',
  correctness: '버그를 원천 차단하는 규칙 — var 금지, 엄격 비교, 가변 기본값 금지.',
  logging: '로깅 규칙 — console/print 대신 공용 로거를 사용.',
  security: '보안 규칙 — eval, dangerouslySetInnerHTML 등 위험한 API 금지.',
  errors: '오류 처리 패턴 — 예외 대신 Result 유니온.',
  api: 'API 라우트 작성 패턴 — Express/FastAPI 핸들러 형태.',
  docs: '문서화 규칙 — 공개 함수의 docstring.',
  styling: '스타일링 규칙 — 인라인 스타일 금지.',
  testing: '테스트 작성 패턴 — pytest 스타일.',
  modeling: '데이터 모델링 패턴 — dataclass 모델.',
};

export const LANGUAGE_LABELS: Record<string, string> = {
  typescript: 'TypeScript',
  tsx: 'TSX (React)',
  javascript: 'JavaScript',
  python: 'Python',
};
