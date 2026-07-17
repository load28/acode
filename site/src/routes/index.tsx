import { Link, createFileRoute } from '@tanstack/react-router';
import { CATEGORY_DESCRIPTIONS, LANGUAGE_LABELS, categories } from '../data';

export const Route = createFileRoute('/')({
  component: Home,
});

function Home() {
  const totalRules = categories.reduce(
    (n, c) => n + c.entries.filter((e) => e.kind === 'rule').length,
    0,
  );
  const totalPatterns = categories.reduce(
    (n, c) => n + c.entries.filter((e) => e.kind === 'pattern').length,
    0,
  );

  return (
    <main className="container">
      <div className="hero">
        <h1>acode 컨벤션 룰</h1>
        <p>
          결정적 AST 룰 엔진이 기계적으로 검사하는 룰 {totalRules}개와 권장
          패턴 {totalPatterns}개의 공식 문서입니다. 각 룰 페이지에서{' '}
          <strong>어떤 코드를 넣으면 엔진이 무엇을 제안하는지</strong>를 실제
          엔진 실행 결과로 확인할 수 있습니다. 카테고리 하나가 페이지 하나입니다.
        </p>
      </div>
      <div className="category-grid">
        {categories.map((c) => {
          const rules = c.entries.filter((e) => e.kind === 'rule').length;
          const patterns = c.count - rules;
          return (
            <Link
              key={c.slug}
              to="/category/$slug"
              params={{ slug: c.slug }}
              className="category-card"
            >
              <h2>{c.slug}</h2>
              <p>{CATEGORY_DESCRIPTIONS[c.slug] ?? '컨벤션 룰 모음.'}</p>
              <div className="badge-row">
                {rules > 0 && <span className="badge kind-rule">룰 {rules}</span>}
                {patterns > 0 && (
                  <span className="badge kind-pattern">패턴 {patterns}</span>
                )}
                {c.languages.map((l) => (
                  <span key={l} className="badge lang">
                    {LANGUAGE_LABELS[l] ?? l}
                  </span>
                ))}
              </div>
            </Link>
          );
        })}
      </div>
    </main>
  );
}
