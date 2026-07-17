import { createFileRoute, notFound } from '@tanstack/react-router';
import { useState } from 'react';
import { LANGUAGE_LABELS, CATEGORY_DESCRIPTIONS, findCategory } from '../data';
import type { RuleEntry } from '../data';

export const Route = createFileRoute('/category/$slug')({
  loader: ({ params }) => {
    const category = findCategory(params.slug);
    if (!category) throw notFound();
    return { category };
  },
  head: ({ params }) => ({
    meta: [{ title: `${params.slug} — acode 컨벤션 룰` }],
  }),
  component: CategoryPage,
});

function CategoryPage() {
  const { category } = Route.useLoaderData();
  const [lang, setLang] = useState<string | null>(null);

  const visible = category.entries.filter(
    (e) => lang === null || e.language === lang,
  );
  const rules = visible.filter((e) => e.kind === 'rule');
  const patterns = visible.filter((e) => e.kind === 'pattern');

  return (
    <main className="container">
      <div className="category-header">
        <h1>{category.slug}</h1>
        <p>{CATEGORY_DESCRIPTIONS[category.slug] ?? '컨벤션 룰 모음.'}</p>
        {category.languages.length > 1 && (
          <div className="filter-row">
            <button
              type="button"
              className={`filter-btn ${lang === null ? 'on' : ''}`}
              onClick={() => setLang(null)}
            >
              전체 ({category.count})
            </button>
            {category.languages.map((l) => (
              <button
                key={l}
                type="button"
                className={`filter-btn ${lang === l ? 'on' : ''}`}
                onClick={() => setLang(l)}
              >
                {LANGUAGE_LABELS[l] ?? l} (
                {category.entries.filter((e) => e.language === l).length})
              </button>
            ))}
          </div>
        )}
      </div>

      {rules.length > 0 && (
        <>
          <div className="section-title">기계 검사 룰</div>
          {rules.map((entry) => (
            <RuleCard key={entry.id} entry={entry} />
          ))}
        </>
      )}

      {patterns.length > 0 && (
        <>
          <div className="section-title">권장 패턴</div>
          {patterns.map((entry) => (
            <RuleCard key={entry.id} entry={entry} />
          ))}
        </>
      )}

      {visible.length === 0 && (
        <p className="empty-note">선택한 언어에는 항목이 없습니다.</p>
      )}
    </main>
  );
}

function RuleCard({ entry }: { entry: RuleEntry }) {
  const isRule = entry.kind === 'rule';
  return (
    <article className="rule-card" id={entry.id}>
      <div className="rule-card-head">
        <span className="rule-id">{entry.id}</span>
        <h2>{entry.title}</h2>
        <div className="badge-row">
          <span className={`badge kind-${entry.kind}`}>
            {isRule ? `룰 · ${entry.rule_type}` : '패턴'}
          </span>
          <span className="badge lang">
            {LANGUAGE_LABELS[entry.language] ?? entry.language}
          </span>
          {entry.tags.map((t) => (
            <span key={t} className="badge">
              {t}
            </span>
          ))}
        </div>
      </div>
      <div className="guideline">{entry.guideline}</div>
      {!isRule && (
        <div className="pattern-note">
          권장 패턴 — 기계 검사 룰 없이 검색/제안 컨텍스트로 쓰입니다.
        </div>
      )}
      <div className="example-grid">
        {entry.bad_example && (
          <div className="example-col">
            <div className="example-label bad">✗ 이 코드를 넣으면</div>
            <pre className="code bad-border">
              <code>{entry.bad_example}</code>
            </pre>
            {isRule && (
              <div className="engine-output">
                <div className="engine-output-title">엔진이 이렇게 제안</div>
                {entry.violations.map((v, i) => (
                  <p className="engine-violation" key={i}>
                    <span className="loc">
                      L{v.start_line}:{v.start_col}
                    </span>{' '}
                    {v.message}
                  </p>
                ))}
              </div>
            )}
          </div>
        )}
        {entry.good_example && (
          <div className="example-col">
            <div className="example-label good">
              ✓ {entry.bad_example ? '이렇게 작성' : '권장 형태'}
            </div>
            <pre className="code good-border">
              <code>{entry.good_example}</code>
            </pre>
            {isRule && entry.bad_example && (
              <div className="engine-clean">✓ 엔진 검사 통과 — 위반 0건</div>
            )}
          </div>
        )}
      </div>
    </article>
  );
}
